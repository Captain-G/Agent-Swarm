"""Registry of research connectors, ordered from free to paid."""

from __future__ import annotations

from .base import Connector, CostTier, RawItem, SearchContext, parse_date
from .reference import Wikipedia
from .social import (
    Apify,
    Bluesky,
    Mastodon,
    Reddit,
    SocialIndex,
    TelegramChannels,
    TwitterApiIo,
    XApi,
    YouTube,
)
from .web import (
    GDELT,
    BraveSearch,
    CustomRSS,
    DuckDuckGoNews,
    DuckDuckGoWeb,
    GoogleNewsRSS,
    SearXNG,
    Serper,
    Tavily,
)

CONNECTORS: list[Connector] = [
    DuckDuckGoWeb(),
    DuckDuckGoNews(),
    GoogleNewsRSS(),
    GDELT(),
    Wikipedia(),
    Reddit(),
    SocialIndex(),
    YouTube(),
    Bluesky(),
    TelegramChannels(),
    Mastodon(),
    CustomRSS(),
    SearXNG(),
    Tavily(),
    BraveSearch(),
    Serper(),
    Apify(),
    TwitterApiIo(),
    XApi(),
]
CONNECTORS_BY_KEY = {c.key: c for c in CONNECTORS}


def available_connectors(config: dict) -> list[dict]:
    order = {tier: i for i, tier in enumerate(CostTier.ORDER)}
    infos = [c.info(config) for c in CONNECTORS]
    return sorted(infos, key=lambda i: (order[i["cost"]], not i["configured"]))


def default_selection(config: dict) -> list[str]:
    return [c.key for c in CONNECTORS if c.default_enabled and c.is_configured(config)]


__all__ = [
    "CONNECTORS",
    "CONNECTORS_BY_KEY",
    "Connector",
    "CostTier",
    "RawItem",
    "SearchContext",
    "available_connectors",
    "default_selection",
    "parse_date",
]
