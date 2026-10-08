"""
Knowledge build job: chunk evidence -> extract (GraphRAG) -> resolve entities
-> graph + communities -> argument bank -> research dossier.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

from django.db import close_old_connections, connection, transaction

from apps.core.config import get_config
from apps.core.geo import get_country
from apps.core.llm import get_llm
from apps.core.text.retrieval import BM25, to_bytes
from apps.core.text.textutils import chunk_text, truncate

from .dossier import (
    annotate_impact_matrix,
    community_title,
    consolidate_arguments,
    generate_missing_arguments,
    heuristic_missing_arguments,
    heuristic_overview,
    heuristic_stakeholders,
    llm_overview,
    llm_stakeholders,
    polish_arguments,
    social_signals,
    summarize_community,
)
from .extraction import extract_chunk, normalize_type
from .graph import EntityResolver, build_graph, entity_key, salience_and_communities
from .models import Argument, Chunk, Community, Dossier, Entity, Relation

logger = logging.getLogger("baraza.knowledge")

DEPTH = {
    "quick": {"extract": 10, "communities": 0, "max_sources": 30},
    "standard": {"extract": 22, "communities": 4, "max_sources": 60},
    "deep": {"extract": 40, "communities": 6, "max_sources": 120},
}


def _make_chunks(project, sources) -> list[Chunk]:
    chunks: list[Chunk] = []
    documents = [s for s in sources if not s.is_social][:]
    social = [s for s in sources if s.is_social]
    for source in documents:
        body = source.body
        if not body or len(body) < 40:
            continue
        pieces = chunk_text(f"{source.title}\n\n{body}" if source.title and source.title not in body[:200] else body, 1600, 200)
        for i, piece in enumerate(pieces[:8]):
            chunks.append(Chunk(project=project, source=source, idx=i, kind="text", text=piece))
    by_platform = defaultdict(list)
    for s in sorted(social, key=lambda s: -(s.relevance + 0.001 * s.engagement)):
        by_platform[s.get_platform_display()].append(s)
    for platform, posts in by_platform.items():
        for i in range(0, min(len(posts), 120), 10):
            group = posts[i : i + 10]
            lines = "\n".join(f"- {truncate(p.body.replace(chr(10), ' '), 260)}" for p in group)
            chunks.append(Chunk(project=project, source=group[0], idx=i // 10, kind="social_digest", text=f"Public posts on {platform}:\n{lines}"))
    return chunks


def _select_for_extraction(chunks: list[Chunk], k: int) -> list[Chunk]:
    social = [c for c in chunks if c.kind == "social_digest"]
    text = [c for c in chunks if c.kind != "social_digest"]
    n_social = min(len(social), max(1, k // 4)) if social else 0
    picked, per_source = [], defaultdict(int)
    for chunk in sorted(text, key=lambda c: -c.score):
        if len(picked) >= k - n_social:
            break
        if per_source[chunk.source_id] >= 3:
            continue
        per_source[chunk.source_id] += 1
        picked.append(chunk)
    picked += sorted(social, key=lambda c: -c.score)[:n_social]
    return picked


def build_knowledge(ctx, project_id):
    from apps.projects.brief import build_brief
    from apps.projects.models import Project
    from apps.research.pipeline import observed_stance_summary

    project = Project.objects.get(pk=project_id)
    config = get_config()
    country = get_country(project.country)
    if not project.brief:
        project.brief = build_brief(project, ctx=ctx.sub(0, 3))
        project.save(update_fields=["brief", "updated_at"])
    brief = project.brief
    depth = DEPTH.get(project.depth, DEPTH["standard"])
    llm = get_llm(project)
    use_llm = llm.available("smart")
    if not use_llm:
        ctx.warn("No language model available: building the knowledge base with heuristics (lower quality).")

    ctx.progress(3, "Preparing evidence")
    with transaction.atomic():
        for model in (Relation, Argument, Community, Entity, Chunk):
            model.objects.filter(project=project).delete()
    sources = list(project.sources.filter(included=True).order_by("-relevance")[: depth["max_sources"] * 3])
    documents = [s for s in sources if not s.is_social][: depth["max_sources"]]
    social = [s for s in sources if s.is_social][:300]
    chunks = _make_chunks(project, documents + social)
    if chunks:
        brief_doc = " ".join([brief.get("title", ""), brief.get("refined_question", ""), " ".join(brief.get("key_issues") or []), " ".join(brief.get("social_keywords") or [])])
        bm25 = BM25([c.text for c in chunks])
        scores = bm25.scores(brief_doc)
        top = max(scores) or 1.0
        source_rel = {s.pk: s.relevance for s in sources}
        for chunk, score in zip(chunks, scores):
            chunk.score = round((score / top) * (0.5 + source_rel.get(chunk.source_id, 0.5)), 4)
        Chunk.objects.bulk_create(chunks, batch_size=200)
        chunks = list(Chunk.objects.filter(project=project).select_related("source"))
    ctx.log(f"Prepared {len(chunks)} evidence chunks from {len(documents)} documents and {len(social)} public posts.")

    # Embeddings (optional, free with `ollama pull nomic-embed-text`).
    if chunks:
        ctx.progress(8, "Embedding evidence")
        vectors = llm.embed([c.text for c in chunks])
        if vectors:
            for chunk, vector in zip(chunks, vectors):
                chunk.embedding = to_bytes(vector)
            Chunk.objects.bulk_update(chunks, ["embedding"], batch_size=200)
            ctx.log(f"Embedded {len(vectors)} chunks for semantic retrieval.")
        else:
            ctx.log("Embeddings unavailable; using keyword (BM25) retrieval. Tip: `ollama pull nomic-embed-text`.")
        ctx.check_cancel()

    # GraphRAG extraction.
    selected = _select_for_extraction(chunks, depth["extract"])
    results: list[tuple[Chunk, dict]] = []
    methods = defaultdict(int)
    workers = max(1, int(config.get("LLM_CONCURRENCY", 2))) if use_llm else 4

    def work(chunk: Chunk):
        close_old_connections()
        try:
            label = "public posts" if chunk.kind == "social_digest" else f"{chunk.source.get_platform_display()} - {truncate(chunk.source.title, 80)}"
            return chunk, extract_chunk(
                llm, use_llm, chunk.text, title=brief.get("title", project.title), question=brief.get("refined_question", ""),
                country=country.name, label=label, chunk_id=chunk.pk, source_id=chunk.source_id,
            )
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="extract") as pool:
        futures = [pool.submit(work, c) for c in selected]
        for i, future in enumerate(as_completed(futures), start=1):
            chunk, (data, method) = future.result()
            methods[method] += 1
            results.append((chunk, data))
            ctx.progress(10 + 45 * i / max(1, len(selected)), "Extracting entities, relations and arguments", f"{i}/{len(selected)} chunks read")
            ctx.check_cancel()
    Chunk.objects.filter(pk__in=[c.pk for c, _ in results]).update(extracted=True)
    ctx.log(f"Extraction done ({dict(methods)}).")

    # Entity resolution + graph.
    ctx.progress(56, "Building the knowledge graph")
    resolver = EntityResolver()
    relations_raw, cooccurrence, raw_args, facts, rumors = [], [], [], [], []
    for chunk, data in results:
        keys = []
        for e in data["entities"]:
            key = resolver.add(e["name"], e["type"], e.get("description", ""), e.get("stance"), e.get("mentions", 1))
            if key:
                keys.append(key)
        cooccurrence.append(keys)
        for r in data["relations"]:
            src = resolver.add(r["source"], "organization", mentions=0)
            tgt = resolver.add(r["target"], "organization", mentions=0)
            if src and tgt and src != tgt:
                relations_raw.append((src, tgt, r["type"], r.get("description", ""), chunk.pk))
        raw_args.extend(data["arguments"])
        facts.extend(data["facts"])
        rumors.extend(data["rumors"])
    for s in brief.get("stakeholders") or []:
        resolver.add(s["name"], normalize_type(s.get("type", "organization")), stance=s.get("likely_position"), mentions=1)
    entities = resolver.finalize()
    graph = build_graph(entities, [(s, t, ty, 1.0) for s, t, ty, _, _ in relations_raw], cooccurrence)
    salience, communities = salience_and_communities(graph)
    stakeholder_keys = {entity_key(s["name"]) for s in brief.get("stakeholders") or []}
    entity_objs = [
        Entity(
            project=project,
            name=e["name"][:200],
            key=key,
            type=e["type"] if e["type"] in Entity.Type.values else "other",
            description=e["description"],
            aliases=e["aliases"],
            mentions=max(1, e["mentions"]),
            salience=salience.get(key, 0.0),
            stance=e["stance"],
            community=communities.get(key),
            is_stakeholder=key in stakeholder_keys,
        )
        for key, e in entities.items()
    ]
    Entity.objects.bulk_create(entity_objs, batch_size=200, ignore_conflicts=True)
    by_key = {e.key: e for e in Entity.objects.filter(project=project)}
    Relation.objects.bulk_create(
        [
            Relation(project=project, source=by_key[s], target=by_key[t], type=ty, description=desc[:500], evidence_id=chunk_id)
            for s, t, ty, desc, chunk_id in relations_raw
            if s in by_key and t in by_key
        ],
        batch_size=300,
    )
    ctx.log(f"Graph: {len(by_key)} entities, {len(relations_raw)} relations, {len(set(communities.values()))} communities.")
    ctx.check_cancel()

    # Argument bank.
    ctx.progress(62, "Consolidating the argument bank")
    arguments = consolidate_arguments(raw_args)
    for side in ("oppose", "support"):
        have = sum(1 for a in arguments if a["side"] == side)
        if have < 3:
            extra = []
            if use_llm:
                extra = generate_missing_arguments(llm, side=side, n=4 - have, title=brief.get("title", project.title), summary=brief.get("summary", ""), country=country.name)
            if not extra:
                extra = heuristic_missing_arguments(side, brief)
            arguments.extend(extra[: max(0, 5 - have)])
    if not any(a["side"] == "neutral" for a in arguments):
        arguments.extend(heuristic_missing_arguments("neutral", brief))
    if use_llm:
        ctx.progress(66, "Polishing arguments")
        polish_arguments(llm, [a for a in arguments if not a.get("short")], title=brief.get("title", project.title), demonym=country.demonym)
    Argument.objects.bulk_create(
        [
            Argument(
                project=project,
                side=a["side"],
                claim=a["claim"],
                short=truncate(a.get("short") or a["claim"], 280),
                rationale=a.get("rationale", ""),
                strength=a.get("strength", 0.5),
                frequency=a.get("frequency", 1),
                emotion=a.get("emotion", "neutral"),
                groups=a.get("groups", []),
                evidence=[e for e in a.get("evidence", []) if e],
            )
            for a in arguments
        ]
    )
    ctx.log(f"Argument bank: {sum(1 for a in arguments if a['side'] == 'support')} for, {sum(1 for a in arguments if a['side'] == 'oppose')} against.")
    ctx.check_cancel()

    # Dossier.
    ctx.progress(70, "Writing the research dossier")
    top_chunks = sorted([c for c in chunks if c.kind != "social_digest"], key=lambda c: -c.score)
    diverse, seen_sources = [], set()
    for chunk in top_chunks:
        if chunk.source_id in seen_sources and len(diverse) < 8:
            continue
        seen_sources.add(chunk.source_id)
        diverse.append(chunk)
        if len(diverse) >= 10:
            break
    diverse += sorted([c for c in chunks if c.kind == "social_digest"], key=lambda c: -c.score)[:2]
    entities_sorted = sorted(by_key.values(), key=lambda e: -e.salience)
    overview = stakeholder_data = None
    generated_by = "heuristic"
    if use_llm:
        try:
            overview = llm_overview(llm, project=project, brief=brief, chunks=diverse, facts=facts, country=country.name)
            ctx.progress(80, "Mapping stakeholders and impacts")
            stakeholder_data = llm_stakeholders(llm, project=project, brief=brief, entities=entities_sorted, arguments=arguments, country=country.name)
            generated_by = "llm"
        except Exception as exc:  # noqa: BLE001 - fall back to heuristics
            ctx.warn(f"Dossier synthesis by the model failed ({exc}); using heuristic summary.")
    if not overview or not overview.get("summary"):
        overview = heuristic_overview(brief=brief, chunks=diverse, facts=facts, sources=sources)
    if not stakeholder_data or not stakeholder_data.get("stakeholders"):
        stakeholder_data = heuristic_stakeholders(brief=brief, entities=entities_sorted)
    if not stakeholder_data.get("impact_matrix"):
        stakeholder_data["impact_matrix"] = [dict(g) for g in brief.get("affected_groups", [])]

    # Feed stakeholder positions back into the graph.
    for s in stakeholder_data["stakeholders"]:
        key = entity_key(s["name"])
        entity = by_key.get(key)
        if entity is None:
            entity = Entity.objects.create(project=project, name=s["name"][:200], key=key, type=normalize_type(s["type"]), mentions=1, salience=0.3)
            by_key[key] = entity
        entity.stance = s["position"]
        entity.is_stakeholder = True
        if s.get("summary") and not entity.description:
            entity.description = s["summary"]
        entity.save(update_fields=["stance", "is_stakeholder", "description"])

    # Community summaries (GraphRAG "global" view).
    n_comm = depth["communities"]
    comm_members: dict[int, list[Entity]] = defaultdict(list)
    for entity in entities_sorted:
        if entity.community is not None:
            comm_members[entity.community].append(entity)
    community_objs = []
    for idx, members in sorted(comm_members.items(), key=lambda kv: -len(kv[1]))[: max(n_comm, 8)]:
        if len(members) < 2:
            continue
        title, summary = community_title([m.name for m in members]), ""
        if use_llm and len(community_objs) < n_comm and len(members) >= 3:
            member_ids = [m.pk for m in members]
            rels = [f"{r.source.name} {r.type.lower().replace('_', ' ')} {r.target.name}" for r in Relation.objects.filter(project=project, source_id__in=member_ids, target_id__in=member_ids).select_related("source", "target")[:12]]
            title, summary = summarize_community(llm, title=brief.get("title", project.title), members=members, relations=rels)
        community_objs.append(Community(project=project, index=idx, title=title or community_title([m.name for m in members]), summary=summary, entity_keys=[m.key for m in members[:30]], size=len(members)))
    Community.objects.bulk_create(community_objs)

    ctx.progress(92, "Summarising public sentiment signals")
    observed = observed_stance_summary(project)
    observed.pop("values", None)
    signals = {"observed": observed, **social_signals(sources)}
    rumor_list = list(dict.fromkeys([r["rumor"] for r in rumors] + stakeholder_data.get("rumors", [])))[:8]
    Dossier.objects.update_or_create(
        project=project,
        defaults={
            "summary": overview["summary"],
            "background": overview["background"],
            "timeline": overview["timeline"],
            "key_facts": overview["key_facts"] or [{"fact": f["fact"], "sources": [f["source"]] if f.get("source") else []} for f in facts[:10]],
            "stakeholders": stakeholder_data["stakeholders"],
            "impact_matrix": annotate_impact_matrix(stakeholder_data["impact_matrix"]),
            "sentiment_signals": signals,
            "narratives": overview.get("narratives", []),
            "rumors": rumor_list,
            "uncertainties": overview.get("uncertainties", []),
            "generated_by": generated_by,
        },
    )
    project.save(update_fields=["updated_at"])
    return {
        "chunks": len(chunks),
        "entities": len(by_key),
        "relations": len(relations_raw),
        "arguments": len(arguments),
        "communities": len(community_objs),
        "dossier": generated_by,
    }
