"""
Content writers turn a ContentRequest (who speaks, where, with which stance,
argument and emotion) into text.

The *engine* decides the semantics (stance, argument, emotion) so opinion
dynamics are identical whatever writer is used; writers only produce words.
``TemplateWriter`` needs no AI at all and is used for zero-cost runs and as
the fallback for the LLM writer.
"""

from __future__ import annotations

import random
import re

from .state import ContentRequest, Draft

EN_OPENERS = {
    "anger": ["This is unacceptable.", "Enough is enough.", "Are we being serious right now?", "Shame on them.", "I am so angry about this.", "We will not accept this."],
    "fear": ["I'm genuinely worried.", "This scares me.", "Where is this taking us?", "Think about what happens next."],
    "sadness": ["It's heartbreaking.", "Life is already hard enough.", "So many families will suffer."],
    "disgust": ["Disgraceful.", "This stinks.", "Shameless."],
    "joy": ["Finally!", "Great news.", "This is a win."],
    "hope": ["This could work.", "I'm cautiously optimistic.", "Let's give it a chance."],
    "trust": ["Makes sense to me.", "This is the right call.", "A credible plan."],
    "neutral": ["Honest question:", "Trying to understand this.", "Let's discuss.", "Weighing both sides here."],
}
REBUT = ["That's not the full picture.", "With respect, I disagree.", "Not true.", "You're missing the point.", "Easy for you to say.", "Have you even read the details?"]
AGREE = ["Exactly this.", "Well said.", "100%.", "This!", "Couldn't agree more.", "Facts."]
CLOSERS = ["Who is with me?", "Speak up!", "Thoughts?", "Share if you agree.", "", "", ""]
MOBILISE = ["We must speak up!", "Make noise everywhere!", "Call your representative today.", "Show up and be counted!"]
CHAT_PREFIX = ["Guys,", "Family,", "Have you seen this?", "Friends,", "", "", ""]
INSTITUTION_PREFIX = {"official": ["Statement:", "Clarification:", "Our position:", "For the record:"], "media": ["BREAKING:", "Developing:", "Analysis:", "Explainer:"]}
BOT_SLOGANS = {
    "oppose": ["{tag} NOW!", "Wake up people! {tag}", "They are lying to you! {tag}", "Say NO! {tag}", "Not in our name! {tag}"],
    "support": ["{tag}! Progress cannot be stopped.", "Haters will hate. {tag}", "Real patriots support this. {tag}", "Keep going! {tag}"],
    "neutral": ["Don't believe everything you read. {tag}", "Stay woke. {tag}"],
}


def _fit(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip(",;: ") + "…"


class TemplateWriter:
    def __init__(self, *, hashtags: dict | None = None, flavor: dict | None = None, language_style: str = "local", max_chars: int = 280, facts: list[str] | None = None, demonym: str = "citizen"):
        self.hashtags = hashtags or {}
        self.flavor = flavor or {}
        self.language_style = language_style
        self.max_chars = max_chars
        self.facts = facts or []
        self.demonym = demonym

    def _opener(self, rng: random.Random, emotion: str) -> str:
        chance = {"english": 0.0, "local": 0.35, "mixed": 0.65}.get(self.language_style, 0.35)
        if self.flavor.get(emotion) and rng.random() < chance:
            return rng.choice(self.flavor[emotion])
        return rng.choice(EN_OPENERS.get(emotion, EN_OPENERS["neutral"]))

    def _tag(self, rng: random.Random, side: str) -> str:
        tags = self.hashtags.get(side) or self.hashtags.get("neutral") or []
        return rng.choice(tags) if tags else ""

    def write_one(self, world, req: ContentRequest) -> Draft:
        rng = random.Random(req.seed)
        agent = world.agents[req.agent]
        arg = req.argument or {}
        body = (arg.get("short") or arg.get("claim") or "").strip()
        side = arg.get("side") or ("support" if req.stance > 0.15 else "oppose" if req.stance < -0.15 else "neutral")
        if req.forced_text:
            text = req.forced_text
            if req.platform == "microblog" and req.kind in ("news", "statement") and len(text) > self.max_chars:
                text = _fit(text, self.max_chars)
            return Draft(text=text, title=req.forced_title or _fit(text, 90), hashtags=[])
        parent = world.posts.get(req.parent) if req.parent else None
        parent_author = world.agents[parent.author].handle if parent else ""

        if agent.kind == "bot":
            tag = self._tag(rng, side) or "#Truth"
            if req.is_misinfo:
                text = f"{body} {tag}"
            else:
                text = rng.choice(BOT_SLOGANS.get(side, BOT_SLOGANS["neutral"])).format(tag=tag)
                if rng.random() < 0.5 and body:
                    text = f"{text} {body}"
            if req.kind in ("reply", "comment") and parent_author:
                text = f"@{parent_author} {text}"
            return Draft(text=_fit(text, self.max_chars), title=_fit(body, 90), hashtags=[tag])

        if agent.kind in ("institution", "media"):
            prefix = rng.choice(INSTITUTION_PREFIX["media" if agent.kind == "media" else "official"])
            content = arg.get("claim") or body
            if agent.kind == "media" and self.facts and rng.random() < 0.5:
                content = rng.choice(self.facts)
            text = f"{prefix} {content}"
            if req.kind in ("reply", "comment") and parent_author:
                text = f"@{parent_author} {content}"
            return Draft(text=_fit(text, self.max_chars if req.platform != "forum" else 600), title=_fit(content, 90))

        emotion = req.emotion
        if req.kind in ("reply", "comment") or (req.kind == "message" and parent):
            lead = rng.choice(AGREE if req.intent == "agree" else REBUT)
            mention = f"@{parent_author} " if parent_author and req.platform != "chat" else ""
            text = f"{mention}{lead} {body}"
        elif req.platform == "chat":
            prefix = rng.choice(CHAT_PREFIX)
            text = f"{prefix} {self._opener(rng, emotion) if rng.random() < 0.5 else ''} {body}"
        else:
            text = f"{self._opener(rng, emotion)} {body}"
            closer = rng.choice(MOBILISE) if (side == "oppose" and req.arousal > 0.7 and rng.random() < 0.35) else rng.choice(CLOSERS)
            if closer:
                text = f"{text} {closer}"
        hashtags = []
        if req.platform == "microblog" and rng.random() < 0.6:
            tag = self._tag(rng, side)
            if tag:
                text = f"{text} {tag}"
                hashtags.append(tag)
        limit = self.max_chars if req.platform != "forum" else 900
        title = ""
        if req.kind == "thread":
            title = _fit(arg.get("claim") or body, 90)
            extra = rng.choice(["What do you all think?", "Am I wrong?", "Let's discuss honestly.", "Change my mind."])
            text = f"{body} {extra}"
        return Draft(text=_fit(text, limit), title=title, hashtags=hashtags)

    def write(self, world, requests: list[ContentRequest], use_llm: bool = False) -> list[Draft]:
        return [self.write_one(world, req) for req in requests]


class NullWriter:
    """For dynamics-only runs (Monte Carlo ensembles): no text at all."""

    def write(self, world, requests: list[ContentRequest], use_llm: bool = False) -> list[Draft]:
        return [Draft(text=req.forced_text or "") for req in requests]
