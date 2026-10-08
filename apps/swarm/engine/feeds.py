"""
Feed / recommendation models for the three simulated platforms.

The ``echo_chamber`` parameter (0..1) controls how strongly the ranking
favours content that matches the reader's current opinion - the main
experimental lever for studying algorithmic polarisation.
"""

from __future__ import annotations

import math

import numpy as np

from .state import SimAgent, SimPost


def weighted_sample(rng: np.random.Generator, items: list, weights: np.ndarray, k: int) -> list:
    """Weighted sampling without replacement (Efraimidis-Spirakis)."""
    if not items or k <= 0:
        return []
    weights = np.asarray(weights, dtype=float)
    mask = weights > 0
    if not mask.any():
        return []
    keys = np.full(len(items), -np.inf)
    u = rng.random(mask.sum())
    keys[mask] = np.log(u) / weights[mask]
    order = np.argsort(-keys)[: min(k, int(mask.sum()))]
    return [items[i] for i in order]


class FeedIndex:
    """Per-round snapshot of candidate posts, vectorised for fast ranking."""

    def __init__(self, posts: list[SimPost], agents: list[SimAgent], current_round: int, window: int):
        self.posts = [p for p in posts if current_round - p.round <= window]
        self.n = len(self.posts)
        if self.n:
            self.stance = np.array([p.stance for p in self.posts])
            self.age = np.array([current_round - p.round for p in self.posts], dtype=float)
            self.engagement = np.array([p.engagement for p in self.posts], dtype=float)
            self.boost = np.array([p.boost if p.boost_until >= current_round else 1.0 for p in self.posts])
            self.author = np.array([p.author for p in self.posts])
            followers = np.array([len(agents[p.author].followers) for p in self.posts], dtype=float)
            self.popularity = 1.0 + 0.25 * np.log1p(followers)

    def microblog(self, agent: SimAgent, k: int, echo: float, rng: np.random.Generator) -> list[SimPost]:
        if not self.n:
            return []
        recency = np.exp(-0.35 * self.age)
        follow = np.fromiter((3.0 if a in agent.following else 1.0 for a in self.author), dtype=float, count=self.n)
        reposted = np.fromiter(
            (1.6 if (p.reposted_by and not p.reposted_by.isdisjoint(agent.following)) else 1.0 for p in self.posts), dtype=float, count=self.n
        )
        engagement = 1.0 + np.log1p(self.engagement)
        homophily = np.exp(-echo * 5.0 * np.abs(self.stance - agent.stance))
        own = np.fromiter((0.0 if a == agent.idx or a in agent.muted else 1.0 for a in self.author), dtype=float, count=self.n)
        weights = recency * follow * reposted * engagement * homophily * self.boost * self.popularity * own
        return weighted_sample(rng, self.posts, weights, k)

    def forum_threads(self, agent: SimAgent, echo: float, rng: np.random.Generator, k: int = 1) -> list[SimPost]:
        if not self.n:
            return []
        score = np.array([p.likes - p.dislikes for p in self.posts], dtype=float)
        hot = np.log10(1.0 + np.maximum(score, 0)) + 0.3 * np.log1p(np.array([p.replies for p in self.posts], dtype=float))
        recency = np.exp(-0.25 * self.age)
        homophily = np.exp(-echo * 3.0 * np.abs(self.stance - agent.stance))
        own = np.fromiter((0.3 if a == agent.idx else 0.0 if a in agent.muted else 1.0 for a in self.author), dtype=float, count=self.n)
        weights = (1.0 + hot) * recency * homophily * self.boost * own
        return weighted_sample(rng, self.posts, weights, k)


def thread_comments(thread: SimPost, comments: list[SimPost], agent: SimAgent, k: int, echo: float, rng: np.random.Generator) -> list[SimPost]:
    if not comments:
        return []
    weights = np.array(
        [
            (1.0 + max(0, c.likes - c.dislikes)) ** 0.7
            * math.exp(-echo * 1.0 * abs(c.stance - agent.stance))
            * (0.0 if c.author == agent.idx or c.author in agent.muted else 1.0)
            * (2.0 if c.parent == thread.seq else 1.0)
            for c in comments
        ]
    )
    return weighted_sample(rng, comments, weights, k)


def chat_view(agent: SimAgent, group_msgs: dict[int, list[SimPost]], current_round: int, window: int, k: int, rng) -> tuple[int | None, list[SimPost]]:
    if not agent.groups:
        return None, []
    activity = [1.0 + sum(1 for m in group_msgs.get(g, []) if current_round - m.round <= window) for g in agent.groups]
    group = rng.choices(agent.groups, weights=activity, k=1)[0]
    msgs = [m for m in group_msgs.get(group, []) if current_round - m.round <= window * 2 and m.author != agent.idx]
    return group, msgs[-k:]
