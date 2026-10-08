"""
The single entry point for language-model calls.

``LLMClient`` routes each call to a provider/model by *tier*:

* ``smart`` – briefs, research synthesis, reports (quality matters).
* ``fast``  – thousands of agent posts and classifications (speed matters).

It adds response caching, a concurrency limit (Ollama processes requests
one-by-one on most laptops), JSON repair with retries, embeddings with
graceful failure, and per-project usage/cost accounting.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from typing import Any

from django.db import IntegrityError, close_old_connections
from django.db.models import F

from ..config import get_config
from .jsonfix import JSONExtractionError, extract_json
from .providers import (
    BaseProvider,
    ChatResult,
    LLMError,
    LLMUnavailable,
    NoneProvider,
    OllamaProvider,
    OpenAICompatProvider,
)

logger = logging.getLogger("baraza.llm")

_providers: dict[tuple, BaseProvider] = {}
_providers_lock = threading.Lock()
_semaphores: dict[str, threading.BoundedSemaphore] = {}
_health_cache: dict[tuple, tuple[float, dict]] = {}
_embed_disabled_until: dict[tuple, float] = {}
HEALTH_TTL = 20.0


def _provider(kind: str, conf: dict) -> BaseProvider:
    if kind == "ollama":
        key = ("ollama", conf["OLLAMA_BASE_URL"], conf["LLM_TIMEOUT"], conf["OLLAMA_NUM_CTX"])
    elif kind == "openai":
        key = ("openai", conf["OPENAI_BASE_URL"], conf["OPENAI_API_KEY"], conf["LLM_TIMEOUT"])
    else:
        key = (kind,)
    with _providers_lock:
        if key not in _providers:
            if kind == "ollama":
                _providers[key] = OllamaProvider(
                    conf["OLLAMA_BASE_URL"], timeout=conf["LLM_TIMEOUT"], num_ctx=conf["OLLAMA_NUM_CTX"]
                )
            elif kind == "openai":
                _providers[key] = OpenAICompatProvider(
                    conf["OPENAI_BASE_URL"], conf["OPENAI_API_KEY"], timeout=conf["LLM_TIMEOUT"]
                )
            elif kind == "fake":
                from .fake import FakeProvider

                _providers[key] = FakeProvider()
            else:
                _providers[key] = NoneProvider()
        return _providers[key]


def _semaphore(name: str, size: int) -> threading.BoundedSemaphore:
    key = f"{name}:{size}"
    with _providers_lock:
        if key not in _semaphores:
            _semaphores[key] = threading.BoundedSemaphore(max(1, size))
        return _semaphores[key]


def reset_providers() -> None:
    """Forget cached provider instances (after settings change)."""
    with _providers_lock:
        _providers.clear()
        _health_cache.clear()
        _embed_disabled_until.clear()


class LLMClient:
    def __init__(self, project_id: Any = None, config: dict | None = None):
        self.project_id = project_id
        self.conf = config or get_config()

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------
    def route(self, tier: str = "smart") -> tuple[BaseProvider, str]:
        conf = self.conf
        kind = conf.get("LLM_PROVIDER", "ollama")
        if tier == "smart" and conf.get("HYBRID_SMART_TIER") and conf.get("OPENAI_API_KEY"):
            kind = "openai"
        provider = _provider(kind, conf)
        if kind == "ollama":
            model = conf["OLLAMA_MODEL"]
            if tier == "fast" and conf.get("OLLAMA_FAST_MODEL"):
                model = conf["OLLAMA_FAST_MODEL"]
        elif kind == "openai":
            model = conf["OPENAI_MODEL"]
            if tier == "fast" and conf.get("OPENAI_FAST_MODEL"):
                model = conf["OPENAI_FAST_MODEL"]
        elif kind == "fake":
            model = "fake-demo"
        else:
            model = "none"
        return provider, model

    def describe(self) -> dict:
        smart_provider, smart_model = self.route("smart")
        fast_provider, fast_model = self.route("fast")
        return {
            "smart": f"{smart_provider.name}:{smart_model}",
            "fast": f"{fast_provider.name}:{fast_model}",
            "provider": self.conf.get("LLM_PROVIDER"),
            "is_local": self.conf.get("LLM_PROVIDER") == "ollama" and not self.conf.get("HYBRID_SMART_TIER"),
        }

    def health(self, tier: str = "smart", force: bool = False) -> dict:
        provider, model = self.route(tier)
        key = (provider.name, id(provider), model)
        now = time.monotonic()
        cached = _health_cache.get(key)
        if cached and not force and now - cached[0] < HEALTH_TTL:
            return cached[1]
        info = provider.health()
        info = dict(info)
        info["provider"] = provider.name
        info["model"] = model
        if info.get("ok") and provider.name == "ollama":
            names = {m["name"] for m in info.get("models", [])}
            wanted = {model, f"{model}:latest"}
            info["model_installed"] = bool(names & wanted) or any(n.split(":")[0] == model for n in names)
            if not info["model_installed"]:
                info["ok"] = False
                info["detail"] = f"Ollama is running but model '{model}' is not installed. Run: ollama pull {model}"
        _health_cache[key] = (now, info)
        return info

    def available(self, tier: str = "smart") -> bool:
        return bool(self.health(tier).get("ok"))

    # ------------------------------------------------------------------
    # Chat
    # ------------------------------------------------------------------
    def chat(
        self,
        messages: list[dict],
        *,
        tier: str = "smart",
        purpose: str = "general",
        temperature: float = 0.4,
        max_tokens: int = 800,
        json_mode: bool = False,
        json_schema: dict | None = None,
        seed: int | None = None,
        use_cache: bool = True,
    ) -> ChatResult:
        provider, model = self.route(tier)
        cacheable = use_cache and self.conf.get("LLM_CACHE", True) and (temperature <= 0.5 or seed is not None)
        cache_key = None
        if cacheable:
            cache_key = hashlib.sha256(
                json.dumps(
                    [provider.name, model, messages, round(temperature, 3), max_tokens, json_mode, json_schema, seed],
                    sort_keys=True,
                    ensure_ascii=False,
                ).encode("utf-8")
            ).hexdigest()
            hit = self._cache_get(cache_key)
            if hit is not None:
                self._record(purpose, provider.name, model, hit, cached=True)
                hit.raw["cache_key"] = cache_key
                return hit

        sem = _semaphore(provider.name, int(self.conf.get("LLM_CONCURRENCY", 2)))
        with sem:
            try:
                result = provider.chat(
                    messages,
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    json_mode=json_mode,
                    json_schema=json_schema,
                    seed=seed,
                    purpose=purpose,
                )
            except LLMError:
                self._record(purpose, provider.name, model, None, error=True)
                _health_cache.clear()
                raise
        self._record(purpose, provider.name, model, result)
        if cache_key and result.text.strip():
            self._cache_put(cache_key, model, result)
            result.raw["cache_key"] = cache_key
        return result

    def complete(self, system: str, user: str, **kwargs) -> str:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        return self.chat(messages, **kwargs).text.strip()

    def json(
        self,
        *,
        system: str,
        user: str,
        purpose: str,
        tier: str = "smart",
        schema: dict | None = None,
        expect: type = dict,
        temperature: float = 0.2,
        max_tokens: int = 1200,
        retries: int = 1,
        seed: int | None = None,
        use_cache: bool = True,
    ) -> Any:
        """Ask for JSON and return the parsed value (with repair and one retry)."""
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        last_text = ""
        for attempt in range(retries + 1):
            result = self.chat(
                messages,
                tier=tier,
                purpose=purpose,
                temperature=min(1.0, temperature + 0.15 * attempt),
                max_tokens=max_tokens,
                json_mode=True,
                json_schema=schema,
                seed=seed,
                use_cache=use_cache,
            )
            last_text = result.text
            try:
                return extract_json(result.text, expect)
            except JSONExtractionError:
                self._cache_delete(result.raw.get("cache_key"))
                logger.info("Invalid JSON from %s for %s (attempt %d)", result.model, purpose, attempt + 1)
                messages = messages + [
                    {"role": "assistant", "content": result.text[:3000]},
                    {
                        "role": "user",
                        "content": "That reply was not valid JSON. Reply again with ONLY the JSON value, "
                        "no explanations, no code fences.",
                    },
                ]
        raise LLMError(f"The model did not return valid JSON for '{purpose}'. Last output: {last_text[:200]!r}")

    # ------------------------------------------------------------------
    # Embeddings (optional; callers fall back to BM25 when None)
    # ------------------------------------------------------------------
    def embed_model(self) -> tuple[BaseProvider, str] | None:
        conf = self.conf
        kind = conf.get("LLM_PROVIDER")
        if kind == "ollama" and conf.get("OLLAMA_EMBED_MODEL"):
            return _provider("ollama", conf), conf["OLLAMA_EMBED_MODEL"]
        if kind == "openai" and conf.get("OPENAI_EMBED_MODEL"):
            return _provider("openai", conf), conf["OPENAI_EMBED_MODEL"]
        if kind == "fake":
            return _provider("fake", conf), "fake-embed"
        return None

    def embed(self, texts: list[str], batch_size: int = 32) -> list[list[float]] | None:
        target = self.embed_model()
        if not target or not texts:
            return None
        provider, model = target
        key = (provider.name, model)
        if _embed_disabled_until.get(key, 0) > time.monotonic():
            return None
        vectors: list[list[float]] = []
        try:
            for i in range(0, len(texts), batch_size):
                batch = [t[:2000] for t in texts[i : i + batch_size]]
                vectors.extend(provider.embed(batch, model=model))
        except LLMError as exc:
            logger.warning("Embeddings unavailable (%s); falling back to keyword retrieval.", exc)
            _embed_disabled_until[key] = time.monotonic() + 300
            return None
        if len(vectors) != len(texts) or not all(vectors):
            return None
        return vectors

    # ------------------------------------------------------------------
    # Cache + usage bookkeeping
    # ------------------------------------------------------------------
    def _cache_get(self, key: str) -> ChatResult | None:
        from ..models import LLMCacheEntry

        try:
            entry = LLMCacheEntry.objects.filter(key=key).first()
            if entry is None:
                return None
            LLMCacheEntry.objects.filter(pk=entry.pk).update(hits=F("hits") + 1)
        except Exception:  # pragma: no cover - cache must never break a call
            return None
        return ChatResult(
            text=entry.response,
            model=entry.model,
            provider="cache",
            prompt_tokens=entry.prompt_tokens,
            completion_tokens=entry.completion_tokens,
            cached=True,
        )

    def _cache_put(self, key: str, model: str, result: ChatResult) -> None:
        from ..models import LLMCacheEntry

        try:
            LLMCacheEntry.objects.update_or_create(
                key=key,
                defaults={
                    "model": model,
                    "response": result.text,
                    "prompt_tokens": result.prompt_tokens,
                    "completion_tokens": result.completion_tokens,
                },
            )
        except Exception:  # pragma: no cover
            logger.debug("cache write failed", exc_info=True)

    def _cache_delete(self, key: str | None) -> None:
        if not key:
            return
        from ..models import LLMCacheEntry

        try:
            LLMCacheEntry.objects.filter(key=key).delete()
        except Exception:  # pragma: no cover
            pass

    def _record(self, purpose: str, provider: str, model: str, result: ChatResult | None, *, cached=False, error=False):
        from ..models import LLMUsage

        prompt = result.prompt_tokens if result and not cached else 0
        completion = result.completion_tokens if result and not cached else 0
        cost = 0.0
        if provider == "openai" and not cached:
            cost = prompt / 1e6 * float(self.conf.get("LLM_COST_PER_1M_INPUT_USD") or 0) + completion / 1e6 * float(
                self.conf.get("LLM_COST_PER_1M_OUTPUT_USD") or 0
            )
        updates = {
            "calls": F("calls") + 1,
            "cached_calls": F("cached_calls") + (1 if cached else 0),
            "errors": F("errors") + (1 if error else 0),
            "prompt_tokens": F("prompt_tokens") + prompt,
            "completion_tokens": F("completion_tokens") + completion,
            "seconds": F("seconds") + (result.seconds if result and not cached else 0.0),
            "cost_usd": F("cost_usd") + cost,
        }
        lookup = {"project_id": self.project_id, "purpose": purpose[:64], "provider": provider, "model": model[:200]}
        try:
            for _ in range(2):
                if LLMUsage.objects.filter(**lookup).update(**updates):
                    return
                try:
                    LLMUsage.objects.create(
                        **lookup,
                        calls=1,
                        cached_calls=1 if cached else 0,
                        errors=1 if error else 0,
                        prompt_tokens=prompt,
                        completion_tokens=completion,
                        seconds=result.seconds if result and not cached else 0.0,
                        cost_usd=cost,
                    )
                    return
                except IntegrityError:
                    continue
        except Exception:  # pragma: no cover - accounting must never break a call
            logger.debug("usage accounting failed", exc_info=True)


def get_llm(project=None) -> LLMClient:
    project_id = getattr(project, "pk", project)
    return LLMClient(project_id=project_id)


def thread_safe(func):
    """Decorator for functions run in worker threads: close DB connections afterwards."""

    def wrapper(*args, **kwargs):
        close_old_connections()
        try:
            return func(*args, **kwargs)
        finally:
            from django.db import connection

            connection.close()

    wrapper.__name__ = getattr(func, "__name__", "wrapped")
    return wrapper


__all__ = ["LLMClient", "LLMError", "LLMUnavailable", "ChatResult", "get_llm", "reset_providers", "thread_safe"]
