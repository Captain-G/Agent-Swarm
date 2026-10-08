"""Background/reference knowledge connectors."""

from __future__ import annotations

from urllib.parse import quote

from apps.core.text.textutils import clean_text

from .base import Connector, CostTier, RawItem, SearchContext


class Wikipedia(Connector):
    key = "wikipedia"
    name = "Wikipedia"
    platform = "wikipedia"
    cost = CostTier.FREE
    query_kind = "reference"
    description = "Background on institutions, laws, places and past events. Gives agents shared factual context."
    setup = "Works out of the box."
    terms = "Content under CC BY-SA; cite Wikipedia."
    docs_url = "https://www.mediawiki.org/wiki/API:Main_page"

    def search(self, query, ctx: SearchContext):
        data = ctx.fetcher.get_json(
            "https://en.wikipedia.org/w/api.php",
            params={"action": "query", "list": "search", "srsearch": query, "format": "json", "srlimit": "3", "utf8": "1"},
        )
        items = []
        for hit in (data.get("query") or {}).get("search", [])[:3]:
            title = hit.get("title")
            if not title:
                continue
            page = ctx.fetcher.get_json(
                "https://en.wikipedia.org/w/api.php",
                params={"action": "query", "prop": "extracts", "explaintext": "1", "titles": title, "format": "json", "redirects": "1"},
            )
            pages = (page.get("query") or {}).get("pages") or {}
            extract = ""
            for value in pages.values():
                extract = value.get("extract") or ""
            if not extract:
                continue
            items.append(
                RawItem(
                    url=f"https://en.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}",
                    title=title,
                    text=clean_text(extract, 12000),
                    platform="wikipedia",
                    kind="reference",
                    author="Wikipedia",
                )
            )
        return items
