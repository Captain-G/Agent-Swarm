"""
Research dossier synthesis (GraphRAG step 3): consolidate arguments, write a
neutral evidence-based dossier, map affected groups onto citizen archetypes.
"""

from __future__ import annotations

import logging
from collections import Counter

from apps.core.geo import ARCHETYPES
from apps.core.llm import LLMError, as_float, as_str, as_str_list
from apps.core.text.retrieval import BM25
from apps.core.text.textutils import jaccard, keywords, sentences, tokenize, top_terms, truncate

logger = logging.getLogger("baraza.knowledge")

DOSSIER_SYSTEM = """You are a policy analyst writing a neutral, evidence-based research dossier for decision-makers in {country}.
Use only the evidence provided; when evidence is thin, say so. Reply with JSON only."""

OVERVIEW_USER = """TOPIC: {title}
QUESTION: {question}
BRIEF: {summary}

EVIDENCE (numbered excerpts from news, documents and public posts):
{evidence}

FACTS ALREADY EXTRACTED:
{facts}

Write:
- "summary": executive summary in 4-6 sentences: what is proposed, by whom, who is for and against, and how the public is reacting so far.
- "background": 1-2 short paragraphs of context.
- "timeline": up to 8 events [{{"date": "YYYY-MM-DD or month", "event": ""}}] taken from the evidence (empty if none).
- "key_facts": up to 10 [{{"fact": "", "sources": [1, 3]}}] citing evidence numbers.
- "narratives": 3-6 dominant public narratives or frames, as short phrases.
- "uncertainties": 3-6 open questions or evidence gaps.

Return ONLY JSON with keys: summary, background, timeline, key_facts, narratives, uncertainties."""

STAKEHOLDER_USER = """TOPIC: {title}
QUESTION: {question}

ACTORS FOUND IN THE EVIDENCE:
{actors}

STAKEHOLDERS EXPECTED IN THE BRIEF:
{brief_stakeholders}

AFFECTED GROUPS FROM THE BRIEF:
{groups}

MAIN ARGUMENTS FOR:
{pro}
MAIN ARGUMENTS AGAINST:
{con}

Write:
- "stakeholders": 5-10 organised actors [{{"name": "", "type": "government|legislature|political_party|business|union|civil_society|religious|media|international|organization", "position": -1..1, "influence": 0..1, "summary": "one sentence on their position"}}].
- "impact_matrix": 6-12 groups of ordinary citizens [{{"group": "", "impact": "how they are affected", "direction": -1..1, "intensity": 0..1}}] (direction -1 = harmed/opposed, 1 = benefits/supportive).
- "rumors": up to 5 rumours or misleading claims likely to circulate (empty list if none).

Return ONLY JSON with keys: stakeholders, impact_matrix, rumors."""

ARGUMENT_SYSTEM = "You are an editor who rewrites public arguments clearly and neutrally without changing their meaning. Reply with JSON only."
ARGUMENT_USER = """TOPIC: {title}

Rewrite each argument below as (a) a crisp one-sentence "claim" and (b) a "short" social media version under 200 characters that an ordinary {demonym} might post. Keep the side and meaning. Do not merge items.

ARGUMENTS:
{items}

Return JSON: {{"arguments": [{{"id": 1, "claim": "", "short": ""}}]}}"""

GENERATE_ARGS_USER = """TOPIC: {title}
SUMMARY: {summary}

The evidence contains few arguments on the "{side}" side. Write the {n} strongest arguments that people who {side_verb} this would realistically make in {country}. Be realistic, not exaggerated.

Return JSON: {{"arguments": [{{"claim": "", "short": "tweet-length version under 200 characters", "by": "who typically makes it", "emotion": "anger|fear|sadness|disgust|joy|hope|trust|neutral", "strength": 0.5}}]}}"""


def consolidate_arguments(raw: list[dict], *, max_per_side: int = 10) -> list[dict]:
    """Cluster near-duplicate arguments and count how often each appears."""
    clusters: list[dict] = []
    for arg in sorted(raw, key=lambda a: -a.get("strength", 0.5)):
        tokens = set(tokenize(arg["claim"]))
        placed = False
        for cluster in clusters:
            if cluster["side"] == arg["side"] and jaccard(tokens, cluster["tokens"]) >= 0.42:
                cluster["members"].append(arg)
                cluster["tokens"] |= tokens
                placed = True
                break
        if not placed:
            clusters.append({"side": arg["side"], "tokens": tokens, "members": [arg]})
    consolidated = []
    for cluster in clusters:
        members = cluster["members"]
        best = max(members, key=lambda a: (a.get("strength", 0.5), len(a["claim"])))
        emotions = Counter(m.get("emotion", "neutral") for m in members)
        consolidated.append(
            {
                "side": cluster["side"],
                "claim": best["claim"],
                "short": best.get("short", ""),
                "rationale": "",
                "strength": round(sum(m.get("strength", 0.5) for m in members) / len(members), 3),
                "frequency": len(members),
                "emotion": emotions.most_common(1)[0][0],
                "groups": sorted({m.get("by", "") for m in members if m.get("by")})[:6],
                "evidence": sorted({m.get("source") for m in members if m.get("source")})[:10],
            }
        )
    out = []
    for side in ("oppose", "support", "neutral"):
        side_args = [a for a in consolidated if a["side"] == side]
        side_args.sort(key=lambda a: (-a["frequency"], -a["strength"]))
        out.extend(side_args[: max_per_side if side != "neutral" else max(3, max_per_side // 2)])
    return out


def polish_arguments(llm, arguments: list[dict], *, title: str, demonym: str) -> None:
    """Add clean claims and tweet-length versions (in place)."""
    if not arguments:
        return
    for start in range(0, len(arguments), 10):
        batch = arguments[start : start + 10]
        items = "\n".join(f"{i + 1}. [{a['side']}] {truncate(a['claim'], 300)}" for i, a in enumerate(batch))
        try:
            data = llm.json(
                system=ARGUMENT_SYSTEM,
                user=ARGUMENT_USER.format(title=title, demonym=demonym, items=items),
                purpose="arguments",
                tier="smart",
                max_tokens=110 * len(batch) + 100,
            )
        except LLMError as exc:
            logger.info("argument polishing failed: %s", exc)
            continue
        for row in data.get("arguments") or []:
            if not isinstance(row, dict):
                continue
            idx = int(as_float(row.get("id"), 0)) - 1
            if 0 <= idx < len(batch):
                claim = as_str(row.get("claim"), max_len=400)
                short = as_str(row.get("short"), max_len=240)
                if claim:
                    batch[idx]["claim"] = claim
                if short:
                    batch[idx]["short"] = short


def generate_missing_arguments(llm, *, side: str, n: int, title: str, summary: str, country: str) -> list[dict]:
    try:
        data = llm.json(
            system=ARGUMENT_SYSTEM,
            user=GENERATE_ARGS_USER.format(
                title=title, summary=truncate(summary, 600), side=side, n=n, country=country,
                side_verb="support" if side == "support" else "oppose",
            ),
            purpose="arguments",
            tier="smart",
            max_tokens=700,
            temperature=0.4,
        )
    except LLMError:
        return []
    out = []
    for row in (data.get("arguments") or [])[:n]:
        if not isinstance(row, dict) or not as_str(row.get("claim")):
            continue
        emotion = as_str(row.get("emotion"), "neutral").lower()
        out.append(
            {
                "side": side,
                "claim": as_str(row.get("claim"), max_len=400),
                "short": as_str(row.get("short"), max_len=240),
                "rationale": "Generated to balance the argument bank (not found in the evidence).",
                "strength": as_float(row.get("strength"), 0.5, 0.0, 1.0),
                "frequency": 1,
                "emotion": emotion if emotion in {"anger", "fear", "sadness", "disgust", "joy", "hope", "trust", "neutral"} else "neutral",
                "groups": [as_str(row.get("by"), max_len=100)] if row.get("by") else [],
                "evidence": [],
            }
        )
    return out


def heuristic_missing_arguments(side: str, brief: dict) -> list[dict]:
    title = brief.get("title", "this policy")
    groups = brief.get("affected_groups") or []
    out = []
    if side == "oppose":
        harmed = [g for g in groups if g.get("direction", 0) < 0][:3] or groups[:2]
        for g in harmed:
            out.append({"claim": f"{title} will hurt {g['group'].lower()}: {g.get('impact') or 'it raises their costs'}", "emotion": "anger"})
        out.append({"claim": f"Ordinary people are already struggling; {title} adds to the cost of living.", "emotion": "anger"})
        out.append({"claim": f"There is no guarantee the money from {title} will be used well.", "emotion": "disgust"})
    elif side == "support":
        helped = [g for g in groups if g.get("direction", 0) > 0][:3]
        for g in helped:
            out.append({"claim": f"{title} will benefit {g['group'].lower()}: {g.get('impact') or 'it improves their situation'}", "emotion": "hope"})
        out.append({"claim": f"{title} is a necessary step to fund public services and reduce borrowing.", "emotion": "trust"})
        out.append({"claim": f"If well implemented, {title} could improve the economy in the long run.", "emotion": "hope"})
    else:
        out.append({"claim": f"We need more details and public participation before deciding on {title}.", "emotion": "neutral"})
    return [
        {"side": side, "claim": a["claim"], "short": truncate(a["claim"], 200), "rationale": "Heuristic placeholder (no evidence found).", "strength": 0.45, "frequency": 1, "emotion": a["emotion"], "groups": [], "evidence": []}
        for a in out
    ]


def map_group_to_archetypes(group: str, impact: str = "") -> list[tuple[str, float]]:
    text = f"{group} {impact}".lower()
    tokens = set(tokenize(text))
    scored = []
    for arch in ARCHETYPES:
        score = 0.0
        for kw in arch.keywords:
            if " " in kw:
                if kw in text:
                    score += 2.0
            elif kw in tokens:
                score += 1.0
        label_tokens = set(tokenize(arch.label)) | set(tokenize(arch.occupation))
        score += 1.5 * len(label_tokens & set(tokenize(group)))
        if score > 0:
            scored.append((arch.key, round(score, 2)))
    scored.sort(key=lambda x: -x[1])
    return scored[:4]


def annotate_impact_matrix(matrix: list[dict]) -> list[dict]:
    for row in matrix:
        row["archetypes"] = [k for k, _ in map_group_to_archetypes(row.get("group", ""), row.get("impact", ""))]
    return matrix


# ---------------------------------------------------------------------------
# Dossier
# ---------------------------------------------------------------------------


def _evidence_block(chunks: list, max_items: int = 12, max_chars: int = 650) -> tuple[str, list]:
    lines, refs = [], []
    for i, chunk in enumerate(chunks[:max_items], start=1):
        source = chunk.source
        label = ""
        if source is not None:
            date = source.published_at.date().isoformat() if source.published_at else ""
            label = f"{source.get_platform_display()}, {source.domain or source.title[:40]}{', ' + date if date else ''}"
        lines.append(f"[{i}] ({label or 'public posts'}) {truncate(chunk.text.replace(chr(10), ' '), max_chars)}")
        refs.append(source.pk if source is not None else None)
    return "\n".join(lines), refs


def normalize_overview(data: dict, refs: list) -> dict:
    timeline = []
    for row in data.get("timeline") or []:
        if isinstance(row, dict) and as_str(row.get("event")):
            timeline.append({"date": as_str(row.get("date"), max_len=40), "event": as_str(row.get("event"), max_len=300)})
    facts = []
    for row in data.get("key_facts") or []:
        if isinstance(row, dict):
            fact = as_str(row.get("fact"), max_len=400)
            source_ids = []
            for n in row.get("sources") or []:
                idx = int(as_float(n, 0)) - 1
                if 0 <= idx < len(refs) and refs[idx]:
                    source_ids.append(refs[idx])
        else:
            fact, source_ids = as_str(row, max_len=400), []
        if fact:
            facts.append({"fact": fact, "sources": source_ids})
    return {
        "summary": as_str(data.get("summary"), max_len=2500),
        "background": as_str(data.get("background"), max_len=4000),
        "timeline": timeline[:10],
        "key_facts": facts[:12],
        "narratives": as_str_list(data.get("narratives"), 8, 160),
        "uncertainties": as_str_list(data.get("uncertainties"), 8, 300),
    }


def normalize_stakeholders(data: dict) -> dict:
    stakeholders = []
    for row in data.get("stakeholders") or []:
        if not isinstance(row, dict) or not as_str(row.get("name")):
            continue
        stakeholders.append(
            {
                "name": as_str(row.get("name"), max_len=120),
                "type": as_str(row.get("type"), "organization", max_len=40).lower(),
                "position": round(as_float(row.get("position"), 0.0, -1.0, 1.0), 2),
                "influence": round(as_float(row.get("influence"), 0.5, 0.0, 1.0), 2),
                "summary": as_str(row.get("summary"), max_len=300),
            }
        )
    matrix = []
    for row in data.get("impact_matrix") or []:
        if not isinstance(row, dict) or not as_str(row.get("group")):
            continue
        matrix.append(
            {
                "group": as_str(row.get("group"), max_len=100),
                "impact": as_str(row.get("impact"), max_len=300),
                "direction": round(as_float(row.get("direction"), 0.0, -1.0, 1.0), 2),
                "intensity": round(as_float(row.get("intensity"), 0.5, 0.0, 1.0), 2),
            }
        )
    return {"stakeholders": stakeholders[:12], "impact_matrix": matrix[:14], "rumors": as_str_list(data.get("rumors"), 6, 300)}


def llm_overview(llm, *, project, brief, chunks, facts, country) -> dict:
    evidence, refs = _evidence_block(chunks)
    fact_lines = "\n".join(f"- {truncate(f['fact'], 220)}" for f in facts[:14]) or "- (none)"
    data = llm.json(
        system=DOSSIER_SYSTEM.format(country=country),
        user=OVERVIEW_USER.format(
            title=brief.get("title", project.title),
            question=brief.get("refined_question", project.prompt),
            summary=truncate(brief.get("summary", ""), 600),
            evidence=evidence or "(no evidence collected yet)",
            facts=fact_lines,
        ),
        purpose="dossier_overview",
        tier="smart",
        max_tokens=1800,
        temperature=0.2,
    )
    return normalize_overview(data, refs)


def llm_stakeholders(llm, *, project, brief, entities, arguments, country) -> dict:
    actors = "\n".join(
        f"- {e.name} ({e.get_type_display()}): {truncate(e.description, 160)}{'' if e.stance is None else f' [stance hint {e.stance:+.1f}]'}"
        for e in entities[:18]
    ) or "- (none)"
    brief_st = "\n".join(f"- {s['name']} ({s['type']}), expected position {s['likely_position']:+.1f}" for s in brief.get("stakeholders", [])) or "- (none)"
    groups = "\n".join(f"- {g['group']}: {g['impact']} (direction {g['direction']:+.1f})" for g in brief.get("affected_groups", [])) or "- (none)"
    pro = "\n".join(f"- {truncate(a['claim'], 160)}" for a in arguments if a["side"] == "support")[:1500] or "- (none)"
    con = "\n".join(f"- {truncate(a['claim'], 160)}" for a in arguments if a["side"] == "oppose")[:1500] or "- (none)"
    data = llm.json(
        system=DOSSIER_SYSTEM.format(country=country),
        user=STAKEHOLDER_USER.format(
            title=brief.get("title", project.title), question=brief.get("refined_question", project.prompt),
            actors=actors, brief_stakeholders=brief_st, groups=groups, pro=pro, con=con,
        ),
        purpose="dossier_stakeholders",
        tier="smart",
        max_tokens=1800,
        temperature=0.2,
    )
    return normalize_stakeholders(data)


def heuristic_overview(*, brief, chunks, facts, sources) -> dict:
    texts = [c.text for c in chunks]
    summary = brief.get("summary", "")
    if texts:
        all_sents = [s for t in texts[:20] for s in sentences(t) if 50 < len(s) < 350]
        if all_sents:
            bm = BM25(all_sents)
            scores = bm.scores(" ".join([brief.get("title", ""), brief.get("refined_question", "")] + list(brief.get("key_issues") or [])))
            ranked = [s for _, s in sorted(zip(scores, all_sents), key=lambda x: -x[0])]
            picked = []
            for sent in ranked:
                if all(jaccard(set(tokenize(sent)), set(tokenize(p))) < 0.5 for p in picked):
                    picked.append(sent)
                if len(picked) >= 4:
                    break
            if picked:
                summary = f"{summary} {' '.join(picked)}".strip()
    dated = sorted([s for s in sources if s.published_at], key=lambda s: s.published_at)[-8:]
    return {
        "summary": truncate(summary, 2500),
        "background": brief.get("summary", ""),
        "timeline": [{"date": s.published_at.date().isoformat(), "event": truncate(s.title, 200)} for s in dated],
        "key_facts": [{"fact": f["fact"], "sources": [f["source"]] if f.get("source") else []} for f in facts[:10]],
        "narratives": [t for t, _ in top_terms([s.body for s in sources if s.is_social], 6)],
        "uncertainties": [f"Unverified hypothesis: {h}" for h in (brief.get("hypotheses") or [])[:4]],
    }


def heuristic_stakeholders(*, brief, entities) -> dict:
    stakeholders = [
        {"name": s["name"], "type": s.get("type", "organization"), "position": s.get("likely_position", 0.0), "influence": s.get("influence", 0.5), "summary": ""}
        for s in brief.get("stakeholders", [])
    ]
    known = {s["name"].lower() for s in stakeholders}
    for e in entities:
        if e.type in {"government", "legislature", "political_party", "union", "business", "civil_society", "religious", "media"} and e.name.lower() not in known:
            stakeholders.append({"name": e.name, "type": e.type, "position": e.stance if e.stance is not None else 0.0, "influence": round(0.3 + 0.5 * e.salience, 2), "summary": truncate(e.description, 200)})
            known.add(e.name.lower())
        if len(stakeholders) >= 10:
            break
    return {"stakeholders": stakeholders, "impact_matrix": [dict(g) for g in brief.get("affected_groups", [])], "rumors": []}


def community_title(names: list[str]) -> str:
    return ", ".join(names[:3]) + (" and others" if len(names) > 3 else "")


def summarize_community(llm, *, title: str, members: list, relations: list[str]) -> tuple[str, str]:
    member_lines = "\n".join(f"- {m.name} ({m.get_type_display()}): {truncate(m.description, 120)}" for m in members[:12])
    rel_lines = "\n".join(f"- {r}" for r in relations[:12]) or "- (none)"
    try:
        data = llm.json(
            system="You summarise clusters of actors in a knowledge graph about public debate. Reply with JSON only.",
            user=(
                f"TOPIC: {title}\nACTORS IN THIS CLUSTER:\n{member_lines}\nRELATIONSHIPS:\n{rel_lines}\n\n"
                'Return JSON: {"title": "max 6 words naming what unites this cluster", "summary": "2 sentences on who they are and how they relate to the topic"}'
            ),
            purpose="community_summary",
            tier="fast",
            max_tokens=220,
        )
        return as_str(data.get("title"), max_len=80), as_str(data.get("summary"), max_len=500)
    except LLMError:
        return community_title([m.name for m in members]), ""


def social_signals(sources: list) -> dict:
    social = [s for s in sources if s.is_social]
    by_platform: dict[str, list[float]] = {}
    for s in social:
        if s.sentiment is not None:
            by_platform.setdefault(s.get_platform_display(), []).append(s.sentiment)
    return {
        "platforms": {p: {"n": len(v), "mean_sentiment": round(sum(v) / len(v), 3)} for p, v in by_platform.items()},
        "top_terms": [t for t, _ in top_terms([s.body for s in social], 15)],
        "n_social": len(social),
        "keywords": keywords(" ".join(s.body for s in social[:200]), 10),
    }
