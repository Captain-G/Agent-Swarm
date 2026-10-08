"""
A deterministic, offline stand-in for a language model.

Used for the automated tests and for "demo mode" (LLM_PROVIDER=fake) so the
whole pipeline can be explored without installing any model. It parses the
prompts it receives and returns well-formed answers for every purpose the
application uses. Its writing is deliberately plain - connect Ollama for real
quality.
"""

from __future__ import annotations

import hashlib
import json
import re

import numpy as np

from .providers import BaseProvider, ChatResult


def _between(text: str, start: str, end: str | None = None) -> str:
    i = text.find(start)
    if i < 0:
        return ""
    i += len(start)
    j = text.find(end, i) if end else -1
    return text[i:j if j >= 0 else None].strip()


def _numbered(text: str) -> list[tuple[int, str]]:
    return [(int(m.group(1)), m.group(2).strip()) for m in re.finditer(r"^\s*(\d+)\.\s+(.*)$", text, re.M)]


class FakeProvider(BaseProvider):
    name = "fake"

    def health(self) -> dict:
        return {"ok": True, "detail": "Demo mode (offline stand-in model)", "models": [{"name": "fake-demo"}]}

    def list_models(self) -> list[dict]:
        return [{"name": "fake-demo"}]

    def embed(self, texts, *, model):
        vectors = []
        for text in texts:
            vec = np.zeros(64, dtype=np.float32)
            for token in re.findall(r"[a-z]{3,}", text.lower()):
                h = int(hashlib.md5(token.encode()).hexdigest(), 16)
                vec[h % 64] += 1.0 if (h >> 7) & 1 else -1.0
            norm = float(np.linalg.norm(vec)) or 1.0
            vectors.append((vec / norm).tolist())
        return vectors

    def chat(self, messages, *, model, temperature=0.4, max_tokens=800, json_mode=False, json_schema=None, seed=None, purpose="general"):
        system = "\n".join(m["content"] for m in messages if m["role"] == "system")
        user = messages[-1]["content"] if messages else ""
        handler = getattr(self, f"_{purpose}", None)
        if handler:
            payload = handler(system, user, seed or 0)
        else:
            payload = {"ok": True, "greeting": "Habari! Demo mode is working."} if (json_mode or json_schema) else "OK"
        text = payload if isinstance(payload, str) else json.dumps(payload)
        return ChatResult(text=text, model="fake-demo", provider=self.name, prompt_tokens=len(user) // 4, completion_tokens=len(text) // 4, seconds=0.0)

    # ------------------------------------------------------------------
    def _brief(self, system, user, seed):
        from apps.core.geo.countries import COUNTRIES, get_country
        from apps.projects.brief import heuristic_brief

        request = _between(user, 'USER REQUEST:\n"""', '"""') or user
        country_name = _between(system, "analyst in ", ".")
        code = next((c.code for c in COUNTRIES.values() if c.name == country_name), "KE")
        hint = _between(user, "SUGGESTED TITLE:", "\n")
        brief = heuristic_brief(request, get_country(code), title_hint=hint)
        tags = brief.pop("hashtags", {})
        brief["hashtags_support"] = tags.get("support", [])
        brief["hashtags_oppose"] = tags.get("oppose", [])
        return brief

    def _classify_stance(self, system, user, seed):
        from apps.core.text.sentiment import sentiment

        posts = _numbered(_between(user, "POSTS:", "Return JSON"))
        labels = []
        for n, text in posts:
            s = sentiment(text)
            labels.append({"id": n, "stance": round(max(-1, min(1, s)), 2), "confidence": 0.6})
        return {"labels": labels}

    def _extract(self, system, user, seed):
        from apps.knowledge.extraction import heuristic_extract

        text = _between(user, 'TEXT', '"""\n\nExtract')
        text = text.split('"""', 1)[-1] if '"""' in text else text
        data = heuristic_extract(text)
        for e in data["entities"]:
            e.pop("mentions", None)
        for a in data["arguments"]:
            a.pop("chunk", None)
            a.pop("source", None)
        return {
            "entities": data["entities"],
            "relations": [{k: r[k] for k in ("source", "target", "type", "description")} for r in data["relations"][:8]],
            "arguments": data["arguments"],
            "facts": [f["fact"] for f in data["facts"]],
            "rumors": [],
        }

    def _arguments(self, system, user, seed):
        items = _numbered(_between(user, "ARGUMENTS:", "Return JSON"))
        if items:
            out = []
            for n, line in items:
                claim = re.sub(r"^\[\w+\]\s*", "", line)
                out.append({"id": n, "claim": claim[:300], "short": claim[:190]})
            return {"arguments": out}
        side = "support" if 'side' in user and '"support"' in user else "oppose"
        title = _between(user, "TOPIC:", "\n")
        return {
            "arguments": [
                {"claim": f"{title} will affect ordinary families' budgets.", "short": f"{title}: think about ordinary families.", "by": "citizens", "emotion": "fear" if side == "oppose" else "hope", "strength": 0.5},
                {"claim": f"{title} needs more transparency on how money is used.", "short": "Where will the money go?", "by": "civil society", "emotion": "disgust" if side == "oppose" else "trust", "strength": 0.5},
            ]
        }

    def _dossier_overview(self, system, user, seed):
        title = _between(user, "TOPIC:", "\n")
        brief = _between(user, "BRIEF:", "\n")
        evidence = _between(user, "EVIDENCE", "FACTS ALREADY EXTRACTED")
        from apps.core.text.textutils import sentences

        picked: list[str] = []
        for line in [line[line.find(")") + 1 :].strip() for line in evidence.splitlines() if line.startswith("[")][:4]:
            # Whole sentences only, skipping demo markers and lines cut off by the evidence limit.
            usable = [x for x in sentences(line) if 40 < len(x) < 300 and x[-1] in ".!?" and "NOT REAL DATA" not in x]
            picked.extend(usable[:1])
        facts = [line[2:] for line in _between(user, "FACTS ALREADY EXTRACTED:", "Write:").splitlines() if line.startswith("- ") and "(none)" not in line][:6]
        return {
            "summary": f"{brief} Evidence gathered so far shows mixed public reaction to {title}. " + " ".join(picked[:3]),
            "background": brief,
            "timeline": [],
            "key_facts": [{"fact": f, "sources": []} for f in facts],
            "narratives": ["cost of living", "trust in government", "fairness"],
            "uncertainties": ["How the policy will be implemented", "Who will bear most of the cost"],
        }

    def _dossier_stakeholders(self, system, user, seed):
        stakeholders = []
        for line in _between(user, "STAKEHOLDERS EXPECTED IN THE BRIEF:", "AFFECTED GROUPS").splitlines():
            m = re.match(r"- (.+?) \((.+?)\), expected position ([+-]?\d+(?:\.\d+)?)", line)
            if m:
                stakeholders.append({"name": m.group(1), "type": m.group(2), "position": float(m.group(3)), "influence": 0.6, "summary": f"{m.group(1)} has a stated position."})
        groups = []
        for line in _between(user, "AFFECTED GROUPS FROM THE BRIEF:", "MAIN ARGUMENTS FOR").splitlines():
            m = re.match(r"- (.+?): (.*) \(direction ([+-]?\d+(?:\.\d+)?)\)", line)
            if m:
                groups.append({"group": m.group(1), "impact": m.group(2), "direction": float(m.group(3)), "intensity": 0.6})
        return {"stakeholders": stakeholders, "impact_matrix": groups, "rumors": []}

    def _community_summary(self, system, user, seed):
        names = re.findall(r"^- (.+?) \(", user, re.M)[:3]
        return {"title": " & ".join(names[:2]) or "Cluster", "summary": f"A cluster around {', '.join(names)}."}

    def _stance_priors(self, system, user, seed):
        segments = []
        direction = -0.3 if re.search(r"tax|levy|vat|fee|increase|hike|remove", user, re.I) else 0.2
        for line in _between(user, "SEGMENTS:", "For EACH").splitlines():
            m = re.match(r"- (\w+): (.+?) \((\w+(?:-\w+)?) income", line)
            if m:
                factor = {"low": 1.3, "lower-middle": 1.0, "middle": 0.8, "upper": 0.5}.get(m.group(3), 1.0)
                segments.append({"key": m.group(1), "stance": round(direction * factor, 2), "salience": 0.6, "reason": "Cost and fairness concerns."})
        return {"segments": segments}

    def _bios(self, system, user, seed):
        people = _numbered(_between(user, "PEOPLE:", "Return JSON"))
        return {"people": [{"id": n, "bio": line.split(";")[0][:140], "persona": f"{line.split(',')[0]} follows the news closely and talks about it with friends."} for n, line in people]}

    def _agent_post(self, system, user, seed):
        point = _between(user, "POINT TO MAKE (in your own words, from your own life):", "\n") or "I have thoughts on this."
        view = _between(system, "Your current view on", "\n")
        prefix = "Honestly," if "oppose" in view.lower() else "I think" if "support" in view.lower() else "Hmm,"
        text = f"{prefix} {point[0].lower() + point[1:] if point else point}"
        if '"title"' in user:
            return {"title": point[:80], "text": text}
        return {"text": text[:270]}

    def _news_rewrite(self, system, user, seed):
        headline = _between(user, "LATEST HEADLINE:", "\n")
        return {"post": f"NEWS: {headline}"[:240], "stance": 0.0, "strength": 0.6}

    def _report_section(self, system, user, seed):
        # Reuse the transparent template writer on the facts sheet in the prompt.
        from apps.insights.report import SECTIONS, template_section

        from .jsonfix import JSONExtractionError, extract_json

        title = _between(user, 'Write the report section "', '"')
        key = next((k for k, t, _ in SECTIONS if t == title), "summary")
        try:
            facts = extract_json(user.split("FACTS SHEET (JSON):", 1)[1], dict)
        except (IndexError, JSONExtractionError):
            facts = {}
        try:
            return template_section(key, facts)
        except Exception:  # noqa: BLE001 - a truncated facts sheet may lack fields
            return f"This section could not be written in demo mode ({title})."

    def _interview(self, system, user, seed):
        view = "against it" if "oppose" in system.lower() else "for it" if "support" in system.lower() else "not sure yet"
        return f"Speaking for myself, I'm {view}. It affects my daily life and I want clear answers before anything changes."

    def _observer_chat(self, system, user, seed):
        from apps.insights.conversations import describe_facts

        from .jsonfix import JSONExtractionError, extract_json

        try:
            facts = extract_json(_between(user, "SIMULATION FACTS (JSON):", "RELEVANT SIMULATED POSTS"), dict)
        except JSONExtractionError:
            facts = {}
        return "(Demo mode) " + describe_facts(facts)

    def _poll(self, system, user, seed):
        people = _numbered(_between(user, "PEOPLE:", "For each person"))
        options = re.findall(r"exactly one of: (.+?)\.\n", user)
        opts = [o.strip() for o in options[0].split(",")] if options else []
        answers = []
        for n, line in people:
            m = re.search(r"\(([+-]\d\.\d\d)\)", line)
            score = float(m.group(1)) if m else 0.0
            from apps.insights.conversations import heuristic_answer

            answer = heuristic_answer(opts, score)
            answers.append({"id": n, "answer": answer, "score": score, "reason": "It depends on how it affects my family."})
        return {"answers": answers}
