"""Language-model layer: Ollama and OpenAI-compatible protocols, routing, caching, usage."""

from __future__ import annotations

import json

import httpx
from django.test import TestCase

from apps.core.llm import LLMError, LLMUnavailable, get_llm, reset_providers
from apps.core.llm.client import LLMClient
from apps.core.llm.providers import OllamaProvider, OpenAICompatProvider
from apps.core.models import LLMCacheEntry, LLMUsage

from .helpers import FakeLLMMixin, mock_client


def _ollama_chat(content: str, model: str = "llama3"):
    return {"model": model, "message": {"role": "assistant", "content": content}, "prompt_eval_count": 12, "eval_count": 7}


class OllamaProtocolTests(TestCase):
    def test_chat_sends_options_and_parses_reply(self):
        seen = {}

        def chat(request):
            seen.update(json.loads(request.content))
            return httpx.Response(200, json=_ollama_chat('{"ok": true}'))

        provider = OllamaProvider("http://ollama:11434", num_ctx=4096, client=mock_client({"/api/chat": chat}))
        result = provider.chat([{"role": "user", "content": "hi"}], model="llama3", temperature=0.1, max_tokens=50, json_mode=True, seed=7)
        self.assertEqual(result.text, '{"ok": true}')
        self.assertEqual((result.prompt_tokens, result.completion_tokens), (12, 7))
        self.assertEqual(seen["format"], "json")
        self.assertFalse(seen["stream"])
        self.assertEqual(seen["options"], {"temperature": 0.1, "num_predict": 50, "num_ctx": 4096, "seed": 7})

    def test_schema_falls_back_to_json_mode_on_old_servers(self):
        formats = []

        def chat(request):
            body = json.loads(request.content)
            formats.append(body.get("format"))
            if isinstance(body.get("format"), dict):
                return httpx.Response(400, text="invalid format")
            return httpx.Response(200, json=_ollama_chat("{}"))

        provider = OllamaProvider("http://ollama:11434", client=mock_client({"/api/chat": chat}))
        provider.chat([{"role": "user", "content": "x"}], model="llama3", json_schema={"type": "object"})
        self.assertEqual(formats, [{"type": "object"}, "json"])
        provider.chat([{"role": "user", "content": "x"}], model="llama3", json_schema={"type": "object"})
        self.assertEqual(formats[-1], "json")  # remembered

    def test_missing_model_gives_pull_hint(self):
        provider = OllamaProvider("http://ollama:11434", client=mock_client({"/api/chat": httpx.Response(404, text='model "llama3" not found')}))
        with self.assertRaisesRegex(LLMError, "ollama pull llama3"):
            provider.chat([{"role": "user", "content": "x"}], model="llama3")

    def test_connection_error_is_unavailable(self):
        def boom(request):
            raise httpx.ConnectError("refused", request=request)

        provider = OllamaProvider("http://ollama:11434", client=mock_client({"/api/": boom}))
        with self.assertRaisesRegex(LLMUnavailable, "ollama serve"):
            provider.chat([{"role": "user", "content": "x"}], model="llama3")

    def test_embeddings_new_and_legacy_endpoints(self):
        provider = OllamaProvider("http://ollama:11434", client=mock_client({"/api/embed": {"embeddings": [[0.1, 0.2], [0.3, 0.4]]}}))
        self.assertEqual(provider.embed(["a", "b"], model="nomic-embed-text"), [[0.1, 0.2], [0.3, 0.4]])

        legacy = OllamaProvider(
            "http://ollama:11434",
            client=mock_client({"/api/embeddings": {"embedding": [1.0, 0.0]}, "/api/embed": httpx.Response(404, text="404 page not found")}),
        )
        self.assertEqual(legacy.embed(["a", "b"], model="nomic-embed-text"), [[1.0, 0.0], [1.0, 0.0]])

    def test_embedding_model_not_installed(self):
        provider = OllamaProvider("http://ollama:11434", client=mock_client({"/api/embed": httpx.Response(404, text='model "nomic-embed-text" not found, try pulling it first')}))
        with self.assertRaisesRegex(LLMError, "ollama pull nomic-embed-text"):
            provider.embed(["a"], model="nomic-embed-text")

    def test_health_lists_models(self):
        provider = OllamaProvider(
            "http://ollama:11434",
            client=mock_client(
                {
                    "/api/version": {"version": "0.12.3"},
                    "/api/tags": {"models": [{"name": "llama3:latest", "size": 4_700_000_000, "details": {"parameter_size": "8.0B", "quantization_level": "Q4_0", "family": "llama"}}]},
                }
            ),
        )
        health = provider.health()
        self.assertTrue(health["ok"])
        self.assertIn("0.12.3", health["detail"])
        self.assertEqual(health["models"][0]["name"], "llama3:latest")
        self.assertEqual(health["models"][0]["parameters"], "8.0B")


class OpenAICompatTests(TestCase):
    def test_chat_with_json_mode_and_usage(self):
        seen = {}

        def chat(request):
            seen["auth"] = request.headers.get("authorization")
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={"model": "llama-3.3-70b-versatile", "choices": [{"message": {"content": "{}"}}], "usage": {"prompt_tokens": 30, "completion_tokens": 4}})

        provider = OpenAICompatProvider("https://api.groq.com/openai/v1", "gsk_test", client=mock_client({"/chat/completions": chat}))
        result = provider.chat([{"role": "user", "content": "x"}], model="llama-3.3-70b-versatile", json_mode=True)
        self.assertEqual(seen["auth"], "Bearer gsk_test")
        self.assertEqual(seen["body"]["response_format"], {"type": "json_object"})
        self.assertEqual(result.prompt_tokens, 30)

    def test_response_format_fallback(self):
        calls = []

        def chat(request):
            body = json.loads(request.content)
            calls.append("response_format" in body)
            if "response_format" in body:
                return httpx.Response(400, json={"error": "response_format not supported"})
            return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

        provider = OpenAICompatProvider("https://example.org/v1", "k", client=mock_client({"/chat/completions": chat}))
        provider.chat([{"role": "user", "content": "x"}], model="m", json_mode=True)
        self.assertEqual(calls, [True, False])

    def test_bad_key_is_unavailable(self):
        provider = OpenAICompatProvider("https://example.org/v1", "bad", client=mock_client({"/chat/completions": httpx.Response(401, text="unauthorized")}))
        with self.assertRaises(LLMUnavailable):
            provider.chat([{"role": "user", "content": "x"}], model="m")

    def test_missing_key_for_remote_api(self):
        provider = OpenAICompatProvider("https://api.groq.com/openai/v1", "")
        with self.assertRaises(LLMUnavailable):
            provider.chat([{"role": "user", "content": "x"}], model="m")

    def test_embeddings_sorted_by_index(self):
        provider = OpenAICompatProvider(
            "https://example.org/v1", "k", client=mock_client({"/embeddings": {"data": [{"index": 1, "embedding": [2.0]}, {"index": 0, "embedding": [1.0]}]}})
        )
        self.assertEqual(provider.embed(["a", "b"], model="e"), [[1.0], [2.0]])


class ClientRoutingTests(TestCase):
    def tearDown(self):
        reset_providers()

    def _conf(self, **extra):
        from django.conf import settings

        return {**settings.BARAZA, **extra}

    def test_default_routes_to_local_llama3(self):
        client = LLMClient(config=self._conf(LLM_PROVIDER="ollama", OLLAMA_MODEL="llama3", OLLAMA_FAST_MODEL="", HYBRID_SMART_TIER=False))
        provider, model = client.route("smart")
        self.assertEqual((provider.name, model), ("ollama", "llama3"))
        self.assertEqual(client.route("fast")[1], "llama3")
        self.assertTrue(client.describe()["is_local"])

    def test_fast_model_override(self):
        client = LLMClient(config=self._conf(LLM_PROVIDER="ollama", OLLAMA_MODEL="llama3", OLLAMA_FAST_MODEL="llama3.2:3b"))
        self.assertEqual(client.route("fast")[1], "llama3.2:3b")

    def test_hybrid_sends_smart_tier_to_hosted_api(self):
        client = LLMClient(config=self._conf(LLM_PROVIDER="ollama", HYBRID_SMART_TIER=True, OPENAI_API_KEY="gsk_x", OPENAI_MODEL="llama-3.3-70b-versatile"))
        self.assertEqual(client.route("smart")[0].name, "openai")
        self.assertEqual(client.route("fast")[0].name, "ollama")

    def test_hybrid_without_key_stays_local(self):
        client = LLMClient(config=self._conf(LLM_PROVIDER="ollama", HYBRID_SMART_TIER=True, OPENAI_API_KEY=""))
        self.assertEqual(client.route("smart")[0].name, "ollama")

    def test_none_provider_is_unavailable(self):
        client = LLMClient(config=self._conf(LLM_PROVIDER="none"))
        self.assertFalse(client.available())
        with self.assertRaises(LLMUnavailable):
            client.complete("s", "u")


class ClientCacheAndUsageTests(FakeLLMMixin, TestCase):
    def test_identical_calls_are_cached_and_counted(self):
        llm = get_llm()
        first = llm.chat([{"role": "user", "content": "hello"}], purpose="general", temperature=0.0)
        second = llm.chat([{"role": "user", "content": "hello"}], purpose="general", temperature=0.0)
        self.assertFalse(first.cached)
        self.assertTrue(second.cached)
        self.assertEqual(LLMCacheEntry.objects.count(), 1)
        usage = LLMUsage.objects.get(purpose="general")
        self.assertEqual((usage.calls, usage.cached_calls), (2, 1))

    def test_hot_temperature_is_not_cached(self):
        llm = get_llm()
        llm.chat([{"role": "user", "content": "hello"}], temperature=0.9)
        self.assertEqual(LLMCacheEntry.objects.count(), 0)

    def test_json_repairs_and_retries(self):
        llm = get_llm()
        replies = iter(["not json at all", '```json\n{"fixed": true,}\n```'])

        class Flaky:
            name = "flaky"

            def chat(self, messages, **kwargs):
                from apps.core.llm.providers import ChatResult

                return ChatResult(text=next(replies), model="m", provider="flaky")

        llm.route = lambda tier="smart": (Flaky(), "m")
        self.assertEqual(llm.json(system="s", user="u", purpose="test", use_cache=False), {"fixed": True})

    def test_json_gives_up_with_clear_error(self):
        llm = get_llm()

        class Broken:
            name = "broken"

            def chat(self, messages, **kwargs):
                from apps.core.llm.providers import ChatResult

                return ChatResult(text="I cannot do that.", model="m", provider="broken")

        llm.route = lambda tier="smart": (Broken(), "m")
        with self.assertRaisesRegex(LLMError, "valid JSON"):
            llm.json(system="s", user="u", purpose="test", use_cache=False)

    def test_fake_embeddings_are_normalised(self):
        vectors = get_llm().embed(["mobile money tax", "bread VAT"])
        self.assertEqual(len(vectors), 2)
        self.assertAlmostEqual(sum(v * v for v in vectors[0]), 1.0, places=4)
