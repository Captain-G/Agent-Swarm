"""
Runtime configuration.

Defaults come from ``settings.BARAZA`` (environment variables). Values saved
on the Settings page are stored in :class:`SiteConfig` and take precedence.
A short in-process cache keeps lookups cheap on hot paths (LLM calls).
"""

from __future__ import annotations

import threading
import time
from typing import Any

from django.conf import settings

_CACHE_TTL_SECONDS = 5.0
_lock = threading.Lock()
_cache: dict[str, Any] | None = None
_cache_time = 0.0

# Keys that hold secrets: masked in the UI and never logged.
SECRET_KEYS = {
    "OPENAI_API_KEY",
    "YOUTUBE_API_KEY",
    "REDDIT_CLIENT_SECRET",
    "BLUESKY_APP_PASSWORD",
    "JINA_API_KEY",
    "TAVILY_API_KEY",
    "BRAVE_API_KEY",
    "SERPER_API_KEY",
    "APIFY_TOKEN",
    "TWITTERAPI_IO_KEY",
    "X_BEARER_TOKEN",
}

BOOL_KEYS = {
    "HYBRID_SMART_TIER",
    "LLM_CACHE",
    "REQUIRE_LOGIN",
    "RESPECT_ROBOTS_TXT",
    "ANONYMIZE_SOCIAL_AUTHORS",
}

INT_KEYS = {"LLM_CONCURRENCY", "LLM_TIMEOUT", "OLLAMA_NUM_CTX", "MAX_CONCURRENT_JOBS", "HTTP_TIMEOUT"}
FLOAT_KEYS = {"LLM_COST_PER_1M_INPUT_USD", "LLM_COST_PER_1M_OUTPUT_USD"}


def _coerce(key: str, value: Any) -> Any:
    if key in BOOL_KEYS:
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}
    if key in INT_KEYS:
        try:
            return int(value)
        except (TypeError, ValueError):
            return settings.BARAZA.get(key)
    if key in FLOAT_KEYS:
        try:
            return float(value)
        except (TypeError, ValueError):
            return settings.BARAZA.get(key)
    return value


def get_config(refresh: bool = False) -> dict[str, Any]:
    """Return the merged configuration dictionary."""
    global _cache, _cache_time
    now = time.monotonic()
    with _lock:
        if not refresh and _cache is not None and now - _cache_time < _CACHE_TTL_SECONDS:
            return _cache
    merged = dict(settings.BARAZA)
    try:
        from .models import SiteConfig

        overrides = SiteConfig.objects.filter(pk=1).values_list("values", flat=True).first() or {}
    except Exception:  # database not migrated yet, etc.
        overrides = {}
    for key, value in overrides.items():
        if key in merged and value not in (None, ""):
            merged[key] = _coerce(key, value)
    with _lock:
        _cache = merged
        _cache_time = now
    return merged


def cfg(key: str, default: Any = None) -> Any:
    return get_config().get(key, default)


def save_overrides(values: dict[str, Any]) -> None:
    """Persist UI overrides. Empty strings clear an override (fall back to env)."""
    from .models import SiteConfig

    site = SiteConfig.load()
    current = dict(site.values or {})
    for key, value in values.items():
        if key not in settings.BARAZA:
            continue
        if value is None or value == "":
            current.pop(key, None)
        else:
            current[key] = value
    site.values = current
    site.save()
    get_config(refresh=True)


def mask(value: str | None) -> str:
    if not value:
        return ""
    value = str(value)
    if len(value) <= 8:
        return "•" * len(value)
    return f"{value[:4]}{'•' * 8}{value[-4:]}"
