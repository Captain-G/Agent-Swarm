"""
Turning web pages and uploaded files into clean text.

Page extraction tries, in order: trafilatura (best quality, free), a
BeautifulSoup paragraph heuristic, and the Jina Reader service (free tier;
handles JavaScript-heavy pages).
"""

from __future__ import annotations

import csv
import io
import logging
from pathlib import Path

from bs4 import BeautifulSoup

from apps.core.text.textutils import clean_text

from .http import FetchError, Fetcher

logger = logging.getLogger("baraza.research")

SKIP_DOMAINS = ("news.google.com", "facebook.com", "x.com", "twitter.com", "instagram.com", "tiktok.com", "linkedin.com", "youtube.com", "t.me", "reddit.com", "bsky.app")


def extract_html(html: str, url: str = "") -> tuple[str, str]:
    """Return (title, main text) from an HTML document."""
    title = ""
    try:
        import trafilatura

        text = trafilatura.extract(html, url=url or None, include_comments=False, include_tables=False, favor_precision=True) or ""
        meta = trafilatura.extract_metadata(html)
        if meta is not None and getattr(meta, "title", None):
            title = meta.title
        if len(text) > 200:
            return title, clean_text(text, 30000)
    except Exception:  # trafilatura is optional at runtime
        logger.debug("trafilatura failed for %s", url, exc_info=True)
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form", "noscript"]):
        tag.decompose()
    if not title and soup.title and soup.title.string:
        title = soup.title.string
    paragraphs = [p.get_text(" ", strip=True) for p in soup.find_all(["p", "li"])]
    text = "\n\n".join(p for p in paragraphs if len(p) > 60)
    return clean_text(title, 300), clean_text(text, 30000)


def fetch_article(url: str, fetcher: Fetcher, *, jina_key: str = "", use_jina: bool = True) -> tuple[str, str]:
    """Fetch a page and extract its main text. Returns (title, text) - empty text on failure."""
    if not url or any(domain in url for domain in SKIP_DOMAINS):
        return "", ""
    if not fetcher.allowed_by_robots(url):
        logger.info("robots.txt disallows %s", url)
        return "", ""
    try:
        response = fetcher.get(url, timeout=20, retries=1)
        content_type = response.headers.get("content-type", "")
        if response.status_code == 200 and "html" in content_type:
            title, text = extract_html(response.text, url)
            if len(text) > 300:
                return title, text
        elif response.status_code == 200 and "pdf" in content_type:
            return "", extract_pdf_bytes(response.content)
    except FetchError:
        pass
    if use_jina:
        headers = {"Accept": "text/plain"}
        if jina_key:
            headers["Authorization"] = f"Bearer {jina_key}"
        try:
            response = fetcher.get(f"https://r.jina.ai/{url}", headers=headers, timeout=30, retries=0)
            if response.status_code == 200 and len(response.text) > 300:
                text = response.text
                title = ""
                for line in text.splitlines()[:5]:
                    if line.lower().startswith("title:"):
                        title = line.split(":", 1)[1].strip()
                if "Markdown Content:" in text:
                    text = text.split("Markdown Content:", 1)[1]
                return title, clean_text(text, 30000)
        except FetchError:
            pass
    return "", ""


def extract_pdf_bytes(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:  # pragma: no cover
        return ""
    try:
        reader = PdfReader(io.BytesIO(data))
        pages = []
        for page in reader.pages[:200]:
            pages.append(page.extract_text() or "")
        return clean_text("\n\n".join(pages), 200000)
    except Exception:
        logger.warning("PDF extraction failed", exc_info=True)
        return ""


def extract_docx_bytes(data: bytes) -> str:
    try:
        import docx
    except ImportError:  # pragma: no cover
        return ""
    try:
        document = docx.Document(io.BytesIO(data))
        return clean_text("\n\n".join(p.text for p in document.paragraphs), 200000)
    except Exception:
        logger.warning("DOCX extraction failed", exc_info=True)
        return ""


def decode_text(data: bytes) -> str:
    for encoding in ("utf-8", "utf-16"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    try:
        from charset_normalizer import from_bytes

        best = from_bytes(data).best()
        if best is not None:
            return str(best)
    except ImportError:  # pragma: no cover
        pass
    return data.decode("latin-1", errors="replace")


TEXT_COLUMNS = ("text", "content", "body", "message", "post", "tweet", "comment", "full_text", "caption")
DATE_COLUMNS = ("date", "created_at", "createdat", "timestamp", "time", "published_at", "datetime")


def parse_social_csv(data: bytes, limit: int = 20000) -> list[dict]:
    """Parse a CSV export of social posts. Needs a text-like column; date/likes optional."""
    text = decode_text(data)
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return []
    lower = {f.lower().strip(): f for f in reader.fieldnames}
    text_col = next((lower[c] for c in TEXT_COLUMNS if c in lower), None)
    if text_col is None:
        return []
    date_col = next((lower[c] for c in DATE_COLUMNS if c in lower), None)
    rows = []
    for i, row in enumerate(reader):
        if i >= limit:
            break
        body = clean_text(row.get(text_col))
        if not body:
            continue
        rows.append(
            {
                "text": body,
                "date": row.get(date_col) if date_col else None,
                "platform": (row.get(lower.get("platform", ""), "") or "").lower() if "platform" in lower else "",
                "likes": row.get(lower.get("likes", ""), 0) if "likes" in lower else 0,
                "url": row.get(lower.get("url", ""), "") if "url" in lower else "",
            }
        )
    return rows


def extract_upload(name: str, data: bytes) -> tuple[str, str]:
    """Return (kind, text) for an uploaded file."""
    suffix = Path(name).suffix.lower()
    if suffix == ".pdf":
        return "document", extract_pdf_bytes(data)
    if suffix == ".docx":
        return "document", extract_docx_bytes(data)
    if suffix in (".html", ".htm"):
        return "document", extract_html(decode_text(data))[1]
    if suffix == ".csv":
        rows = parse_social_csv(data, limit=5000)
        if rows:
            return "social_csv", "\n".join(r["text"] for r in rows)
        return "document", clean_text(decode_text(data), 200000)
    return "document", clean_text(decode_text(data), 200000)
