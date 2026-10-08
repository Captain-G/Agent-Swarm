"""
A small, *safe* Markdown renderer.

LLM output is built from scraped web content, so it may contain HTML or
script injection attempts. This renderer escapes everything first and only
then produces a fixed whitelist of tags (headings, paragraphs, lists, tables,
quotes, emphasis, code and http(s) links).
"""

from __future__ import annotations

import html
import re

_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_ITALIC_RE = re.compile(r"(?<![\*\w])\*(?!\s)(.+?)(?<!\s)\*(?![\*\w])")
_CODE_RE = re.compile(r"`([^`]+)`")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_UL_RE = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_OL_RE = re.compile(r"^(\s*)\d+[.)]\s+(.*)$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


def _inline(text: str) -> str:
    text = html.escape(text, quote=False)
    codes: list[str] = []

    def stash(match: re.Match) -> str:
        codes.append(f"<code>{match.group(1)}</code>")
        return f"\x00{len(codes) - 1}\x00"

    text = _CODE_RE.sub(stash, text)
    text = _LINK_RE.sub(
        lambda m: f'<a href="{html.escape(m.group(2), quote=True)}" rel="noopener noreferrer nofollow" target="_blank">{m.group(1)}</a>',
        text,
    )
    text = _BOLD_RE.sub(r"<strong>\1</strong>", text)
    text = _ITALIC_RE.sub(r"<em>\1</em>", text)
    return re.sub(r"\x00(\d+)\x00", lambda m: codes[int(m.group(1))], text)


def _split_row(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [cell.strip() for cell in line.split("|")]


def render_markdown(source: str, heading_offset: int = 0) -> str:
    if not source:
        return ""
    lines = source.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    paragraph: list[str] = []
    list_stack: list[str] = []
    i = 0

    def flush_paragraph():
        if paragraph:
            out.append(f"<p>{_inline(' '.join(p.strip() for p in paragraph))}</p>")
            paragraph.clear()

    def close_lists():
        while list_stack:
            out.append(f"</{list_stack.pop()}>")

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if stripped.startswith("```"):
            flush_paragraph()
            close_lists()
            block = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                block.append(lines[i])
                i += 1
            out.append(f"<pre><code>{html.escape(chr(10).join(block))}</code></pre>")
            i += 1
            continue

        if not stripped:
            flush_paragraph()
            close_lists()
            i += 1
            continue

        heading = _HEADING_RE.match(stripped)
        if heading:
            flush_paragraph()
            close_lists()
            level = min(6, len(heading.group(1)) + heading_offset)
            out.append(f"<h{level}>{_inline(heading.group(2).strip('# '))}</h{level}>")
            i += 1
            continue

        if re.match(r"^(-{3,}|\*{3,}|_{3,})$", stripped):
            flush_paragraph()
            close_lists()
            out.append("<hr>")
            i += 1
            continue

        if "|" in stripped and i + 1 < len(lines) and _TABLE_SEP_RE.match(lines[i + 1]):
            flush_paragraph()
            close_lists()
            header = _split_row(stripped)
            rows = []
            i += 2
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                rows.append(_split_row(lines[i]))
                i += 1
            parts = ['<div class="table-wrap"><table><thead><tr>']
            parts += [f"<th scope=\"col\">{_inline(c)}</th>" for c in header]
            parts.append("</tr></thead><tbody>")
            for row in rows:
                parts.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in row) + "</tr>")
            parts.append("</tbody></table></div>")
            out.append("".join(parts))
            continue

        if stripped.startswith(">"):
            flush_paragraph()
            close_lists()
            quote = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quote.append(lines[i].strip()[1:].strip())
                i += 1
            out.append(f"<blockquote><p>{_inline(' '.join(quote))}</p></blockquote>")
            continue

        ul, ol = _UL_RE.match(line), _OL_RE.match(line)
        if ul or ol:
            flush_paragraph()
            match = ul or ol
            tag = "ul" if ul else "ol"
            depth = len(match.group(1).replace("\t", "  ")) // 2 + 1
            while len(list_stack) > depth:
                out.append(f"</{list_stack.pop()}>")
            while len(list_stack) < depth:
                list_stack.append(tag)
                out.append(f"<{tag}>")
            out.append(f"<li>{_inline(match.group(2))}</li>")
            i += 1
            continue

        close_lists()
        paragraph.append(line)
        i += 1

    flush_paragraph()
    close_lists()
    return "\n".join(out)
