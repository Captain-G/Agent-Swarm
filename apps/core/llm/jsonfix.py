"""
Tolerant JSON extraction for LLM output.

Small local models (llama3 8B and friends) frequently wrap JSON in prose or
code fences, leave trailing commas, use single quotes, Python literals, or get
truncated at the token limit. ``extract_json`` recovers from all of these.
"""

from __future__ import annotations

import json
import re
from typing import Any

_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.DOTALL)
_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")
_SMART_QUOTES = str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'"})


class JSONExtractionError(ValueError):
    pass


def _balanced_candidates(text: str) -> list[str]:
    """Return substrings that start at { or [ and end at their matching bracket (string-aware)."""
    candidates = []
    for start, ch in enumerate(text):
        if ch not in "{[":
            continue
        stack = []
        in_str = False
        escape = False
        for i in range(start, len(text)):
            c = text[i]
            if in_str:
                if escape:
                    escape = False
                elif c == "\\":
                    escape = True
                elif c == '"':
                    in_str = False
                continue
            if c == '"':
                in_str = True
            elif c in "{[":
                stack.append(c)
            elif c in "}]":
                if not stack:
                    break
                opener = stack.pop()
                if (opener == "{") != (c == "}"):
                    break
                if not stack:
                    candidates.append(text[start : i + 1])
                    break
        else:
            # Ran off the end: truncated JSON. Keep it for the repair pass.
            candidates.append(text[start:])
        if len(candidates) >= 3:
            break
    return candidates


def _escape_newlines_in_strings(s: str) -> str:
    out = []
    in_str = False
    escape = False
    for c in s:
        if in_str:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                in_str = False
            elif c == "\n":
                out.append("\\n")
                continue
            elif c == "\r":
                continue
            elif c == "\t":
                out.append("\\t")
                continue
        elif c == '"':
            in_str = True
        out.append(c)
    return "".join(out)


def _close_truncated(s: str) -> str:
    """Close unterminated strings/brackets of truncated JSON."""
    stack = []
    in_str = False
    escape = False
    for c in s:
        if in_str:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c in "{[":
            stack.append(c)
        elif c in "}]" and stack:
            stack.pop()
    result = s
    if in_str:
        result += '"'
    result = result.rstrip()
    # Drop a dangling key or trailing comma/colon before closing.
    result = re.sub(r',\s*"[^"]*"\s*:?\s*$', "", result)
    result = re.sub(r"[,:]\s*$", "", result)
    for opener in reversed(stack):
        result += "}" if opener == "{" else "]"
    return result


def _python_literals(s: str) -> str:
    s = re.sub(r"(?<![\"\w])True(?![\"\w])", "true", s)
    s = re.sub(r"(?<![\"\w])False(?![\"\w])", "false", s)
    s = re.sub(r"(?<![\"\w])None(?![\"\w])", "null", s)
    return s


def _single_to_double_quotes(s: str) -> str:
    # Only attempt when there are no double quotes at all (pure Python-dict style).
    if '"' in s:
        return s
    return re.sub(r"'", '"', s)


def _unquoted_keys(s: str) -> str:
    return re.sub(r"([{,]\s*)([A-Za-z_][A-Za-z0-9_\-]*)(\s*:)", r'\1"\2"\3', s)


def _attempts(candidate: str):
    yield candidate
    fixed = candidate.translate(_SMART_QUOTES)
    fixed = _escape_newlines_in_strings(fixed)
    fixed = _TRAILING_COMMA_RE.sub(r"\1", fixed)
    yield fixed
    fixed2 = _python_literals(fixed)
    yield fixed2
    fixed3 = _unquoted_keys(_single_to_double_quotes(fixed2))
    yield _TRAILING_COMMA_RE.sub(r"\1", fixed3)
    yield _TRAILING_COMMA_RE.sub(r"\1", _close_truncated(fixed3))


def extract_json(text: str, expect: type | None = None) -> Any:
    """Parse the first JSON value found in ``text``.

    ``expect`` may be ``dict`` or ``list`` to prefer that shape. A bare list is
    wrapped/unwrapped when the caller expects the other shape and it is safe.
    """
    if text is None:
        raise JSONExtractionError("empty response")
    text = text.strip()
    if not text:
        raise JSONExtractionError("empty response")

    sources = [m.group(1) for m in _FENCE_RE.finditer(text)] + [text]
    errors = []
    for source in sources:
        for candidate in _balanced_candidates(source) or [source]:
            for attempt in _attempts(candidate):
                try:
                    value = json.loads(attempt)
                except (json.JSONDecodeError, ValueError) as exc:
                    errors.append(str(exc))
                    continue
                if expect is dict and isinstance(value, list):
                    if len(value) == 1 and isinstance(value[0], dict):
                        return value[0]
                    return {"items": value}
                if expect is list and isinstance(value, dict):
                    for key in ("items", "results", "data", "list", "output"):
                        if isinstance(value.get(key), list):
                            return value[key]
                    list_values = [v for v in value.values() if isinstance(v, list)]
                    if len(list_values) == 1:
                        return list_values[0]
                    return [value]
                return value
    raise JSONExtractionError(f"could not parse JSON ({errors[-1] if errors else 'no candidates'})")


# ---------------------------------------------------------------------------
# Coercion helpers used when normalising LLM output into our schemas.
# ---------------------------------------------------------------------------


def as_str(value: Any, default: str = "", max_len: int | None = None) -> str:
    if value is None:
        result = default
    elif isinstance(value, str):
        result = value.strip()
    elif isinstance(value, (int, float)):
        result = str(value)
    elif isinstance(value, dict):
        for key in ("text", "value", "name", "description", "content", "summary", "title"):
            if key in value:
                return as_str(value[key], default, max_len)
        result = json.dumps(value, ensure_ascii=False)
    elif isinstance(value, (list, tuple)):
        result = "; ".join(as_str(v) for v in value if as_str(v))
    else:
        result = str(value)
    if max_len and len(result) > max_len:
        result = result[: max_len - 1].rstrip() + "…"
    return result or default


def as_list(value: Any, max_items: int | None = None) -> list:
    if value is None:
        items: list = []
    elif isinstance(value, list):
        items = value
    elif isinstance(value, tuple):
        items = list(value)
    elif isinstance(value, str):
        parts = re.split(r"\n+|;\s*|\s*•\s*", value)
        items = [p.strip(" -*\t") for p in parts if p.strip(" -*\t")]
    else:
        items = [value]
    return items[:max_items] if max_items else items


def as_str_list(value: Any, max_items: int | None = None, max_len: int | None = None) -> list[str]:
    out = []
    for item in as_list(value):
        text = as_str(item, max_len=max_len)
        if text and text not in out:
            out.append(text)
    return out[:max_items] if max_items else out


def as_float(value: Any, default: float = 0.0, lo: float | None = None, hi: float | None = None) -> float:
    try:
        if isinstance(value, str):
            match = re.search(r"-?\d+(?:\.\d+)?", value)
            number = float(match.group(0)) if match else default
        else:
            number = float(value)
    except (TypeError, ValueError):
        number = default
    if number != number:  # NaN
        number = default
    if lo is not None:
        number = max(lo, number)
    if hi is not None:
        number = min(hi, number)
    return number


def as_int(value: Any, default: int = 0, lo: int | None = None, hi: int | None = None) -> int:
    return int(round(as_float(value, default, lo, hi)))
