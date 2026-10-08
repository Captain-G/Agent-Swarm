"""
Opinion and emotion dynamics.

Opinion change follows Social Judgement Theory as formalised by Jager &
Amblard (2005): a message whose position is within an agent's *latitude of
acceptance* pulls the agent toward it (assimilation); one beyond the
*latitude of rejection* pushes the agent away (contrast / backfire); in
between there is weak assimilation. Latitudes come from personality
(openness widens acceptance, agreeableness delays rejection).

Within the latitude of acceptance, aligned and emotionally charged content
also *intensifies* an aroused agent's view (group polarisation /
persuasive-arguments theory, Myers & Lamm 1976).

Each round every agent is also anchored toward their initial opinion
(Friedkin & Johnsen 1990) in proportion to their stubbornness, and receives
small idiosyncratic noise. Emotional arousal spreads by contagion (high-arousal
content - especially anger - spreads further) and decays over time.

Influence weight combines source credibility, tie strength (following, close
chat groups), social proof (likes/reposts), argument strength, misinformation
scepticism (conscientiousness) and how much the agent cares (salience).
"""

from __future__ import annotations

import math

from .config import SimConfig
from .state import SimAgent, SimPost


def clip(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return lo if x < lo else hi if x > hi else x


def influence_weight(agent: SimAgent, author: SimAgent, post: SimPost, cfg: SimConfig, platform: str) -> float:
    trust = 0.4 + author.credibility
    tie = 1.5 if post.author in agent.following else 1.0
    if platform == "chat":
        tie *= 1.4  # close ties in family and community groups
    social_proof = 1.0 + 0.12 * math.log1p(post.likes + post.reposts)
    quality = 0.5 + post.strength
    misinfo = (1.0 - cfg.misinfo_resistance * agent.c) if post.is_misinfo else 1.0
    salience = 0.5 + 0.5 * agent.salience
    return min(0.5, cfg.influence_scale * agent.mu * trust * tie * social_proof * quality * misinfo * salience)


def expose(agent: SimAgent, author: SimAgent, post: SimPost, cfg: SimConfig, platform: str) -> float:
    """Update ``agent`` after reading ``post``. Returns the stance change."""
    if agent.kind == "bot" or agent.idx == post.author:
        return 0.0
    before = agent.stance
    d = post.stance - before
    ad = abs(d)
    w = influence_weight(agent, author, post, cfg, platform)
    if ad <= agent.eps:
        delta = w * d
        # Group polarisation (persuasive-arguments theory): aligned, emotionally
        # charged content pushes an aroused agent further toward their own pole.
        if cfg.intensify and before * post.stance > 0 and abs(post.stance) >= abs(before) - 0.1:
            delta += cfg.intensify * w * post.arousal * agent.arousal * math.copysign(1.0, before) * (1.0 - abs(before))
    elif ad >= agent.rho:
        delta = -cfg.backfire * w * math.copysign(1.0, d) * (ad - agent.rho) * (0.5 + agent.n)
    else:
        delta = cfg.noncommitment * w * d
    agent.stance = clip(before + delta)
    actual = agent.stance - before
    if post.is_misinfo:
        agent.misinfo_shift += actual
    elif post.factcheck and agent.misinfo_shift:
        # Corrections partially undo what misinformation did (the "continued
        # influence effect" means they rarely undo all of it).
        correction = -0.6 * agent.misinfo_shift * min(1.0, 0.5 + agent.c) * (0.5 + author.credibility)
        agent.stance = clip(agent.stance + correction)
        agent.misinfo_shift *= 0.3
        actual = agent.stance - before

    if d:
        toward = actual * math.copysign(1.0, d)
        post.influence += toward
        if toward > 0.004:
            post.persuaded += 1
        elif toward < -0.002:
            post.backfired += 1

    # Emotional contagion: disagreement provokes, agreement amplifies shared feeling.
    provoke = 1.0 if ad > agent.eps else 0.7
    agent.arousal = clip(agent.arousal + cfg.contagion * post.arousal * (0.6 + 0.8 * agent.n) * provoke * 0.5, 0.0, 1.0)
    return actual


def end_of_round(agent: SimAgent, cfg: SimConfig, rng) -> None:
    if agent.kind == "bot":
        agent.stance = agent.bot_target if agent.bot_target is not None else agent.stance
    else:
        # Anchoring is defined per 4 hours of simulated time so results do not
        # depend on the round length.
        pull = 1.0 - (1.0 - min(0.95, cfg.anchoring * agent.gamma)) ** (cfg.step_hours / 4.0)
        agent.stance = clip(agent.stance + pull * (agent.initial - agent.stance))
        if agent.is_public and cfg.noise > 0:
            agent.stance = clip(agent.stance + rng.gauss(0.0, cfg.noise))
    base = 0.12 + 0.2 * agent.n
    agent.arousal = clip(base + (agent.arousal - base) * cfg.arousal_decay, 0.0, 1.0)
    agent.emotion = agent_emotion(agent)
    agent.history.append(round(agent.stance, 3))


def agent_emotion(agent: SimAgent) -> str:
    s, ar = agent.stance, agent.arousal
    if ar < 0.3:
        if abs(s) < 0.4:
            return "neutral"
        return "trust" if s > 0 else "sadness"
    if s <= -0.25:
        if agent.n > 0.65:
            return "fear"
        return "anger" if ar >= 0.45 else "disgust" if agent.c > 0.6 else "sadness"
    if s >= 0.25:
        return "joy" if ar >= 0.6 else "hope"
    return "fear" if agent.n > 0.6 else "neutral"


EMOTION_AROUSAL = {"anger": 0.85, "fear": 0.7, "disgust": 0.65, "joy": 0.6, "hope": 0.45, "sadness": 0.35, "trust": 0.3, "neutral": 0.2}


def policy_shift(agent: SimAgent, delta: float) -> float:
    """Direct opinion shift from a policy change (e.g. a concession)."""
    if agent.kind in ("bot",):
        return 0.0
    effective = delta * (1.0 - min(0.8, agent.gamma * 3)) * (0.5 + 0.5 * agent.salience)
    before = agent.stance
    agent.stance = clip(agent.stance + effective)
    # A concession also resets the anchor partially: people re-evaluate the new policy.
    agent.initial = clip(agent.initial + 0.5 * effective)
    return agent.stance - before
