"""Plain-language descriptions of agents (used in prompts and the UI)."""

from __future__ import annotations


def describe_traits(agent) -> str:
    o, c, e, a, n = (getattr(agent, t, 0.5) for t in "ocean")
    parts = []
    if o > 0.65:
        parts.append("curious and open to new ideas")
    elif o < 0.35:
        parts.append("traditional and wary of change")
    if c > 0.65:
        parts.append("careful and detail-oriented")
    elif c < 0.35:
        parts.append("spontaneous")
    if e > 0.65:
        parts.append("outgoing and enjoys debate")
    elif e < 0.35:
        parts.append("reserved")
    if a > 0.65:
        parts.append("warm and conciliatory")
    elif a < 0.35:
        parts.append("blunt and combative")
    if n > 0.65:
        parts.append("anxious and easily upset")
    elif n < 0.35:
        parts.append("calm under pressure")
    return ", ".join(parts) or "even-tempered and pragmatic"
