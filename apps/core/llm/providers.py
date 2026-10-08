"""
LLM providers.

* :class:`OllamaProvider` talks to a local Ollama server (free). This is the
  default and works with ``llama3`` out of the box.
* :class:`OpenAICompatProvider` talks to any OpenAI-compatible API: Groq,
  OpenRouter, Together, DeepSeek, Google Gemini's OpenAI endpoint, LM Studio,
  vLLM, llama.cpp server or OpenAI itself.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger("baraza.llm")


class LLMError(Exception):
    """A call to the language model failed."""


class LLMUnavailable(LLMError):
    """The provider cannot be reached or is not configured."""


@dataclass
class ChatResult:
    text: str
    model: str
    provider: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    seconds: float = 0.0
    cached: bool = False
    raw: dict = field(default_factory=dict)


class BaseProvider:
    name = "base"

    def chat(
        self,
        messages: list[dict],
        *,
        model: str,
        temperature: float = 0.4,
        max_tokens: int = 800,
        json_mode: bool = False,
        json_schema: dict | None = None,
        seed: int | None = None,
        purpose: str = "general",
    ) -> ChatResult:
        raise NotImplementedError

    def embed(self, texts: list[str], *, model: str) -> list[list[float]]:
        raise NotImplementedError

    def list_models(self) -> list[dict]:
        return []

    def health(self) -> dict:
        return {"ok": False, "detail": "not implemented"}


def _retry_after_seconds(response: httpx.Response, attempt: int) -> float:
    header = response.headers.get("retry-after")
    if header:
        try:
            return min(60.0, float(header))
        except ValueError:
            pass
    return min(30.0, 2.0 * (2**attempt))


class OllamaProvider(BaseProvider):
    name = "ollama"

    def __init__(self, base_url: str, *, timeout: float = 240, num_ctx: int = 8192, client: httpx.Client | None = None):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.num_ctx = num_ctx
        self._client = client
        self._schema_supported = True
        self._embed_endpoint = "/api/embed"
        self._lock = threading.Lock()

    def _http(self) -> httpx.Client:
        if self._client is None:
            # trust_env=False: never route local Ollama traffic through HTTP proxies.
            self._client = httpx.Client(timeout=httpx.Timeout(self.timeout, connect=10.0), trust_env=False)
        return self._client

    def _post(self, path: str, payload: dict) -> httpx.Response:
        try:
            return self._http().post(f"{self.base_url}{path}", json=payload)
        except httpx.ConnectError as exc:
            raise LLMUnavailable(
                f"Cannot reach Ollama at {self.base_url}. Start it with `ollama serve` "
                f"(or open the Ollama app) and make sure the model is pulled."
            ) from exc
        except httpx.TimeoutException as exc:
            raise LLMError(f"Ollama timed out after {self.timeout}s. Try a smaller model or raise LLM_TIMEOUT.") from exc

    def chat(self, messages, *, model, temperature=0.4, max_tokens=800, json_mode=False, json_schema=None, seed=None, purpose="general"):
        options: dict[str, Any] = {"temperature": temperature, "num_predict": max_tokens, "num_ctx": self.num_ctx}
        if seed is not None:
            options["seed"] = int(seed)
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": options,
            "keep_alive": "30m",
        }
        if json_schema and self._schema_supported:
            payload["format"] = json_schema
        elif json_mode or json_schema:
            payload["format"] = "json"
        start = time.monotonic()
        response = self._post("/api/chat", payload)
        if response.status_code == 400 and isinstance(payload.get("format"), dict):
            # Older Ollama versions only accept format="json".
            self._schema_supported = False
            payload["format"] = "json"
            response = self._post("/api/chat", payload)
        if response.status_code == 404:
            raise LLMError(f"Model '{model}' is not installed in Ollama. Run: ollama pull {model}")
        if response.status_code >= 400:
            raise LLMError(f"Ollama error {response.status_code}: {response.text[:300]}")
        data = response.json()
        text = (data.get("message") or {}).get("content", "")
        return ChatResult(
            text=text,
            model=data.get("model", model),
            provider=self.name,
            prompt_tokens=int(data.get("prompt_eval_count") or 0),
            completion_tokens=int(data.get("eval_count") or 0),
            seconds=time.monotonic() - start,
        )

    def embed(self, texts, *, model):
        if not texts:
            return []
        if self._embed_endpoint == "/api/embed":
            response = self._post("/api/embed", {"model": model, "input": texts, "keep_alive": "30m"})
            if response.status_code == 404 and "model" in response.text.lower():
                raise LLMError(f"Embedding model '{model}' is not installed. Run: ollama pull {model}")
            if response.status_code == 404:
                self._embed_endpoint = "/api/embeddings"  # very old Ollama without /api/embed
            elif response.status_code >= 400:
                raise LLMError(f"Ollama embedding error {response.status_code}: {response.text[:200]}")
            else:
                return response.json().get("embeddings") or []
        vectors = []
        for text in texts:
            response = self._post("/api/embeddings", {"model": model, "prompt": text})
            if response.status_code >= 400:
                raise LLMError(f"Ollama embedding error {response.status_code}: {response.text[:200]}")
            vectors.append(response.json().get("embedding") or [])
        return vectors

    def list_models(self):
        try:
            response = self._http().get(f"{self.base_url}/api/tags", timeout=5.0)
            response.raise_for_status()
        except (httpx.HTTPError, ValueError):
            return []
        models = []
        for item in response.json().get("models", []):
            details = item.get("details") or {}
            models.append(
                {
                    "name": item.get("name") or item.get("model"),
                    "size_gb": round((item.get("size") or 0) / 1e9, 2),
                    "parameters": details.get("parameter_size", ""),
                    "quantization": details.get("quantization_level", ""),
                    "family": details.get("family", ""),
                }
            )
        return models

    def health(self):
        try:
            response = self._http().get(f"{self.base_url}/api/version", timeout=5.0)
            response.raise_for_status()
            version = response.json().get("version", "?")
        except httpx.HTTPError as exc:
            return {"ok": False, "detail": f"Ollama not reachable at {self.base_url} ({exc.__class__.__name__})"}
        return {"ok": True, "detail": f"Ollama {version}", "models": self.list_models()}


class OpenAICompatProvider(BaseProvider):
    name = "openai"

    def __init__(self, base_url: str, api_key: str, *, timeout: float = 240, client: httpx.Client | None = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self._client = client
        self._response_format_supported = True

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if "openrouter.ai" in self.base_url:
            headers["HTTP-Referer"] = "https://github.com/captain-g/agent-swarm"
            headers["X-Title"] = "Baraza"
        return headers

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=httpx.Timeout(self.timeout, connect=15.0))
        return self._client

    def _request(self, method: str, path: str, payload: dict | None = None, retries: int = 4) -> httpx.Response:
        if not self.base_url:
            raise LLMUnavailable("OPENAI_BASE_URL is not configured.")
        last: httpx.Response | None = None
        for attempt in range(retries + 1):
            try:
                response = self._http().request(method, f"{self.base_url}{path}", json=payload, headers=self._headers())
            except httpx.ConnectError as exc:
                raise LLMUnavailable(f"Cannot reach {self.base_url}: {exc}") from exc
            except httpx.TimeoutException as exc:
                if attempt >= retries:
                    raise LLMError(f"Request to {self.base_url} timed out") from exc
                time.sleep(2.0 * (attempt + 1))
                continue
            if response.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                wait = _retry_after_seconds(response, attempt)
                logger.warning("LLM API returned %s; retrying in %.1fs", response.status_code, wait)
                time.sleep(wait)
                last = response
                continue
            return response
        assert last is not None
        return last

    def chat(self, messages, *, model, temperature=0.4, max_tokens=800, json_mode=False, json_schema=None, seed=None, purpose="general"):
        if not self.api_key and "localhost" not in self.base_url and "127.0.0.1" not in self.base_url:
            raise LLMUnavailable("OPENAI_API_KEY is not set for the OpenAI-compatible provider.")
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if seed is not None:
            payload["seed"] = int(seed)
        if (json_mode or json_schema) and self._response_format_supported:
            payload["response_format"] = {"type": "json_object"}
        start = time.monotonic()
        response = self._request("POST", "/chat/completions", payload)
        if response.status_code == 400 and "response_format" in payload:
            self._response_format_supported = False
            payload.pop("response_format")
            response = self._request("POST", "/chat/completions", payload)
        if response.status_code in (401, 403):
            raise LLMUnavailable(f"The API rejected the key ({response.status_code}). Check OPENAI_API_KEY.")
        if response.status_code >= 400:
            raise LLMError(f"LLM API error {response.status_code}: {response.text[:300]}")
        data = response.json()
        choices = data.get("choices") or []
        text = ((choices[0].get("message") or {}).get("content") if choices else "") or ""
        usage = data.get("usage") or {}
        return ChatResult(
            text=text,
            model=data.get("model", model),
            provider=self.name,
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            seconds=time.monotonic() - start,
        )

    def embed(self, texts, *, model):
        if not texts:
            return []
        response = self._request("POST", "/embeddings", {"model": model, "input": texts})
        if response.status_code >= 400:
            raise LLMError(f"Embedding API error {response.status_code}: {response.text[:200]}")
        data = sorted(response.json().get("data", []), key=lambda d: d.get("index", 0))
        return [item.get("embedding") or [] for item in data]

    def list_models(self):
        try:
            response = self._request("GET", "/models", retries=0)
            if response.status_code >= 400:
                return []
            return [{"name": m.get("id")} for m in response.json().get("data", [])][:200]
        except LLMError:
            return []

    def health(self):
        if not self.api_key and "localhost" not in self.base_url:
            return {"ok": False, "detail": "No API key configured"}
        try:
            response = self._request("GET", "/models", retries=0)
        except LLMError as exc:
            return {"ok": False, "detail": str(exc)}
        if response.status_code >= 400:
            return {"ok": False, "detail": f"HTTP {response.status_code} from {self.base_url}"}
        return {"ok": True, "detail": f"Connected to {self.base_url}", "models": self.list_models()[:30]}


class NoneProvider(BaseProvider):
    name = "none"

    def chat(self, messages, **kwargs):
        raise LLMUnavailable("No language model configured (LLM_PROVIDER=none). Heuristic fallbacks are used.")

    def embed(self, texts, *, model):
        raise LLMUnavailable("No embedding model configured.")

    def health(self):
        return {"ok": False, "detail": "Language model disabled (heuristic mode)"}
