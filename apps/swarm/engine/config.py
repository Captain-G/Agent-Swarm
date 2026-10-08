"""Simulation parameters and presets."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone


@dataclass
class SimConfig:
    rounds: int = 24
    step_hours: float = 4.0
    # Simulated time of round 0 (ISO). Fixed per run so that a baseline and its
    # variants share the same daily activity rhythm; empty = 06:00 local today.
    start_time: str = ""
    platforms: list[str] = field(default_factory=lambda: ["microblog", "forum", "chat"])
    # Text generation: "hybrid" (LLM for the most influential posts, templates for
    # the rest), "template" (no LLM, zero cost) or "dynamics" (numbers only).
    mode: str = "hybrid"
    llm_posts_per_round: int = 6
    feed_size: int = 8
    # 0 = chronological/engagement feed; 1 = strongly personalised (echo chambers).
    echo_chamber: float = 0.5
    recency_window: int = 6
    # Opinion dynamics (social judgement theory, Jager & Amblard 2005, with
    # Friedkin-Johnsen anchoring).
    influence_scale: float = 1.2
    backfire: float = 0.3
    noncommitment: float = 0.25
    intensify: float = 0.6
    anchoring: float = 0.35
    noise: float = 0.01
    # Emotions and attention.
    contagion: float = 0.15
    arousal_decay: float = 0.85
    salience_decay: float = 0.94
    min_salience: float = 0.3
    activity_scale: float = 1.0
    misinfo_resistance: float = 0.7
    seed: int = 7
    tz_offset_hours: float = 3.0  # local time zone of the population (activity rhythm)
    language_style: str = "local"
    max_post_chars: int = 280

    @classmethod
    def from_dict(cls, data: dict | None) -> "SimConfig":
        data = dict(data or {})
        known = cls.__dataclass_fields__
        cfg = cls(**{k: v for k, v in data.items() if k in known})
        cfg.rounds = max(1, min(500, int(cfg.rounds)))
        cfg.step_hours = max(0.5, min(168.0, float(cfg.step_hours)))
        cfg.platforms = [p for p in cfg.platforms if p in ("microblog", "forum", "chat")] or ["microblog"]
        cfg.echo_chamber = max(0.0, min(1.0, float(cfg.echo_chamber)))
        cfg.llm_posts_per_round = max(0, int(cfg.llm_posts_per_round))
        cfg.feed_size = max(2, min(30, int(cfg.feed_size)))
        if cfg.mode not in ("hybrid", "template", "dynamics"):
            cfg.mode = "hybrid"
        return cfg

    def to_dict(self) -> dict:
        return asdict(self)

    def start_datetime(self) -> datetime:
        if self.start_time:
            try:
                start = datetime.fromisoformat(self.start_time)
                return start if start.tzinfo else start.replace(tzinfo=timezone.utc)
            except ValueError:
                pass
        return default_start(self.tz_offset_hours)


def default_start(tz_offset_hours: float, day: date | None = None) -> datetime:
    """06:00 local time (when morning news breaks) on ``day`` (default today), in UTC."""
    tz = timezone(timedelta(hours=tz_offset_hours))
    day = day or datetime.now(tz).date()
    return datetime(day.year, day.month, day.day, 6, tzinfo=tz).astimezone(timezone.utc)


PRESETS = {
    "quick": {
        "label": "Quick look",
        "help": "12 rounds of 6 hours (3 days). About 3 AI-written posts per round.",
        "config": {"rounds": 12, "step_hours": 6, "llm_posts_per_round": 3, "feed_size": 6},
    },
    "standard": {
        "label": "Standard",
        "help": "24 rounds of 4 hours (4 days). About 6 AI-written posts per round.",
        "config": {"rounds": 24, "step_hours": 4, "llm_posts_per_round": 6, "feed_size": 8},
    },
    "deep": {
        "label": "Deep",
        "help": "42 rounds of 4 hours (one week). About 10 AI-written posts per round.",
        "config": {"rounds": 42, "step_hours": 4, "llm_posts_per_round": 10, "feed_size": 10},
    },
    "free": {
        "label": "Instant (no AI text)",
        "help": "24 rounds, every post written from the argument bank. Runs in seconds, costs nothing.",
        "config": {"rounds": 24, "step_hours": 4, "mode": "template", "llm_posts_per_round": 0, "feed_size": 8},
    },
}

TZ_OFFSETS = {"KE": 3, "UG": 3, "TZ": 3, "ET": 3, "RW": 2, "ZA": 2, "NG": 1, "GH": 0, "XX": 3}
TZ_NAMES = {3: "EAT", 2: "CAT", 1: "WAT", 0: "GMT"}


def country_timezone(code: str | None) -> timezone:
    """Fixed-offset local time zone of a country's population (none of them use daylight saving)."""
    offset = TZ_OFFSETS.get((code or "").upper(), 3)
    return timezone(timedelta(hours=offset), TZ_NAMES.get(offset))

# Relative online activity by local hour of day (East African rhythm: commute,
# lunch and evening peaks).
HOUR_CURVE = [
    0.25, 0.15, 0.10, 0.08, 0.10, 0.25, 0.60, 0.90, 1.00, 0.85, 0.75, 0.75,
    0.90, 1.00, 0.80, 0.75, 0.80, 0.95, 1.10, 1.25, 1.30, 1.25, 0.95, 0.55,
]


def hour_factor(start_hour: int, step_hours: float) -> float:
    if step_hours >= 12:
        return 1.0
    hours = max(1, int(round(step_hours)))
    return sum(HOUR_CURVE[(start_hour + h) % 24] for h in range(hours)) / hours
