"""
Prompt Architect: turns a short, vague user prompt into a structured
*simulation brief* that gives every later stage full context (what exactly is
proposed, who proposes it, who is affected and how, which stakeholders will
speak, what to search for, which hypotheses to test).

Works with any LLM; falls back to a transparent heuristic when no model is
available so the pipeline never blocks.
"""

from __future__ import annotations

import logging
import re

from django.utils import timezone

from apps.core.geo import ARCHETYPES, get_country
from apps.core.geo.countries import CountryProfile
from apps.core.llm import LLMError, as_float, as_str, as_str_list, get_llm
from apps.core.text.textutils import camel_tag, keywords, tokenize

logger = logging.getLogger("baraza.brief")

PROPOSERS = ["government", "legislature", "opposition", "private_sector", "civil_society", "international", "other"]

BRIEF_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "refined_question": {"type": "string"},
        "summary": {"type": "string"},
        "policy_details": {"type": "array", "items": {"type": "string"}},
        "proposer": {"type": "string", "enum": PROPOSERS},
        "proposer_name": {"type": "string"},
        "status": {"type": "string"},
        "stakeholders": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "type": {"type": "string"},
                    "likely_position": {"type": "number"},
                    "influence": {"type": "number"},
                },
                "required": ["name", "type", "likely_position"],
            },
        },
        "affected_groups": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "group": {"type": "string"},
                    "impact": {"type": "string"},
                    "direction": {"type": "number"},
                    "intensity": {"type": "number"},
                },
                "required": ["group", "impact", "direction"],
            },
        },
        "key_issues": {"type": "array", "items": {"type": "string"}},
        "search_queries": {"type": "array", "items": {"type": "string"}},
        "local_queries": {"type": "array", "items": {"type": "string"}},
        "social_keywords": {"type": "array", "items": {"type": "string"}},
        "hashtags_support": {"type": "array", "items": {"type": "string"}},
        "hashtags_oppose": {"type": "array", "items": {"type": "string"}},
        "hypotheses": {"type": "array", "items": {"type": "string"}},
        "time_horizon": {"type": "string"},
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "risks_to_watch": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "refined_question", "summary", "stakeholders", "affected_groups", "search_queries"],
}

SYSTEM_PROMPT = """You are a senior public-policy research analyst in {country}. You turn short, vague requests into precise research briefs for a public-sentiment simulator that rehearses how citizens will react online to a policy, action or reform.
You are neutral and evidence-minded. You never invent specific numbers, dates or names you are not sure about; the research step will verify facts later. You reply with JSON only."""

USER_PROMPT = """TODAY: {today}
COUNTRY CONTEXT: {context}
{regions}{title_hint}USER REQUEST:
\"\"\"{prompt}\"\"\"

Rewrite the request into a simulation brief. Rules:
- Stay faithful to the user's intent and scope.
- "title": short name of the policy/action (max 8 words).
- "refined_question": one clear, neutral question naming the policy/action, the population and a time frame.
- "summary": 2-4 sentences on what is proposed, by whom and why it matters to ordinary people.
- "policy_details": the concrete measures being proposed (as stated or clearly implied).
- "proposer": one of {proposers}. "proposer_name": who announced or proposed it, if known.
- "stakeholders": 4-8 institutions or organised groups that will speak publicly (government bodies, opposition, unions, business associations, civil society, religious bodies, media). "likely_position" from -1 (strongly against) to 1 (strongly for); "influence" 0-1.
- "affected_groups": 5-10 segments of ordinary citizens (e.g. boda boda riders, market vendors, salaried workers, students, farmers, small businesses, parents) with HOW they are affected. "direction" from -1 (harmed, likely opposed) to 1 (benefits, likely supportive); "intensity" 0-1 (how strongly they will feel it).
- "key_issues": the 3-6 contested questions people will argue about.
- "search_queries": 6-10 web search queries in English to find news, analysis and public reactions (include the country name).
- "local_queries": {local_rule}
- "social_keywords": 4-8 short phrases or hashtags people would type when posting about this.
- "hashtags_support" and "hashtags_oppose": plausible hashtags for each side.
- "hypotheses": 3-5 testable expectations about how opinion might evolve.
- "time_horizon": the period to simulate (e.g. "first two weeks after the announcement").
- "assumptions" and "risks_to_watch": short lists.

Return ONLY a JSON object with exactly these keys: title, refined_question, summary, policy_details, proposer, proposer_name, status, stakeholders, affected_groups, key_issues, search_queries, local_queries, social_keywords, hashtags_support, hashtags_oppose, hypotheses, time_horizon, assumptions, risks_to_watch."""

BURDEN_WORDS = {
    "tax", "taxes", "taxation", "levy", "levies", "vat", "duty", "excise", "fee", "fees", "charge", "charges",
    "increase", "increases", "hike", "hikes", "raise", "raises", "deduction", "deductions", "contribution",
    "contributions", "penalty", "penalties", "ban", "bans", "remove", "removal", "scrap", "cut", "cuts",
    "austerity", "price", "prices", "tariff", "tariffs", "fine", "fines", "mandatory", "compulsory", "eviction",
}
BENEFIT_WORDS = {
    "subsidy", "subsidies", "relief", "free", "grant", "grants", "reduce", "reduction", "lower", "exemption",
    "exempt", "support", "funding", "jobs", "scholarship", "scholarships", "waiver", "bonus", "cheaper",
    "affordable", "access", "expand", "expansion", "investment", "rebate", "allowance", "stipend",
}

HARMFUL_PATTERNS = [
    (re.compile(r"\b(incite|incitement)\b.*\b(violence|hatred|attack)", re.I), "incitement to violence or hatred"),
    (re.compile(r"\b(ethnic cleansing|genocide|exterminat)", re.I), "violent or genocidal content"),
    (re.compile(r"\bmake (them|people|voters) hate\b", re.I), "promoting hatred"),
    (re.compile(r"\b(dox|doxx|home address of)\b", re.I), "targeting private individuals"),
]
CAUTION_PATTERNS = [
    (re.compile(r"\b(propaganda|disinformation|fake news campaign|astroturf|bot army|troll farm)\b", re.I), "creating manipulative campaigns"),
    (re.compile(r"\b(manipulate|sway|rig)\b.{0,40}\b(voters|election|public opinion)\b", re.I), "manipulating democratic processes"),
    (re.compile(r"\b(tribe|tribes|tribal|ethnic group)\b", re.I), "ethnic framing (the simulator does not model ethnicity)"),
]


def screen_request(text: str) -> dict:
    """Light-weight safety screen for the request. Returns level ok | caution | blocked."""
    concerns, level = [], "ok"
    for pattern, label in HARMFUL_PATTERNS:
        if pattern.search(text or ""):
            concerns.append(label)
            level = "blocked"
    if level != "blocked":
        for pattern, label in CAUTION_PATTERNS:
            if pattern.search(text or ""):
                concerns.append(label)
                level = "caution"
    return {"level": level, "allowed": level != "blocked", "concerns": concerns}


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


def _norm_hashtag(tag: str) -> str:
    tag = re.sub(r"[^\w]", "", tag.replace("#", ""))
    return f"#{tag}" if tag else ""


def normalize_brief(data: dict, *, prompt: str, country: CountryProfile, generated_by: str) -> dict:
    data = data if isinstance(data, dict) else {}
    title = as_str(data.get("title"), max_len=90) or derive_title(prompt)
    proposer = as_str(data.get("proposer")).lower().replace(" ", "_").replace("-", "_")
    if proposer not in PROPOSERS:
        proposer = detect_proposer(prompt)

    stakeholders = []
    for item in data.get("stakeholders") or []:
        if not isinstance(item, dict):
            item = {"name": as_str(item)}
        name = as_str(item.get("name"), max_len=90)
        if not name:
            continue
        stakeholders.append(
            {
                "name": name,
                "type": as_str(item.get("type"), "organization", max_len=40),
                "likely_position": round(as_float(item.get("likely_position"), 0.0, -1.0, 1.0), 2),
                "influence": round(as_float(item.get("influence"), 0.5, 0.0, 1.0), 2),
            }
        )

    groups = []
    for item in data.get("affected_groups") or []:
        if not isinstance(item, dict):
            item = {"group": as_str(item)}
        group = as_str(item.get("group"), max_len=80)
        if not group:
            continue
        groups.append(
            {
                "group": group,
                "impact": as_str(item.get("impact"), max_len=240),
                "direction": round(as_float(item.get("direction"), 0.0, -1.0, 1.0), 2),
                "intensity": round(as_float(item.get("intensity"), 0.5, 0.0, 1.0), 2),
            }
        )

    queries = as_str_list(data.get("search_queries"), max_items=12, max_len=160)
    if not queries:
        queries = heuristic_queries(prompt, title, country)
    elif not any(country.name.lower() in q.lower() for q in queries) and country.code != "XX":
        queries = [f"{q} {country.name}" if i < 3 else q for i, q in enumerate(queries)]

    social = as_str_list(data.get("social_keywords"), max_items=10, max_len=60) or keywords(prompt, 5)
    tag = camel_tag(title)
    support_tags = [t for t in (_norm_hashtag(h) for h in as_str_list(data.get("hashtags_support"), 5)) if t] or [f"#Support{tag}"]
    oppose_tags = [t for t in (_norm_hashtag(h) for h in as_str_list(data.get("hashtags_oppose"), 5)) if t] or [f"#Reject{tag}"]

    brief = {
        "title": title,
        "refined_question": as_str(data.get("refined_question"), max_len=400)
        or f"How will people in {country.name} react online to {title} over the coming weeks, and which groups will support or oppose it?",
        "summary": as_str(data.get("summary"), max_len=1200) or prompt.strip()[:600],
        "policy_details": as_str_list(data.get("policy_details"), max_items=10, max_len=240),
        "proposer": proposer,
        "proposer_name": as_str(data.get("proposer_name"), max_len=120),
        "status": as_str(data.get("status"), "proposed", max_len=60),
        "stakeholders": stakeholders[:10] or heuristic_stakeholders(prompt, country, proposer),
        "affected_groups": groups[:12] or heuristic_groups(prompt),
        "key_issues": as_str_list(data.get("key_issues"), max_items=8, max_len=200),
        "search_queries": queries[:12],
        "local_queries": as_str_list(data.get("local_queries"), max_items=5, max_len=160) if country.local_queries else [],
        "social_keywords": social,
        "hashtags": {"support": support_tags[:5], "oppose": oppose_tags[:5], "neutral": [f"#{tag}"]},
        "hypotheses": as_str_list(data.get("hypotheses"), max_items=6, max_len=300),
        "time_horizon": as_str(data.get("time_horizon"), "the first two weeks after the announcement", max_len=120),
        "assumptions": as_str_list(data.get("assumptions"), max_items=6, max_len=240),
        "risks_to_watch": as_str_list(data.get("risks_to_watch"), max_items=6, max_len=240),
        "generated_by": generated_by,
        "generated_at": timezone.now().isoformat(),
    }
    return brief


# ---------------------------------------------------------------------------
# Heuristics (used when no language model is available)
# ---------------------------------------------------------------------------


ACTION_VERBS = r"(?:proposes?|introduces?|announces?|plans? to introduce|plans?|raises?|removes?|launches?|imposes?|replaces?|bans?|scraps?|increases?|cuts?|rolls? out|adopts?|approves?|passes?|considers?)"
_STOP = r"(?:that|which|who|to|for|on|by|in|from|of|after|because|so|and|with|across|at|above|below|over|under|starting|effective|\w+ed)"
_END = rf"(?=\s+{_STOP}\b|\s*[,;:?()]|\.\s|\.$|$)"
_OBJECT_RE = re.compile(
    rf"\b{ACTION_VERBS}\s+(?:(?:a|an|the|its|new|that)\s+)?((?:[A-Za-z0-9%'’.-]+\s+){{0,5}}?[A-Za-z0-9%'’.-]+){_END}",
    re.I,
)
# "a 2% levy" + " on mobile money transfers": extend very short objects.
_EXTEND_RE = re.compile(rf"\s+((?:on|of|for)\s+(?:(?:a|an|the|all)\s+)?(?:[A-Za-z0-9%'’.-]+\s+){{0,3}}?[A-Za-z0-9%'’.-]+){_END}", re.I)


def derive_title(prompt: str) -> str:
    text = re.sub(r"\s+", " ", (prompt or "").strip())
    match = _OBJECT_RE.search(text)
    if match:
        candidate = match.group(1).strip(" ,.")
        if len(candidate.split()) <= 2:
            extension = _EXTEND_RE.match(text, match.end(1))
            if extension and len(candidate) + len(extension.group(1)) < 60:
                candidate = f"{candidate} {extension.group(1).strip(' ,.')}"
        if 3 <= len(candidate) <= 60 and len(candidate.split()) <= 8 and candidate.lower().split()[0] not in {"all", "every", "some"}:
            return candidate[:1].upper() + candidate[1:]
    first = re.split(r"(?<=[.?!])\s", text)[0]
    first = re.sub(r"^(simulate|predict|how (will|would)|what (will|would)|analy[sz]e|test)\b[^:]*?(react(ion)? to|about|of|on)\s+", "", first, flags=re.I)
    first = first.strip(" .?!")
    if len(first) > 70:
        kws = keywords(text, 4)
        first = " ".join(w[:1].upper() + w[1:] for w in " ".join(kws[:2]).split()) if kws else first[:70]
    return first[:1].upper() + first[1:] if first else "Untitled policy"


def detect_proposer(prompt: str) -> str:
    text = (prompt or "").lower()
    if re.search(r"\b(opposition|minority party)\b", text):
        return "opposition"
    if re.search(r"\b(parliament|senate|national assembly|mps|bill|legislat)", text):
        return "legislature" if "government" not in text and "treasury" not in text else "government"
    if re.search(r"\b(government|ministry|treasury|president|cabinet|county|authority|state|regulator|commission)\b", text):
        return "government"
    if re.search(r"\b(company|bank|telecom|telco|firm|brand|corporation|safaricom|mtn|airtel|employer|startup)\b", text):
        return "private_sector"
    if re.search(r"\b(ngo|civil society|union|church|activists)\b", text):
        return "civil_society"
    if re.search(r"\b(imf|world bank|un|donor|african union|eac|ecowas)\b", text):
        return "international"
    return "government"


def policy_direction(prompt: str) -> float:
    """Rough sign of a policy's effect on ordinary people: burden (-) vs benefit (+)."""
    tokens = tokenize(prompt, keep_stopwords=True)
    burden = sum(1 for t in tokens if t in BURDEN_WORDS)
    benefit = sum(1 for t in tokens if t in BENEFIT_WORDS)
    if burden == benefit == 0:
        return -0.1
    return max(-0.9, min(0.9, (benefit - burden) / (benefit + burden + 1)))


def heuristic_groups(prompt: str) -> list[dict]:
    tokens = set(tokenize(prompt))
    text = (prompt or "").lower()
    base = policy_direction(prompt)
    scored = []
    for arch in ARCHETYPES:
        overlap = sum(1 for kw in arch.keywords if (" " in kw and kw in text) or kw in tokens)
        scored.append((overlap, arch))
    scored.sort(key=lambda x: (-x[0], -x[1].weight))
    groups = []
    for overlap, arch in scored[:7]:
        direct = overlap > 0
        income_factor = {"low": 1.2, "lower_middle": 1.0, "middle": 0.8, "upper": 0.6}[arch.income]
        direction = base * (1.0 if direct else 0.5) * (income_factor if base < 0 else 1.0)
        groups.append(
            {
                "group": arch.label,
                "impact": f"Directly affected via {', '.join(arch.concerns[:2])}." if direct else f"Indirectly affected; main concerns: {', '.join(arch.concerns[:2])}.",
                "direction": round(max(-1.0, min(1.0, direction)), 2),
                "intensity": 0.75 if direct else 0.4,
            }
        )
    return groups


def heuristic_stakeholders(prompt: str, country: CountryProfile, proposer: str) -> list[dict]:
    base = policy_direction(prompt)
    proposer_pos = 0.8
    stakeholders = [
        {"name": "National government", "type": "government", "likely_position": proposer_pos if proposer == "government" else 0.1, "influence": 0.9},
        {"name": "Parliament", "type": "legislature", "likely_position": 0.4 if proposer in ("government", "legislature") else 0.0, "influence": 0.7},
        {"name": "Opposition parties", "type": "political party", "likely_position": -0.6 if proposer == "government" else 0.3, "influence": 0.6},
        {"name": "Trade unions", "type": "union", "likely_position": round(min(0.3, base) - 0.1, 2), "influence": 0.5},
        {"name": "Business associations", "type": "business", "likely_position": round(base * 0.6, 2), "influence": 0.5},
        {"name": "Civil society organisations", "type": "civil society", "likely_position": round(min(0.2, base) - 0.2, 2), "influence": 0.5},
        {"name": "Religious leaders", "type": "religious", "likely_position": round(base * 0.5, 2), "influence": 0.5},
    ]
    if proposer == "private_sector":
        stakeholders.insert(0, {"name": "The company", "type": "company", "likely_position": 0.8, "influence": 0.8})
        stakeholders.append({"name": "Consumer rights groups", "type": "civil society", "likely_position": round(base, 2), "influence": 0.4})
    return stakeholders


def heuristic_queries(prompt: str, title: str, country: CountryProfile) -> list[str]:
    name = country.name if country.code != "XX" else ""
    kws = keywords(prompt, 6)
    base = title if len(title) < 60 else " ".join(kws[:3])
    queries = [
        f"{base} {name}".strip(),
        f"{base} public reaction {name}".strip(),
        f"{base} criticism {name}".strip(),
        f"{base} support benefits {name}".strip(),
        f"{base} impact on ordinary citizens {name}".strip(),
    ]
    if len(kws) >= 2:
        queries.append(f"{kws[0]} {kws[1]} {name} news".strip())
    if kws:
        queries.append(f"{kws[0]} {name} opinion".strip())
    seen, out = set(), []
    for q in queries:
        if q.lower() not in seen:
            seen.add(q.lower())
            out.append(q)
    return out


def heuristic_brief(prompt: str, country: CountryProfile, title_hint: str = "") -> dict:
    title = title_hint or derive_title(prompt)
    data = {
        "title": title,
        "summary": prompt.strip()[:800],
        "proposer": detect_proposer(prompt),
        "key_issues": [f"Who bears the cost of {title.lower()}?", "Is it fair to low-income households?", "Will the money be used well?"],
        "hypotheses": [
            "Opposition will be strongest among low-income and directly affected groups.",
            "Sentiment will polarise along existing political lines within days.",
            "Clear explanations of benefits will soften (but not reverse) opposition.",
        ],
        "local_queries": [f"{title} maoni ya wananchi", f"{title} wananchi"] if country.local_queries else [],
    }
    return normalize_brief(data, prompt=prompt, country=country, generated_by="heuristic")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def build_brief(project, *, ctx=None) -> dict:
    country = get_country(project.country)
    llm = get_llm(project)
    regions = ""
    if project.focus_regions:
        regions = f"FOCUS {country.region_label.upper()}S: {', '.join(project.focus_regions)}\n"
    from .scenarios import SCENARIOS_BY_KEY

    hint = SCENARIOS_BY_KEY.get(project.template_key, {}).get("title", "") if project.template_key else ""
    if llm.available("smart"):
        if ctx:
            ctx.progress(10, "Restructuring the request", f"Asking {llm.describe()['smart']} to build the brief…")
        try:
            data = llm.json(
                system=SYSTEM_PROMPT.format(country=country.name),
                user=USER_PROMPT.format(
                    today=timezone.localdate().isoformat(),
                    context=country.context,
                    regions=regions,
                    title_hint=f"SUGGESTED TITLE: {hint}\n" if hint else "",
                    prompt=project.prompt.strip()[:3000],
                    proposers=", ".join(PROPOSERS),
                    local_rule="2-4 queries in Swahili that a local would type" if country.local_queries else "[] (empty list)",
                ),
                purpose="brief",
                tier="smart",
                schema=BRIEF_SCHEMA,
                max_tokens=1800,
                temperature=0.2,
            )
            return normalize_brief(data, prompt=project.prompt, country=country, generated_by="llm")
        except LLMError as exc:
            logger.warning("Brief generation failed, using heuristic: %s", exc)
            if ctx:
                ctx.warn(f"The language model failed ({exc}); using the heuristic brief instead.")
    elif ctx:
        ctx.warn("No language model available; building a heuristic brief. Connect Ollama for a much richer brief.")
    return heuristic_brief(project.prompt, country, title_hint=hint)
