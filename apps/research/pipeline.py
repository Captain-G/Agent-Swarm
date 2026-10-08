"""
Deep research pipeline.

1. Plan queries from the brief (web/news queries, short social keywords,
   reference topics).
2. Run every enabled connector in parallel (politely rate-limited).
3. De-duplicate, score relevance (BM25 against the brief), anonymise authors.
4. Fetch full text for the most relevant articles (robots.txt respected).
5. Score sentiment for everything; ask the LLM to label the *stance* of
   social posts toward the policy (this later calibrates the simulation).
"""

from __future__ import annotations

import hashlib
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta

from django.db import close_old_connections, connection
from django.utils import timezone

from apps.core.config import get_config
from apps.core.geo import get_country
from apps.core.llm import LLMError, as_float, get_llm
from apps.core.text.retrieval import BM25
from apps.core.text.sentiment import sentiment
from apps.core.text.textutils import clean_text, content_hash, keywords, normalize_url, truncate

from .connectors import CONNECTORS_BY_KEY, Connector, RawItem, SearchContext, default_selection
from .connectors.social import anonymize
from .extract import extract_upload, fetch_article, parse_social_csv
from .http import Fetcher
from .models import Source

logger = logging.getLogger("baraza.research")

DEPTH = {
    "quick": {"queries": 4, "social_queries": 3, "reference": 2, "per_query": 6, "fulltext": 8, "stance_items": 32},
    "standard": {"queries": 8, "social_queries": 5, "reference": 3, "per_query": 8, "fulltext": 20, "stance_items": 80},
    "deep": {"queries": 12, "social_queries": 8, "reference": 4, "per_query": 10, "fulltext": 40, "stance_items": 160},
}
# Expensive or quota-limited connectors get fewer queries.
QUERY_CAPS = {"youtube": 3, "gdelt": 4, "apify": 2, "twitterapi_io": 3, "x_api": 3, "tavily": 4, "serper": 4, "brave": 5, "telegram": 3, "custom_rss": 2, "social_index": 3, "mastodon": 3}


def make_fetcher(config: dict | None = None) -> Fetcher:
    config = config or get_config()
    return Fetcher(
        user_agent=config["HTTP_USER_AGENT"],
        timeout=config.get("HTTP_TIMEOUT", 20),
        respect_robots=config.get("RESPECT_ROBOTS_TXT", True),
    )


def _social_queries(brief: dict, limit: int) -> list[str]:
    raw = list(brief.get("social_keywords") or [])
    raw += [h for h in (brief.get("hashtags") or {}).get("oppose", [])[:1]]
    raw += [h for h in (brief.get("hashtags") or {}).get("support", [])[:1]]
    if not raw:
        raw = keywords(f"{brief.get('title', '')}. {brief.get('summary', '')}", 4)
    out, seen = [], set()
    for q in raw:
        q = q.strip()
        if q and q.lower() not in seen:
            seen.add(q.lower())
            out.append(q)
    return out[:limit]


def plan_tasks(brief: dict, connectors: list[Connector], depth: dict, extra_queries: list[str] | None = None) -> list[tuple[Connector, str]]:
    web = list(extra_queries or []) + list(brief.get("search_queries") or [])[: depth["queries"]]
    web += list(brief.get("local_queries") or [])[:2]
    social = _social_queries(brief, depth["social_queries"])
    reference = [brief.get("title", "")] + [s["name"] for s in (brief.get("stakeholders") or [])[:3] if s.get("name")]
    reference = [r for r in reference if r][: depth["reference"]]
    plan = []
    for connector in connectors:
        queries = {"web": web, "social": social or web[:3], "reference": reference}[connector.query_kind]
        cap = QUERY_CAPS.get(connector.key)
        for query in queries[:cap] if cap else queries:
            plan.append((connector, query))
    return plan


def _item_hash(item: RawItem) -> str:
    if item.url:
        return hashlib.sha1(normalize_url(item.url).encode("utf-8")).hexdigest()
    return content_hash(f"{item.title} {item.text}")


def _brief_document(brief: dict) -> str:
    parts = [brief.get("title", ""), brief.get("refined_question", ""), brief.get("summary", "")]
    parts += brief.get("key_issues") or []
    parts += brief.get("policy_details") or []
    parts += brief.get("social_keywords") or []
    return " ".join(p for p in parts if p)


def score_relevance(items: list, brief: dict) -> list[float]:
    """BM25 score of each item against the brief, normalised to 0..1."""
    if not items:
        return []
    texts = [f"{getattr(i, 'title', '')} {getattr(i, 'text', '') or getattr(i, 'snippet', '')}" for i in items]
    bm25 = BM25(texts)
    scores = bm25.scores(_brief_document(brief))
    top = max(scores) or 1.0
    return [round(s / top, 4) for s in scores]


def collect(plan: list[tuple[Connector, str]], sctx: SearchContext, ctx=None, span=(5.0, 50.0)) -> tuple[list[RawItem], dict]:
    stats: dict[str, dict] = {}
    items: list[RawItem] = []
    total = max(1, len(plan))
    done = 0

    def run(connector: Connector, query: str):
        close_old_connections()
        try:
            return connector, query, connector.search(query, sctx), None
        except Exception as exc:  # each connector failure is isolated
            return connector, query, [], exc
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="research") as pool:
        futures = [pool.submit(run, c, q) for c, q in plan]
        for future in as_completed(futures):
            connector, query, found, error = future.result()
            done += 1
            entry = stats.setdefault(connector.key, {"name": connector.name, "items": 0, "errors": 0, "last_error": ""})
            if error is not None:
                entry["errors"] += 1
                entry["last_error"] = f"{error.__class__.__name__}: {str(error)[:160]}"
                if ctx:
                    ctx.log(f"{connector.name}: '{truncate(query, 50)}' failed ({entry['last_error']})", "warning")
            else:
                for item in found:
                    item.connector = item.connector or connector.key
                    item.query = item.query or query
                entry["items"] += len(found)
                items.extend(found)
            if ctx:
                lo, hi = span
                ctx.progress(lo + (hi - lo) * done / total, "Searching sources", f"{len(items)} items from {len(stats)} sources so far")
                ctx.check_cancel()
    return items, stats


def save_items(project, items: list[RawItem], brief: dict, config: dict) -> list[Source]:
    existing = set(project.sources.values_list("url_hash", flat=True))
    unique: dict[str, RawItem] = {}
    for item in items:
        if not (item.text or item.title):
            continue
        key = _item_hash(item)
        if key in existing:
            continue
        current = unique.get(key)
        if current is None or len(item.text or "") > len(current.text or ""):
            unique[key] = item
    if not unique:
        return []
    keys = list(unique.keys())
    rel = score_relevance([unique[k] for k in keys], brief)
    anonymise = config.get("ANONYMIZE_SOCIAL_AUTHORS", True)
    sources = []
    for key, relevance in zip(keys, rel):
        item = unique[key]
        is_social = item.platform in Source.SOCIAL_PLATFORMS
        text = clean_text(item.text, 30000)
        sources.append(
            Source(
                project=project,
                connector=item.connector[:40],
                platform=item.platform if item.platform in Source.Platform.values else "web",
                kind=item.kind if item.kind in Source.Kind.values else "snippet",
                url=item.url[:1000],
                url_hash=key,
                title=truncate(item.title or text[:120], 500),
                author=(anonymize(item.author) if (is_social and anonymise) else item.author)[:200],
                published_at=item.published_at,
                query=item.query[:300],
                snippet=text if item.kind in ("snippet", "social_post", "comment") else truncate(text, 600),
                text=text if item.kind in ("article", "reference", "document") else "",
                full_text=item.kind in ("article", "reference", "document"),
                metrics=item.metrics or {},
                meta=item.meta or {},
                sentiment=round(sentiment(f"{item.title}. {text}" if not is_social else text), 4),
                relevance=relevance,
                included=relevance >= 0.04 or is_social,
            )
        )
    Source.objects.bulk_create(sources, ignore_conflicts=True, batch_size=200)
    return list(project.sources.filter(url_hash__in=keys))


def enrich_full_text(sources: list[Source], fetcher: Fetcher, config: dict, limit: int, ctx=None, span=(50.0, 75.0)) -> int:
    candidates = [s for s in sources if s.included and not s.full_text and s.platform in ("web", "news") and s.url]
    candidates.sort(key=lambda s: -s.relevance)
    candidates = candidates[:limit]
    if not candidates:
        return 0
    enriched = 0
    done = 0

    def work(source: Source):
        return source, fetch_article(source.url, fetcher, jina_key=config.get("JINA_API_KEY", ""))

    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="extract") as pool:
        futures = [pool.submit(work, s) for s in candidates]
        for future in as_completed(futures):
            source, (title, text) = future.result()
            done += 1
            if text and len(text) > len(source.snippet or ""):
                Source.objects.filter(pk=source.pk).update(
                    text=text,
                    full_text=True,
                    kind="article",
                    title=truncate(title or source.title, 500),
                    sentiment=round(sentiment(text[:5000]), 4),
                )
                enriched += 1
            if ctx:
                lo, hi = span
                ctx.progress(lo + (hi - lo) * done / len(candidates), "Reading full articles", f"{enriched} articles extracted")
                ctx.check_cancel()
    return enriched


STANCE_SYSTEM = "You label public social media posts by their stance toward one specific policy or action. You are careful and neutral. Reply with JSON only."
STANCE_USER = """POLICY / ACTION: {title}
WHAT IT IS: {summary}

For each numbered post, decide its stance toward this policy/action:
-1 = strongly against, -0.5 = somewhat against, 0 = neutral, unclear or unrelated, 0.5 = somewhat supportive, 1 = strongly supportive.
Sarcasm counts as its real meaning. Celebrating the policy's withdrawal means AGAINST the policy.

POSTS:
{posts}

Return JSON: {{"labels": [{{"id": 1, "stance": -0.5, "confidence": 0.8}}, ...]}} with one entry per post."""


def classify_stance(project, sources: list[Source], ctx=None, max_items: int = 80, batch_size: int = 8, span=(75.0, 98.0)) -> int:
    llm = get_llm(project)
    targets = [s for s in sources if s.included and s.is_social and s.stance is None and (s.snippet or s.text)]
    targets.sort(key=lambda s: -(s.relevance + 0.002 * s.engagement))
    targets = targets[:max_items]
    if not targets:
        return 0
    if not llm.available("fast"):
        if ctx:
            ctx.warn("No language model: skipping stance labelling of social posts (sentiment scores are still available).")
        return 0
    brief = project.brief or {}
    labelled = 0
    batches = [targets[i : i + batch_size] for i in range(0, len(targets), batch_size)]
    for b, batch in enumerate(batches):
        posts = "\n".join(f"{i + 1}. \"{truncate((s.snippet or s.text).replace(chr(10), ' '), 280)}\"" for i, s in enumerate(batch))
        try:
            data = llm.json(
                system=STANCE_SYSTEM,
                user=STANCE_USER.format(title=brief.get("title", project.title), summary=truncate(brief.get("summary", project.prompt), 400), posts=posts),
                purpose="classify_stance",
                tier="fast",
                max_tokens=60 * len(batch) + 80,
                temperature=0.0,
            )
        except LLMError as exc:
            if ctx:
                ctx.warn(f"Stance labelling batch failed: {exc}")
            continue
        for label in data.get("labels") or data.get("items") or []:
            if not isinstance(label, dict):
                continue
            idx = int(as_float(label.get("id"), 0)) - 1
            if 0 <= idx < len(batch):
                Source.objects.filter(pk=batch[idx].pk).update(
                    stance=round(as_float(label.get("stance"), 0.0, -1.0, 1.0), 3),
                    stance_confidence=round(as_float(label.get("confidence"), 0.5, 0.0, 1.0), 3),
                )
                labelled += 1
        if ctx:
            lo, hi = span
            ctx.progress(lo + (hi - lo) * (b + 1) / len(batches), "Labelling public stance", f"{labelled} posts labelled")
            ctx.check_cancel()
    return labelled


# ---------------------------------------------------------------------------
# Job entry points
# ---------------------------------------------------------------------------


def run_research(ctx, project_id, connectors: list[str] | None = None, depth: str | None = None, timelimit: str | None = "m", extra_queries: list[str] | None = None):
    from apps.projects.brief import build_brief
    from apps.projects.models import Project

    project = Project.objects.get(pk=project_id)
    config = get_config()
    if not project.brief:
        ctx.progress(2, "Building the brief first")
        project.brief = build_brief(project, ctx=ctx.sub(0, 4))
        project.save(update_fields=["brief", "updated_at"])
    brief = project.brief
    depth_cfg = DEPTH.get(depth or project.depth, DEPTH["standard"])
    selected = connectors or default_selection(config)
    enabled = [CONNECTORS_BY_KEY[k] for k in selected if k in CONNECTORS_BY_KEY and CONNECTORS_BY_KEY[k].is_configured(config)]
    skipped = [k for k in selected if k in CONNECTORS_BY_KEY and not CONNECTORS_BY_KEY[k].is_configured(config)]
    if skipped:
        ctx.log(f"Skipping sources that need setup: {', '.join(skipped)}", "warning")
    if not enabled:
        raise RuntimeError("No data sources are enabled. Choose at least one source.")
    ctx.log(f"Researching '{brief.get('title')}' with {len(enabled)} sources ({', '.join(c.name for c in enabled)}).")

    fetcher = make_fetcher(config)
    sctx = SearchContext(
        country=get_country(project.country),
        fetcher=fetcher,
        config=config,
        brief=brief,
        limit=depth_cfg["per_query"],
        timelimit=timelimit,
        log=lambda m: ctx.log(m, "warning"),
    )
    plan = plan_tasks(brief, enabled, depth_cfg, extra_queries)
    ctx.progress(5, "Searching sources", f"{len(plan)} searches planned")
    items, stats = collect(plan, sctx, ctx)
    ctx.log(f"Collected {len(items)} raw items.")

    ctx.progress(50, "De-duplicating and ranking")
    sources = save_items(project, items, brief, config)
    ctx.log(f"Saved {len(sources)} new unique sources.")

    enriched = enrich_full_text(sources, fetcher, config, depth_cfg["fulltext"], ctx)
    ctx.log(f"Extracted full text for {enriched} articles.")

    sources = list(project.sources.filter(pk__in=[s.pk for s in sources]))
    labelled = classify_stance(project, sources, ctx, max_items=depth_cfg["stance_items"])
    if labelled:
        ctx.log(f"Labelled the stance of {labelled} public posts.")
    fetcher.close()
    total = project.sources.count()
    if total == 0:
        ctx.warn("No sources were found. Try other sources, broader queries, or upload documents manually.")
    project.save(update_fields=["updated_at"])
    return {"new_sources": len(sources), "total_sources": total, "full_text": enriched, "stance_labelled": labelled, "connectors": stats}


def fresh_news(project, *, max_items: int = 5, hours: int = 48) -> list[Source]:
    """Live web grounding during a simulation: fetch the latest headlines."""
    config = get_config()
    brief = project.brief or {}
    fetcher = make_fetcher(config)
    sctx = SearchContext(country=get_country(project.country), fetcher=fetcher, config=config, brief=brief, limit=6, timelimit="d" if hours <= 24 else "w")
    plan = [(CONNECTORS_BY_KEY[k], q) for k in ("google_news", "ddg_news") for q in (brief.get("search_queries") or [project.title])[:2]]
    items, _ = collect(plan, sctx)
    cutoff = timezone.now() - timedelta(hours=hours)
    recent = [i for i in items if i.published_at is None or i.published_at >= cutoff]
    sources = save_items(project, recent, brief, config)
    fetcher.close()
    sources.sort(key=lambda s: -s.relevance)
    return sources[:max_items]


# ---------------------------------------------------------------------------
# Manual evidence
# ---------------------------------------------------------------------------


def add_url(project, url: str) -> Source | None:
    config = get_config()
    fetcher = make_fetcher(config)
    title, text = fetch_article(url, fetcher, jina_key=config.get("JINA_API_KEY", ""))
    fetcher.close()
    if not text:
        return None
    key = hashlib.sha1(normalize_url(url).encode("utf-8")).hexdigest()
    source, _ = Source.objects.update_or_create(
        project=project,
        url_hash=key,
        defaults={
            "connector": "manual_url",
            "platform": "web",
            "kind": "article",
            "url": url[:1000],
            "title": truncate(title or url, 500),
            "text": text,
            "snippet": truncate(text, 600),
            "full_text": True,
            "sentiment": round(sentiment(text[:5000]), 4),
            "relevance": 1.0,
            "included": True,
        },
    )
    return source


def add_text(project, title: str, text: str) -> Source:
    text = clean_text(text, 200000)
    return Source.objects.create(
        project=project,
        connector="manual_text",
        platform="manual",
        kind="document",
        url_hash=content_hash(text),
        title=truncate(title or text[:80], 500),
        text=text,
        snippet=truncate(text, 600),
        full_text=True,
        sentiment=round(sentiment(text[:5000]), 4),
        relevance=1.0,
        included=True,
    )


def add_upload(project, uploaded_file) -> list[Source]:
    data = uploaded_file.read()
    kind, text = extract_upload(uploaded_file.name, data)
    if kind == "social_csv":
        rows = parse_social_csv(data, limit=3000)
        from .connectors.base import parse_date

        sources = []
        for row in rows:
            sources.append(
                Source(
                    project=project,
                    connector="upload_csv",
                    platform=row["platform"] if row["platform"] in Source.Platform.values else "x",
                    kind="social_post",
                    url=(row.get("url") or "")[:1000],
                    url_hash=content_hash(row["text"]),
                    title=truncate(row["text"], 140),
                    snippet=row["text"],
                    published_at=parse_date(row.get("date")),
                    sentiment=round(sentiment(row["text"]), 4),
                    relevance=0.5,
                    included=True,
                )
            )
        Source.objects.bulk_create(sources, ignore_conflicts=True, batch_size=500)
        return sources
    if not text:
        return []
    uploaded_file.seek(0)
    source = Source(
        project=project,
        connector="upload",
        platform="upload",
        kind="document",
        url_hash=content_hash(text),
        title=truncate(uploaded_file.name, 500),
        text=text,
        snippet=truncate(text, 600),
        full_text=True,
        sentiment=round(sentiment(text[:5000]), 4),
        relevance=1.0,
        included=True,
    )
    source.file.save(uploaded_file.name, uploaded_file, save=False)
    source.save()
    return [source]


def observed_stance_summary(project) -> dict:
    """Distribution of public stance observed in research (used for calibration)."""
    stances = list(project.sources.filter(included=True, stance__isnull=False).values_list("stance", "stance_confidence"))
    social_sent = list(project.sources.filter(included=True, platform__in=Source.SOCIAL_PLATFORMS).values_list("sentiment", flat=True))
    out = {"n_stance": len(stances), "n_social": len(social_sent)}
    if stances:
        values = [s for s, c in stances if (c or 0) >= 0.3 and abs(s) > 0.0] or [s for s, _ in stances]
        n = len(values)
        out.update(
            {
                "mean": round(sum(values) / n, 3),
                "support_share": round(sum(1 for v in values if v > 0.2) / n, 3),
                "oppose_share": round(sum(1 for v in values if v < -0.2) / n, 3),
                "neutral_share": round(sum(1 for v in values if -0.2 <= v <= 0.2) / n, 3),
                "values": values,
            }
        )
    if social_sent:
        vals = [v for v in social_sent if v is not None]
        if vals:
            out["social_sentiment_mean"] = round(sum(vals) / len(vals), 3)
    return out
