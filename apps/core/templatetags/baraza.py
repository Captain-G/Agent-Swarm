"""Template helpers: inline SVG icons, stance formatting, safe markdown."""

from __future__ import annotations

import functools
import re
from pathlib import Path

from django import template
from django.conf import settings
from django.utils.html import escape, format_html
from django.utils.safestring import mark_safe

from apps.core.text.markdown import render_markdown
from apps.swarm.models import stance_label as _stance_label

register = template.Library()
ICON_DIR = Path(settings.BASE_DIR) / "static" / "icons"
_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_SVG_OPEN_RE = re.compile(r"<svg[^>]*>", re.S)


@functools.lru_cache(maxsize=256)
def _icon_inner(name: str) -> str:
    path = ICON_DIR / f"{name}.svg"
    if not path.exists():
        return ""
    raw = _COMMENT_RE.sub("", path.read_text(encoding="utf-8"))
    inner = _SVG_OPEN_RE.sub("", raw, count=1).replace("</svg>", "")
    return re.sub(r"\s+", " ", inner).strip()


@register.simple_tag
def icon(name: str, css: str = "icon", label: str = ""):
    inner = _icon_inner(name)
    if not inner:
        return ""
    aria = f'role="img" aria-label="{escape(label)}"' if label else 'aria-hidden="true" focusable="false"'
    return mark_safe(
        f'<svg class="{escape(css)}" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        f'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" {aria}>{inner}</svg>'
    )


@register.filter
def stance_class(value) -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "neutral"
    return "support" if v > 0.2 else "oppose" if v < -0.2 else "neutral"


@register.filter
def stance_label(value) -> str:
    try:
        return _stance_label(float(value))
    except (TypeError, ValueError):
        return "Unknown"


@register.filter
def stance_pos(value) -> str:
    """Position (0-100%) on a -1..1 stance bar."""
    try:
        return f"{(float(value) + 1) * 50:.1f}%"
    except (TypeError, ValueError):
        return "50%"


@register.filter
def pct(value, digits: int = 0) -> str:
    try:
        return f"{100 * float(value):.{int(digits)}f}%"
    except (TypeError, ValueError):
        return "n/a"


@register.filter
def signed(value, digits: int = 2) -> str:
    try:
        return f"{float(value):+.{int(digits)}f}"
    except (TypeError, ValueError):
        return "n/a"


@register.filter(name="abs")
def abs_filter(value):
    try:
        return abs(float(value))
    except (TypeError, ValueError):
        return value


@register.filter
def get_item(mapping, key):
    """Dictionary key or list index lookup."""
    if isinstance(mapping, (list, tuple)):
        try:
            return mapping[int(key)]
        except (ValueError, TypeError, IndexError):
            return None
    try:
        return mapping.get(key)
    except AttributeError:
        return None


@register.filter
def initials(name: str) -> str:
    parts = [p for p in re.split(r"[\s_]+", str(name or "")) if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2]
    return (parts[0][0] + parts[-1][0])


@register.filter
def markdown(value: str):
    return mark_safe(render_markdown(value or "", heading_offset=1))


_TAG_OR_MENTION_RE = re.compile(r"([#@]\w{2,60})")


@register.filter
def social_text(value: str):
    """Highlight hashtags and mentions; every piece of text is escaped."""
    parts = _TAG_OR_MENTION_RE.split(value or "")
    out = []
    for i, part in enumerate(parts):
        if i % 2:
            out.append(f'<span class="hashtag">{escape(part)}</span>')
        else:
            out.append(escape(part))
    return mark_safe("".join(out))


@register.filter
def heat_style(value) -> str:
    """Inline background for a stance heat-map cell."""
    try:
        v = max(-1.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return ""
    var = "--stance-support" if v > 0 else "--stance-oppose"
    strength = int(min(85, 12 + abs(v) * 90))
    return f"background: color-mix(in srgb, var({var}) {strength}%, transparent); color: {'#fff' if abs(v) > 0.45 else 'inherit'};"


@register.filter
def duration(seconds) -> str:
    try:
        s = int(float(seconds))
    except (TypeError, ValueError):
        return ""
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60:02d}s"
    return f"{s // 3600}h {(s % 3600) // 60:02d}m"


@register.filter
def trait_pct(value) -> str:
    try:
        return f"{float(value) * 100:.0f}%"
    except (TypeError, ValueError):
        return "0%"


@register.simple_tag
def stance_badge(value, label: str = ""):
    cls = stance_class(value)
    text = label or stance_label(value)
    return format_html('<span class="badge stance-{}">{}</span>', cls, text)


@register.filter
def lines(value) -> str:
    """Join a list into newline-separated text (for <textarea> editing)."""
    if not value:
        return ""
    if isinstance(value, str):
        return value
    return mark_safe("\n".join(escape(str(v)) for v in value))
