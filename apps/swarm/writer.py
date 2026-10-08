"""
LLM post writer.

Every call re-injects the agent's full persona and current stance into the
system prompt (the main defence against identity drift in long runs), plus a
small, relevant slice of the knowledge base (facts, stakeholder positions, the
argument to make) - GraphRAG grounding at a few hundred tokens per post.

Guardrails: coordinated "bot" accounts and misinformation are always written
by templates, never by the LLM, so the tool cannot be used to mass-produce
persuasive disinformation. If the model fails repeatedly the writer falls
back to templates for the rest of the run instead of stalling.
"""

from __future__ import annotations

import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor

from django.db import close_old_connections, connection

from apps.core.geo import get_country
from apps.core.llm import LLMError, as_str, get_llm
from apps.core.text.textutils import truncate
from apps.swarm.models import stance_label

from .engine.content import TemplateWriter, _fit
from .engine.population_text import describe_traits
from .engine.state import ContentRequest, Draft

logger = logging.getLogger("baraza.swarm")

PLATFORM_DESC = {
    "microblog": "an X/Twitter-style microblog (short, public, hashtags common)",
    "forum": "a Facebook-group/Reddit-style forum (longer, threaded discussion)",
    "chat": "a WhatsApp group chat with people you know (informal, short)",
}
KIND_DESC = {
    "post": "post",
    "reply": "reply",
    "thread": "new discussion thread with a title",
    "comment": "comment in the thread",
    "message": "group chat message",
    "statement": "official post",
    "news": "news post",
}
AI_TELLS = re.compile(r"^(as an ai|as a language model|here is|here's|sure[,!])[^:]*:\s*", re.I)


class LLMWriter:
    def __init__(self, project, cfg, *, facts: list[str], stakeholders: list[str], topic: str, log=None):
        self.project = project
        self.cfg = cfg
        self.country = get_country(project.country)
        self.topic = topic
        self.facts = facts
        self.stakeholders = stakeholders
        self.llm = get_llm(project)
        self.template = TemplateWriter(
            hashtags=(project.brief or {}).get("hashtags", {}),
            flavor=self.country.flavor,
            language_style=cfg.language_style,
            max_chars=cfg.max_post_chars,
            facts=facts,
            demonym=self.country.demonym,
        )
        self.log = log or (lambda m: None)
        self._lock = threading.Lock()
        self._failures = 0
        self.disabled = not self.llm.available("fast")
        self.stats = {"llm": 0, "template": 0, "fallback": 0}
        if self.disabled:
            self.log("No language model reachable: all posts will be written from templates.")

    # ------------------------------------------------------------------
    def language_rule(self) -> str:
        style = self.cfg.language_style
        example = ""
        if self.country.flavor.get("neutral"):
            example = f' (for example: "{self.country.flavor["neutral"][0]}")'
        if style == "english":
            return "Write in clear, simple English."
        if style == "mixed":
            return f"Write the way people in {self.country.name} really post online: {self.country.local_language_style}."
        return f"Write mainly in English; an occasional local expression from {self.country.name} is fine if natural{example}."

    def system_prompt(self, agent) -> str:
        if agent.kind == "institution":
            return (
                f"You run the official social media account of {agent.name} in {self.country.name}. {agent.persona}\n"
                f"Its position on \"{self.topic}\": {stance_label(agent.stance)} ({agent.stance:+.2f} on a scale from -1 strongly against to +1 strongly for).\n"
                "Write like a real institution's communications team: clear, measured, on-message. Never mention being simulated. Reply with JSON only."
            )
        if agent.kind == "media":
            return (
                f"You run the social media account of {agent.name}, a news outlet in {self.country.name}.\n"
                "Write neutral, factual news-style posts. Never invent statistics. Never mention being simulated. Reply with JSON only."
            )
        age = f"{agent.age}-year-old " if agent.age else ""
        return (
            f"You are {agent.name} (@{agent.handle}), a {age}{agent.occupation.lower() or agent.archetype_label.lower()} "
            f"from {agent.region}, {self.country.name}. {agent.persona}\n"
            f"Personality: {describe_traits(agent)}. Writing style: {agent.voice or 'plain, direct'}. {self.language_rule()}\n"
            f"Your current view on \"{self.topic}\": {stance_label(agent.stance)} ({agent.stance:+.2f} on a scale from -1 strongly against to +1 strongly for). "
            f"Right now you feel {agent.emotion}.\n"
            "Stay fully in character. Sound like a real person on social media - not an AI, not a press release. "
            "Never mention being simulated. Reply with JSON only."
        )

    def user_prompt(self, world, req: ContentRequest, agent) -> str:
        limit = self.cfg.max_post_chars if req.platform != "forum" else 600
        parts = [f"PLATFORM: {PLATFORM_DESC[req.platform]}. You are writing a {KIND_DESC.get(req.kind, 'post')}."]
        parent = world.posts.get(req.parent) if req.parent else None
        if parent is not None:
            who = world.agents[parent.author]
            relation = "You disagree with it." if req.intent == "rebut" else "You agree with it." if req.intent == "agree" else ""
            parts.append(f"YOU ARE RESPONDING TO @{who.handle} ({who.archetype_label}): \"{truncate(parent.text, 300)}\" {relation}")
        else:
            seen = [world.posts[s] for s in list(agent.recent_posts)[-1:] if s in world.posts]
            if seen:
                parts.append(f"YOUR LAST POST (do not repeat it): \"{truncate(seen[0].text, 160)}\"")
        arg = req.argument or {}
        if arg.get("claim"):
            parts.append(f"POINT TO MAKE (in your own words, from your own life): {truncate(arg['claim'], 300)}")
        relevant = self.facts_for(arg.get("claim", "") + " " + " ".join(agent.concerns or []))
        if relevant:
            parts.append("WHAT YOU KNOW (use at most one, only if natural):\n" + "\n".join(f"- {f}" for f in relevant))
        if agent.kind in ("institution", "media") and self.stakeholders:
            parts.append("OTHER ACTORS: " + "; ".join(self.stakeholders[:3]))
        memory = list(agent.memory)[-2:]
        if memory and agent.kind not in ("institution", "media"):
            parts.append("RECENTLY: " + " | ".join(truncate(m, 140) for m in memory))
        hashtags = (self.project.brief or {}).get("hashtags", {})
        side = arg.get("side") or ("support" if req.stance > 0.15 else "oppose" if req.stance < -0.15 else "neutral")
        if req.platform == "microblog" and hashtags.get(side):
            parts.append(f"You may use a hashtag such as {hashtags[side][0]}.")
        if req.kind == "thread":
            parts.append(f'Write a thread title (max 90 characters) and a post (max {limit} characters). Return JSON: {{"title": "...", "text": "..."}}')
        else:
            parts.append(f'Write ONE {KIND_DESC.get(req.kind, "post")} of at most {limit} characters that clearly expresses your view. Return JSON: {{"text": "..."}}')
        return "\n".join(parts)

    def facts_for(self, text: str, k: int = 2) -> list[str]:
        if not self.facts:
            return []
        words = {w for w in re.findall(r"[a-z]{4,}", text.lower())}
        scored = sorted(self.facts, key=lambda f: -len(words & set(re.findall(r"[a-z]{4,}", f.lower()))))
        return scored[:k]

    # ------------------------------------------------------------------
    def _clean(self, text: str, limit: int) -> str:
        text = AI_TELLS.sub("", (text or "").strip()).strip().strip('"').strip()
        text = re.sub(r"\s+", " ", text)
        return _fit(text, limit)

    def _one(self, world, req: ContentRequest) -> Draft:
        agent = world.agents[req.agent]
        if self.disabled or agent.kind == "bot" or req.is_misinfo or req.forced_text:
            self.stats["template"] += 1
            return self.template.write_one(world, req)
        close_old_connections()
        try:
            data = self.llm.json(
                system=self.system_prompt(agent),
                user=self.user_prompt(world, req, agent),
                purpose="agent_post",
                tier="fast",
                max_tokens=320 if req.kind == "thread" else 200,
                temperature=0.9,
                seed=req.seed,
                retries=0,
            )
            limit = self.cfg.max_post_chars if req.platform != "forum" else 900
            text = self._clean(as_str(data.get("text") or data.get("post") or data.get("message")), limit)
            if len(text) < 8:
                raise LLMError("empty post")
            with self._lock:
                self._failures = 0
                self.stats["llm"] += 1
            return Draft(text=text, title=self._clean(as_str(data.get("title")), 90) if req.kind == "thread" else "", source="llm")
        except Exception as exc:  # noqa: BLE001 - never let one post break a run
            with self._lock:
                self._failures += 1
                self.stats["fallback"] += 1
                if self._failures >= 6 and not self.disabled:
                    self.disabled = True
                    self.log(f"The language model failed {self._failures} times in a row ({exc}); switching to templates for the rest of this run.")
            return self.template.write_one(world, req)
        finally:
            connection.close()

    def write(self, world, requests: list[ContentRequest], use_llm: bool = False) -> list[Draft]:
        if not use_llm or self.disabled:
            self.stats["template"] += len(requests)
            return self.template.write(world, requests)
        workers = max(1, int(self.llm.conf.get("LLM_CONCURRENCY", 2)))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="writer") as pool:
            return list(pool.map(lambda r: self._one(world, r), requests))
