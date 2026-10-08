"""Check the configured language model end to end."""

from django.core.management.base import BaseCommand

from apps.core.llm import LLMError, get_llm


class Command(BaseCommand):
    help = "Check that the configured language model (e.g. Ollama llama3) is reachable and can generate JSON."

    def handle(self, *args, **opts):
        llm = get_llm()
        d = llm.describe()
        self.stdout.write(f"Smart tier: {d['smart']}   Fast tier: {d['fast']}")
        info = llm.health("smart", force=True)
        style = self.style.SUCCESS if info.get("ok") else self.style.ERROR
        self.stdout.write(style(f"Health: {info.get('detail')}"))
        for m in info.get("models", [])[:20]:
            self.stdout.write(f"  - {m.get('name')} {m.get('parameters', '')}")
        if not info.get("ok"):
            return
        try:
            data = llm.json(
                system="Reply with JSON only.",
                user='Return {"greeting": "a one-sentence greeting to a Kenyan policymaker", "ok": true}',
                purpose="check_llm",
                tier="fast",
                max_tokens=80,
                use_cache=False,
            )
            self.stdout.write(self.style.SUCCESS(f"JSON generation works: {data}"))
        except LLMError as exc:
            self.stdout.write(self.style.ERROR(f"Generation failed: {exc}"))
        vectors = llm.embed(["hello world"])
        self.stdout.write(f"Embeddings: {'available (' + str(len(vectors[0])) + ' dimensions)' if vectors else 'not available - keyword search will be used'}")
