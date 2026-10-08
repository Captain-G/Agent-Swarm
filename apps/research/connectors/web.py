"""Web and news search connectors (free first, then low-cost APIs)."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus

from apps.core.text.textutils import clean_text, keywords

from ..http import FetchError
from .base import TIMELIMIT_DAYS, Connector, CostTier, RawItem, SearchContext, parse_date

DDG_REGIONS = {"ZA": "za-en"}


class DuckDuckGoWeb(Connector):
    key = "ddg_web"
    name = "DuckDuckGo web search"
    platform = "web"
    cost = CostTier.FREE
    query_kind = "web"
    description = "General web search through the open-source 'ddgs' metasearch library (DuckDuckGo, Bing, Brave, Mojeek and others). No account needed."
    setup = "Works out of the box. If you see rate-limit errors, wait a few minutes or self-host SearXNG."
    terms = "Unofficial access to public search results; keep volumes modest and respectful."
    docs_url = "https://pypi.org/project/ddgs/"

    def _ddgs(self):
        from ddgs import DDGS

        return DDGS(timeout=15)

    def search(self, query, ctx: SearchContext):
        region = DDG_REGIONS.get(ctx.country.code, "wt-wt")
        results = self._ddgs().text(query, region=region, safesearch="moderate", timelimit=ctx.timelimit, max_results=ctx.limit)
        return [
            RawItem(
                url=r.get("href") or r.get("url") or "",
                title=clean_text(r.get("title"), 300),
                text=clean_text(r.get("body")),
                platform="web",
                kind="snippet",
            )
            for r in results or []
            if r.get("href") or r.get("url")
        ]


class DuckDuckGoNews(DuckDuckGoWeb):
    key = "ddg_news"
    name = "DuckDuckGo news search"
    platform = "news"
    description = "Recent news articles via the 'ddgs' metasearch library (DuckDuckGo News, Bing News, Yahoo News). No account needed."

    def search(self, query, ctx: SearchContext):
        region = DDG_REGIONS.get(ctx.country.code, "wt-wt")
        results = self._ddgs().news(query, region=region, safesearch="moderate", timelimit=ctx.timelimit, max_results=ctx.limit)
        return [
            RawItem(
                url=r.get("url") or r.get("href") or "",
                title=clean_text(r.get("title"), 300),
                text=clean_text(r.get("body")),
                platform="news",
                kind="snippet",
                author=clean_text(r.get("source"), 120),
                published_at=parse_date(r.get("date")),
                meta={"source": r.get("source", "")},
            )
            for r in results or []
            if r.get("url") or r.get("href")
        ]


class GoogleNewsRSS(Connector):
    key = "google_news"
    name = "Google News (RSS)"
    platform = "news"
    cost = CostTier.FREE
    query_kind = "web"
    description = "Country editions of Google News (e.g. Kenya, Nigeria, South Africa) via the public RSS feed. Excellent for local outlets."
    setup = "Works out of the box."
    terms = "Public RSS feed for personal/research use; links point to the original publishers."
    docs_url = "https://news.google.com"

    def search(self, query, ctx: SearchContext):
        q = query
        if ctx.timelimit in TIMELIMIT_DAYS:
            q = f"{query} when:{TIMELIMIT_DAYS[ctx.timelimit]}d"
        if ctx.country.google_news:
            hl, gl = ctx.country.google_news
        else:
            hl, gl = "en-US", "US"
            if ctx.country.code != "XX" and ctx.country.name.lower() not in query.lower():
                q = f"{q} {ctx.country.name}"
        lang = hl.split("-")[0]
        url = f"https://news.google.com/rss/search?q={quote_plus(q)}&hl={hl}&gl={gl}&ceid={gl}:{lang}"
        response = ctx.fetcher.get(url)
        if response.status_code >= 400:
            raise FetchError(f"Google News returned HTTP {response.status_code}")
        return parse_rss(response.text, platform="news", limit=ctx.limit)


def parse_rss(xml_text: str, *, platform: str = "news", limit: int = 20) -> list[RawItem]:
    try:
        root = ET.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
    except ET.ParseError:
        return []
    items = []
    ns_atom = "{http://www.w3.org/2005/Atom}"
    entries = root.findall(".//item") or root.findall(f".//{ns_atom}entry")
    for node in entries[:limit]:
        title = node.findtext("title") or node.findtext(f"{ns_atom}title") or ""
        link = node.findtext("link") or ""
        if not link:
            link_el = node.find(f"{ns_atom}link")
            link = link_el.get("href", "") if link_el is not None else ""
        description = node.findtext("description") or node.findtext(f"{ns_atom}summary") or ""
        source_el = node.find("source")
        source = source_el.text if source_el is not None and source_el.text else ""
        published = node.findtext("pubDate") or node.findtext(f"{ns_atom}updated") or node.findtext(f"{ns_atom}published")
        clean_title = clean_text(title, 300)
        if source and clean_title.endswith(f" - {source}"):
            clean_title = clean_title[: -len(source) - 3]
        text = clean_text(description)
        if text.startswith(clean_title):
            text = text[len(clean_title):].strip(" - ")
        if not text or text == source:
            text = clean_title
        items.append(
            RawItem(
                url=link.strip(),
                title=clean_title,
                text=text,
                platform=platform,
                kind="snippet",
                author=source,
                published_at=parse_date(published),
                meta={"source": source, "source_url": source_el.get("url", "") if source_el is not None else ""},
            )
        )
    return [i for i in items if i.url]


class GDELT(Connector):
    key = "gdelt"
    name = "GDELT global news index"
    platform = "news"
    cost = CostTier.FREE
    query_kind = "web"
    description = "The GDELT Project monitors news worldwide in 100+ languages. Free API, filtered to the country's own media."
    setup = "Works out of the box (limited to one request every 5 seconds)."
    terms = "Free and open for research use; please cite the GDELT Project."
    docs_url = "https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/"

    TIMESPANS = {"d": "1d", "w": "1week", "m": "1month", "y": "1year"}

    def build_query(self, query: str, ctx: SearchContext) -> str:
        terms = [t for t in keywords(query, 3) if len(t) > 3] or [query]
        phrase = " ".join(f'"{t}"' if " " in t else t for t in terms[:3])
        country_name = ctx.country.name.lower()
        phrase = re.sub(rf"\b{re.escape(country_name)}\b", "", phrase, flags=re.I).strip() or terms[0]
        if ctx.country.gdelt_name:
            phrase += f" sourcecountry:{ctx.country.gdelt_name}"
        return phrase

    def search(self, query, ctx: SearchContext):
        params = {
            "query": self.build_query(query, ctx),
            "mode": "artlist",
            "format": "json",
            "maxrecords": str(min(75, max(10, ctx.limit * 2))),
            "sort": "hybridrel",
            "timespan": self.TIMESPANS.get(ctx.timelimit or "m", "1month"),
        }
        response = ctx.fetcher.get("https://api.gdeltproject.org/api/v2/doc/doc", params=params)
        if response.status_code >= 400:
            raise FetchError(f"GDELT returned HTTP {response.status_code}")
        try:
            data = response.json()
        except ValueError:
            # GDELT answers query errors in plain text.
            ctx.log(f"GDELT: {response.text.strip()[:120]}")
            return []
        out = []
        for art in (data.get("articles") or [])[: ctx.limit]:
            if (art.get("language") or "English") not in ("English", "Swahili", "French"):
                continue
            out.append(
                RawItem(
                    url=art.get("url", ""),
                    title=clean_text(art.get("title"), 300),
                    text=clean_text(art.get("title")),
                    platform="news",
                    kind="snippet",
                    author=art.get("domain", ""),
                    published_at=parse_date(art.get("seendate")),
                    meta={"domain": art.get("domain"), "language": art.get("language"), "sourcecountry": art.get("sourcecountry")},
                )
            )
        return out


class CustomRSS(Connector):
    key = "custom_rss"
    name = "Your RSS feeds"
    platform = "news"
    cost = CostTier.FREE
    query_kind = "web"
    requires = ("CUSTOM_RSS_FEEDS",)
    description = "Any RSS/Atom feeds you list (local newspapers, blogs, government press releases). Items are filtered by keywords."
    setup = "Add comma-separated feed URLs in Settings → Data sources (CUSTOM_RSS_FEEDS)."
    terms = "RSS feeds are published for syndication; respect each publisher's terms."

    def search(self, query, ctx: SearchContext):
        feeds = [f.strip() for f in (ctx.config.get("CUSTOM_RSS_FEEDS") or "").split(",") if f.strip()]
        terms = {t.lower() for t in keywords(query, 6)} | {w.lower() for w in query.split() if len(w) > 4}
        out = []
        for feed in feeds[:20]:
            try:
                response = ctx.fetcher.get(feed)
            except FetchError as exc:
                ctx.log(f"RSS {feed}: {exc}")
                continue
            for item in parse_rss(response.text, platform="news", limit=60):
                haystack = f"{item.title} {item.text}".lower()
                if any(term in haystack for term in terms):
                    out.append(item)
        return out[: ctx.limit * 2]


class SearXNG(Connector):
    key = "searxng"
    name = "SearXNG (self-hosted metasearch)"
    platform = "web"
    cost = CostTier.SELF_HOSTED
    query_kind = "web"
    requires = ("SEARXNG_URL",)
    description = "Your own private metasearch engine aggregating 70+ search services. The most reliable free option for heavy use."
    setup = "Run `docker compose --profile searxng up -d` (see docs/data-sources.md) and set SEARXNG_URL=http://localhost:8888."
    terms = "Self-hosted; you control the traffic."
    docs_url = "https://docs.searxng.org/"

    def search(self, query, ctx: SearchContext):
        base = ctx.config["SEARXNG_URL"].rstrip("/")
        params = {"q": query, "format": "json", "language": "en", "categories": "general,news", "safesearch": "1"}
        if ctx.timelimit in ("d", "w", "m", "y"):
            params["time_range"] = {"d": "day", "w": "week", "m": "month", "y": "year"}[ctx.timelimit]
        data = ctx.fetcher.get_json(f"{base}/search", params=params)
        return [
            RawItem(
                url=r.get("url", ""),
                title=clean_text(r.get("title"), 300),
                text=clean_text(r.get("content")),
                platform="news" if r.get("category") == "news" else "web",
                kind="snippet",
                published_at=parse_date(r.get("publishedDate")),
                meta={"engine": r.get("engine")},
            )
            for r in (data.get("results") or [])[: ctx.limit]
            if r.get("url")
        ]


class Tavily(Connector):
    key = "tavily"
    name = "Tavily AI search"
    platform = "web"
    cost = CostTier.FREEMIUM
    query_kind = "web"
    default_enabled = False
    requires = ("TAVILY_API_KEY",)
    description = "Search API built for AI research that returns cleaned page content."
    setup = "Create a free account at tavily.com and paste the API key in Settings."
    price_note = "Free plan includes about 1,000 searches per month (check current pricing)."
    docs_url = "https://docs.tavily.com/"

    def search(self, query, ctx: SearchContext):
        payload = {
            "query": query,
            "max_results": min(20, ctx.limit),
            "search_depth": "basic",
            "include_raw_content": False,
            "topic": "general",
        }
        response = ctx.fetcher.post(
            "https://api.tavily.com/search",
            json=payload,
            headers={"Authorization": f"Bearer {ctx.config['TAVILY_API_KEY']}"},
        )
        if response.status_code >= 400:
            raise FetchError(f"Tavily HTTP {response.status_code}: {response.text[:150]}")
        return [
            RawItem(
                url=r.get("url", ""),
                title=clean_text(r.get("title"), 300),
                text=clean_text(r.get("raw_content") or r.get("content")),
                platform="web",
                kind="article" if r.get("raw_content") else "snippet",
                published_at=parse_date(r.get("published_date")),
            )
            for r in response.json().get("results", [])
            if r.get("url")
        ]


class BraveSearch(Connector):
    key = "brave"
    name = "Brave Search API"
    platform = "web"
    cost = CostTier.FREEMIUM
    query_kind = "web"
    default_enabled = False
    requires = ("BRAVE_API_KEY",)
    description = "Independent web index with a generous free tier."
    setup = "Get a key at api-dashboard.search.brave.com and paste it in Settings."
    price_note = "Free monthly credits; paid plans are a few dollars per thousand queries (check current pricing)."
    docs_url = "https://api-dashboard.search.brave.com/app/documentation"

    def search(self, query, ctx: SearchContext):
        params = {"q": query, "count": str(min(20, ctx.limit)), "safesearch": "moderate"}
        if ctx.timelimit in ("d", "w", "m", "y"):
            params["freshness"] = {"d": "pd", "w": "pw", "m": "pm", "y": "py"}[ctx.timelimit]
        data = ctx.fetcher.get_json(
            "https://api.search.brave.com/res/v1/web/search",
            params=params,
            headers={"X-Subscription-Token": ctx.config["BRAVE_API_KEY"], "Accept": "application/json"},
        )
        results = (data.get("web") or {}).get("results") or []
        return [
            RawItem(
                url=r.get("url", ""),
                title=clean_text(r.get("title"), 300),
                text=clean_text(r.get("description")),
                platform="web",
                kind="snippet",
                published_at=parse_date(r.get("page_age")),
            )
            for r in results
            if r.get("url")
        ]


class Serper(Connector):
    key = "serper"
    name = "Serper (Google results API)"
    platform = "news"
    cost = CostTier.FREEMIUM
    query_kind = "web"
    default_enabled = False
    requires = ("SERPER_API_KEY",)
    description = "Google search and Google News results as JSON, localised to the country."
    setup = "Sign up at serper.dev (free starter credits) and paste the key in Settings."
    price_note = "About 2,500 free queries on sign-up, then roughly $1 per 1,000 queries (check current pricing)."
    docs_url = "https://serper.dev"

    def search(self, query, ctx: SearchContext):
        payload = {"q": query, "gl": ctx.country.code.lower() if ctx.country.code != "XX" else "us", "hl": "en", "num": min(20, ctx.limit)}
        if ctx.timelimit in ("d", "w", "m", "y"):
            payload["tbs"] = f"qdr:{ctx.timelimit}"
        response = ctx.fetcher.post(
            "https://google.serper.dev/news", json=payload, headers={"X-API-KEY": ctx.config["SERPER_API_KEY"]}
        )
        if response.status_code >= 400:
            raise FetchError(f"Serper HTTP {response.status_code}")
        return [
            RawItem(
                url=r.get("link", ""),
                title=clean_text(r.get("title"), 300),
                text=clean_text(r.get("snippet")),
                platform="news",
                kind="snippet",
                author=r.get("source", ""),
                published_at=_relative_date(r.get("date")),
            )
            for r in response.json().get("news", [])
            if r.get("link")
        ]


def _relative_date(text: str | None):
    """'3 days ago' -> datetime."""
    if not text:
        return None
    match = re.match(r"(\d+)\s+(minute|hour|day|week|month|year)s?\s+ago", text.strip(), re.I)
    if not match:
        return parse_date(text)
    n, unit = int(match.group(1)), match.group(2).lower()
    days = {"minute": 1 / 1440, "hour": 1 / 24, "day": 1, "week": 7, "month": 30, "year": 365}[unit] * n
    return datetime.now(timezone.utc) - timedelta(days=days)
