"""General text helpers: cleaning, tokenising, keywords, sentences and chunking."""

from __future__ import annotations

import hashlib
import html
import re
import unicodedata
from collections import Counter
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

STOPWORDS = set(
    """
a about above after again against all also am an and any are aren't as at be because been before being below
between both but by can can't cannot could couldn't did didn't do does doesn't doing don't down during each
few for from further had hadn't has hasn't have haven't having he he'd he'll he's her here here's hers herself
him himself his how how's i i'd i'll i'm i've if in into is isn't it it's its itself let's me more most mustn't
my myself no nor not of off on once only or other ought our ours ourselves out over own same shan't she she'd
she'll she's should shouldn't so some such than that that's the their theirs them themselves then there there's
these they they'd they'll they're they've this those through to too under until up very was wasn't we we'd we'll
we're we've were weren't what what's when when's where where's which while who who's whom why why's with won't
would wouldn't you you'd you'll you're you've your yours yourself yourselves will just said says say also new
one two three like get got make made many much may might us via per its it’s ’s s t re ve ll d m
na ya wa za la kwa ni katika hii hiyo huu ile kama lakini pia sana tu bado hata kuwa kwamba yetu wetu
yao wao sisi wewe mimi yeye nini gani lini wapi
http https www com co ke org amp rt
""".split()
)

_WORD_RE = re.compile(r"[A-Za-zÀ-ɏ][A-Za-zÀ-ɏ0-9'’\-]*")
_SENT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"“‘(])")
_WS_RE = re.compile(r"[ \t ]+")
_TAG_RE = re.compile(r"<[^>]+>")
_URL_RE = re.compile(r"https?://\S+")
_HASHTAG_RE = re.compile(r"#(\w{2,60})")
_TRACKING_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "fbclid", "gclid", "ref", "s", "igshid"}


def clean_text(text: str | None, max_len: int | None = None) -> str:
    """Strip HTML, normalise whitespace and unicode."""
    if not text:
        return ""
    text = html.unescape(_TAG_RE.sub(" ", str(text)))
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\r", "\n")
    text = "\n".join(_WS_RE.sub(" ", line).strip() for line in text.split("\n"))
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if max_len and len(text) > max_len:
        text = text[: max_len - 1].rstrip() + "…"
    return text


def truncate(text: str, n: int) -> str:
    text = text or ""
    return text if len(text) <= n else text[: max(0, n - 1)].rstrip() + "…"


def tokenize(text: str, *, keep_stopwords: bool = False) -> list[str]:
    tokens = [t.lower().strip("'’-") for t in _WORD_RE.findall(text or "")]
    if keep_stopwords:
        return [t for t in tokens if t]
    return [t for t in tokens if t and t not in STOPWORDS and len(t) > 1]


def sentences(text: str) -> list[str]:
    out = []
    for para in re.split(r"\n{2,}", text or ""):
        para = para.replace("\n", " ").strip()
        if not para:
            continue
        out.extend(s.strip() for s in _SENT_RE.split(para) if s.strip())
    return out


def keywords(text: str, top_k: int = 12, min_len: int = 3) -> list[str]:
    """RAKE-style keyword phrases ranked by degree/frequency."""
    if not text:
        return []
    words_all = [w.lower().strip("'’-") for w in _WORD_RE.findall(text)]
    phrases: list[list[str]] = []
    current: list[str] = []
    for word in words_all:
        if word in STOPWORDS or len(word) < 2:
            if current:
                phrases.append(current)
            current = []
        else:
            current.append(word)
    if current:
        phrases.append(current)
    freq: Counter = Counter()
    degree: Counter = Counter()
    for phrase in phrases:
        for word in phrase:
            freq[word] += 1
            degree[word] += len(phrase) - 1
    scores: dict[str, float] = {}
    for phrase in phrases:
        if len(phrase) > 4:
            continue
        key = " ".join(phrase)
        if len(key) < min_len:
            continue
        scores[key] = max(scores.get(key, 0.0), sum((degree[w] + freq[w]) / freq[w] for w in phrase))
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    out: list[str] = []
    for phrase, _ in ranked:
        if any(phrase in existing or existing in phrase for existing in out):
            continue
        out.append(phrase)
        if len(out) >= top_k:
            break
    return out


def top_terms(texts: list[str], top_k: int = 20) -> list[tuple[str, int]]:
    counter: Counter = Counter()
    for text in texts:
        counter.update(set(tokenize(text)))
    return counter.most_common(top_k)


def hashtags(text: str) -> list[str]:
    return [f"#{h}" for h in _HASHTAG_RE.findall(text or "")]


def strip_urls(text: str) -> str:
    return _URL_RE.sub("", text or "").strip()


def content_hash(text: str) -> str:
    norm = re.sub(r"\W+", " ", (text or "").lower()).strip()
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()


def normalize_url(url: str) -> str:
    """Canonical form used for de-duplication."""
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return url.strip()
    query = [(k, v) for k, v in parse_qsl(parsed.query) if k.lower() not in _TRACKING_PARAMS]
    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    for mobile in ("m.", "mobile."):
        if netloc.startswith(mobile):
            netloc = netloc[len(mobile):]
    if netloc in {"twitter.com", "x.com"}:
        netloc = "x.com"
    path = parsed.path.rstrip("/") or "/"
    return urlunparse((parsed.scheme.lower() or "https", netloc, path, "", urlencode(query), ""))


def domain_of(url: str) -> str:
    try:
        netloc = urlparse(url).netloc.lower()
    except ValueError:
        return ""
    return netloc[4:] if netloc.startswith("www.") else netloc


def chunk_text(text: str, max_chars: int = 1600, overlap: int = 200) -> list[str]:
    """Split text into overlapping chunks on paragraph/sentence boundaries."""
    text = clean_text(text)
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]
    units: list[str] = []
    for para in re.split(r"\n{2,}", text):
        para = para.strip()
        if not para:
            continue
        if len(para) <= max_chars:
            units.append(para)
        else:
            units.extend(sentences(para) or [para])
    chunks: list[str] = []
    current = ""
    for unit in units:
        while len(unit) > max_chars:  # pathological very long sentence
            chunks.append(unit[:max_chars])
            unit = unit[max_chars - overlap :]
        if len(current) + len(unit) + 2 <= max_chars:
            current = f"{current}\n\n{unit}" if current else unit
        else:
            if current:
                chunks.append(current)
            tail = current[-overlap:] if overlap and current else ""
            if tail and " " in tail:
                tail = tail[tail.index(" ") + 1 :]
            current = f"{tail} {unit}".strip() if tail else unit
    if current:
        chunks.append(current)
    return chunks


def slug_handle(name: str, max_len: int = 15) -> str:
    base = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    base = re.sub(r"[^A-Za-z0-9_]+", "", base.replace(" ", "_")).strip("_").lower()
    return (base or "user")[:max_len]


def camel_tag(text: str, max_words: int = 4) -> str:
    """'Finance Bill 2025' -> 'FinanceBill2025'; '2% levy on mobile money' -> 'LevyMobileMoney' (for hashtags)."""
    words = [
        w
        for w in re.findall(r"[A-Za-z0-9]+", text or "")
        if w.lower() not in STOPWORDS and (not w.isdigit() or re.fullmatch(r"(19|20)\d\d", w))
    ][:max_words]
    return "".join(w[:1].upper() + w[1:] for w in words) or "Policy"


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)
