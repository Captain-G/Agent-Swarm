"""Connector framework: every data source implements ``search(query, ctx)``."""

from __future__ import annotations

import email.utils
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, ClassVar

from apps.core.geo.countries import CountryProfile

from ..http import Fetcher


class CostTier:
    FREE = "free"
    FREE_KEY = "free_key"
    SELF_HOSTED = "self_hosted"
    FREEMIUM = "freemium"
    PAID = "paid"

    LABELS = {
        FREE: "Free, no sign-up",
        FREE_KEY: "Free with an API key",
        SELF_HOSTED: "Free, self-hosted",
        FREEMIUM: "Free monthly credits, then paid",
        PAID: "Paid (low cost)",
    }
    ORDER = [FREE, FREE_KEY, SELF_HOSTED, FREEMIUM, PAID]


@dataclass
class RawItem:
    url: str
    title: str = ""
    text: str = ""
    platform: str = "web"
    kind: str = "snippet"
    author: str = ""
    published_at: datetime | None = None
    metrics: dict = field(default_factory=dict)
    meta: dict = field(default_factory=dict)
    connector: str = ""
    query: str = ""


@dataclass
class SearchContext:
    country: CountryProfile
    fetcher: Fetcher
    config: dict
    brief: dict = field(default_factory=dict)
    limit: int = 10
    timelimit: str | None = "m"  # d | w | m | y | None
    log: Callable[[str], None] = lambda message: None


class Connector:
    key: ClassVar[str] = ""
    name: ClassVar[str] = ""
    platform: ClassVar[str] = "web"
    cost: ClassVar[str] = CostTier.FREE
    query_kind: ClassVar[str] = "web"  # web | social | reference
    default_enabled: ClassVar[bool] = True
    requires: ClassVar[tuple[str, ...]] = ()
    description: ClassVar[str] = ""
    setup: ClassVar[str] = ""
    terms: ClassVar[str] = ""
    price_note: ClassVar[str] = ""
    docs_url: ClassVar[str] = ""

    def is_configured(self, config: dict) -> bool:
        return all(config.get(key) for key in self.requires)

    def search(self, query: str, ctx: SearchContext) -> list[RawItem]:  # pragma: no cover - interface
        raise NotImplementedError

    def info(self, config: dict) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "platform": self.platform,
            "cost": self.cost,
            "cost_label": CostTier.LABELS[self.cost],
            "configured": self.is_configured(config),
            "default_enabled": self.default_enabled,
            "requires": list(self.requires),
            "description": self.description,
            "setup": self.setup,
            "terms": self.terms,
            "price_note": self.price_note,
            "docs_url": self.docs_url,
            "query_kind": self.query_kind,
        }


def parse_date(value) -> datetime | None:
    """Best-effort parsing of the many date formats APIs return."""
    if value in (None, "", 0):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        try:
            seconds = value / 1000 if value > 1e11 else value
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    for fmt in ("%Y%m%dT%H%M%SZ", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%a %b %d %H:%M:%S %z %Y"):
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    try:
        parsed = email.utils.parsedate_to_datetime(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError, IndexError):
        return None


TIMELIMIT_DAYS = {"d": 1, "w": 7, "m": 31, "y": 365}
