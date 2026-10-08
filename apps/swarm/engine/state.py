"""In-memory state of a running simulation (independent of Django)."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class SimAgent:
    idx: int
    pk: int | None
    handle: str
    name: str
    kind: str
    archetype: str
    archetype_label: str
    stance: float
    initial: float
    o: float = 0.5
    c: float = 0.5
    e: float = 0.5
    a: float = 0.5
    n: float = 0.5
    region: str = ""
    region_group: str = ""
    age: int | None = None
    gender: str = ""
    leaning: str = ""
    urban: bool = True
    salience: float = 0.5
    credibility: float = 0.35
    activity: float = 0.15
    mu: float = 0.1
    eps: float = 0.4
    rho: float = 1.0
    gamma: float = 0.05
    platforms: dict = field(default_factory=dict)
    following: set = field(default_factory=set)
    followers: set = field(default_factory=set)
    muted: set = field(default_factory=set)
    groups: list = field(default_factory=list)
    bot_target: float | None = None
    arousal: float = 0.2
    emotion: str = "neutral"
    misinfo_shift: float = 0.0
    memory: deque = field(default_factory=lambda: deque(maxlen=8))
    recent_args: deque = field(default_factory=lambda: deque(maxlen=4))
    recent_posts: deque = field(default_factory=lambda: deque(maxlen=3))
    history: list = field(default_factory=list)
    posts_count: int = 0
    reactions_count: int = 0
    last_active: int = -1
    persona: str = ""
    bio: str = ""
    voice: str = ""
    concerns: list = field(default_factory=list)
    occupation: str = ""

    @property
    def is_public(self) -> bool:
        """Members of the public (whose opinion we measure)."""
        return self.kind in ("citizen", "influencer")

    @property
    def side(self) -> str:
        if self.stance > 0.15:
            return "support"
        if self.stance < -0.15:
            return "oppose"
        return "neutral"

    @property
    def age_band(self) -> str:
        if self.age is None:
            return "n/a"
        for lo, hi, label in ((18, 24, "18-24"), (25, 34, "25-34"), (35, 44, "35-44"), (45, 54, "45-54"), (55, 64, "55-64")):
            if lo <= self.age <= hi:
                return label
        return "65+"


@dataclass
class SimPost:
    seq: int
    platform: str
    kind: str
    author: int
    round: int
    text: str = ""
    title: str = ""
    hashtags: list = field(default_factory=list)
    stance: float = 0.0
    strength: float = 0.5
    arousal: float = 0.3
    emotion: str = "neutral"
    argument_id: int | None = None
    source: str = "template"
    is_misinfo: bool = False
    factcheck: bool = False
    intervention_id: int | None = None
    parent: int | None = None
    root: int | None = None
    group: int | None = None
    sim_time: datetime | None = None
    likes: int = 0
    dislikes: int = 0
    reposts: int = 0
    replies: int = 0
    views: int = 0
    influence: float = 0.0
    persuaded: int = 0
    backfired: int = 0
    boost: float = 1.0
    boost_until: int = -1
    reposted_by: set = field(default_factory=set)
    sentiment: float = 0.0

    @property
    def engagement(self) -> int:
        return self.likes + 2 * self.reposts + self.replies


@dataclass
class ContentRequest:
    agent: int
    platform: str
    kind: str  # post | reply | thread | comment | message | forward | news | statement
    parent: int | None = None
    root: int | None = None
    group: int | None = None
    priority: float = 0.0
    intent: str = "new"  # new | rebut | agree | inform | campaign | misinfo
    argument: dict | None = None
    stance: float = 0.0
    emotion: str = "neutral"
    arousal: float = 0.3
    is_misinfo: bool = False
    factcheck: bool = False
    forced_text: str = ""
    forced_title: str = ""
    intervention_id: int | None = None
    boost: float = 1.0
    boost_rounds: int = 0
    seed: int = 0


@dataclass
class Draft:
    text: str
    title: str = ""
    hashtags: list = field(default_factory=list)
    source: str = "template"


@dataclass
class RoundResult:
    round: int
    sim_time: datetime
    new_posts: list = field(default_factory=list)
    touched_posts: set = field(default_factory=set)
    actions: list = field(default_factory=list)  # (kind, agent_idx, post_seq|None, target_idx|None)
    metrics: dict = field(default_factory=dict)
    events: list = field(default_factory=list)
