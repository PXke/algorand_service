"""Minimal, dependency-free Markdown -> HTML / plain-text conversion.

Article bodies are Markdown produced by the writer pipeline. We only need a
faithful-enough rendering for crawlers (the `#ssr-body` content and the JSON-LD
`articleBody`), not a full CommonMark implementation. Everything is HTML-escaped
first so generated content can never inject markup.
"""

from __future__ import annotations

import html
import re

_INLINE_CODE = re.compile(r"`([^`]+)`")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC = re.compile(r"(?<!\*)\*([^*]+)\*(?!\*)")
_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
_IMAGE = re.compile(r"!\[([^\]]*)\]\((https?://[^\s)]+)\)")


def _inline(text: str) -> str:
    """Escape then apply inline Markdown. Links/code use placeholders so their inner text is not re-escaped or re-matched."""
    out = html.escape(text, quote=False)
    out = _IMAGE.sub(
        lambda m: (
            f'<img src="{html.escape(m.group(2), quote=True)}" '
            f'alt="{html.escape(m.group(1), quote=True)}">'
        ),
        out,
    )
    out = _LINK.sub(
        lambda m: (
            f'<a href="{html.escape(m.group(2), quote=True)}" '
            f'rel="noopener nofollow">{m.group(1)}</a>'
        ),
        out,
    )
    out = _INLINE_CODE.sub(lambda m: f"<code>{m.group(1)}</code>", out)
    out = _BOLD.sub(lambda m: f"<strong>{m.group(1)}</strong>", out)
    return _ITALIC.sub(lambda m: f"<em>{m.group(1)}</em>", out)


def _is_table_separator(cells: list[str]) -> bool:
    real = [c for c in cells if c]
    return bool(real) and all(re.fullmatch(r":?-{2,}:?", c) for c in real)


def _table_html(rows: list[str]) -> str:
    parsed = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows]
    header: list[str] | None = None
    if len(parsed) > 1 and _is_table_separator(parsed[1]):
        header = parsed[0]
        parsed = [p for p in parsed[2:] if not _is_table_separator(p)]
    else:
        parsed = [p for p in parsed if not _is_table_separator(p)]
    out = ["<table>"]
    if header:
        cells = "".join(f"<th>{_inline(c)}</th>" for c in header)
        out.append(f"<thead><tr>{cells}</tr></thead>")
    out.append("<tbody>")
    out.extend(
        "<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in cells) + "</tr>" for cells in parsed
    )
    out.append("</tbody></table>")
    return "".join(out)


def _fence_html(info: str, lines: list[str]) -> str:
    """A fenced block. ``chart`` fences carry the platform's chart JSON — the Flutter app draws them, so the SSR body keeps only a readable caption (raw JSON in the visible body / articleBody reads as broken output to crawlers). Any other fence renders as an ordinary code block."""
    if info == "chart":
        title = ""
        try:
            import json as _json

            title = str(_json.loads("\n".join(lines)).get("title", "") or "")
        except Exception:
            title = ""
        return f"<p><em>Chart: {html.escape(title)}</em></p>" if title else ""
    code = html.escape("\n".join(lines), quote=False)
    return f"<pre><code>{code}</code></pre>"


def md_to_html(md: str) -> str:
    """Block-level Markdown -> HTML covering headings, lists, tables, fenced blocks (charts get a caption, not raw JSON), blockquotes, rules and paragraphs."""
    lines = (md or "").replace("\r\n", "\n").split("\n")
    out: list[str] = []
    list_tag: str | None = None  # "ul" | "ol" while inside a list
    para: list[str] = []
    fence_info: str | None = None  # fence language while inside ``` ... ```
    fence_lines: list[str] = []
    table_rows: list[str] = []

    def flush_para() -> None:
        if para:
            out.append(f"<p>{_inline(' '.join(para).strip())}</p>")
            para.clear()

    def close_list() -> None:
        nonlocal list_tag
        if list_tag:
            out.append(f"</{list_tag}>")
            list_tag = None

    def flush_table() -> None:
        if table_rows:
            out.append(_table_html(table_rows))
            table_rows.clear()

    for raw in lines:
        line = raw.rstrip()
        stripped = line.strip()
        if fence_info is not None:
            if stripped.startswith("```"):
                block = _fence_html(fence_info, fence_lines)
                if block:
                    out.append(block)
                fence_info = None
                fence_lines.clear()
            else:
                fence_lines.append(line)
            continue
        if stripped.startswith("```"):
            flush_para()
            close_list()
            flush_table()
            fence_info = stripped[3:].strip().lower()
            continue
        if stripped.startswith("|") and stripped.count("|") >= 2:
            flush_para()
            close_list()
            table_rows.append(stripped)
            continue
        flush_table()
        if not stripped:
            flush_para()
            close_list()
            continue
        heading = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading:
            flush_para()
            close_list()
            level = len(heading.group(1))
            out.append(f"<h{level}>{_inline(heading.group(2).strip())}</h{level}>")
            continue
        if re.match(r"^([-*_])\1{2,}$", stripped):  # --- *** ___ horizontal rule
            flush_para()
            close_list()
            out.append("<hr>")
            continue
        ol = re.match(r"^\d+\.\s+(.*)$", stripped)
        ul = re.match(r"^[-*+]\s+(.*)$", stripped)
        if ol or ul:
            flush_para()
            want = "ol" if ol else "ul"
            if list_tag != want:
                close_list()
                out.append(f"<{want}>")
                list_tag = want
            out.append(f"<li>{_inline((ol or ul).group(1).strip())}</li>")
            continue
        if stripped.startswith(">"):
            flush_para()
            close_list()
            out.append(f"<blockquote>{_inline(stripped[1:].strip())}</blockquote>")
            continue
        para.append(stripped)

    flush_para()
    close_list()
    flush_table()
    if fence_info is not None:  # unterminated fence — flush what was collected
        block = _fence_html(fence_info, fence_lines)
        if block:
            out.append(block)
    return "\n".join(out)


def md_to_text(md: str) -> str:
    """Strip Markdown to readable plain text (for meta descriptions and the JSON-LD articleBody)."""
    text = md or ""
    # Chart fences carry JSON for the app's chart widget — data, not prose.
    text = re.sub(r"```chart\b.*?(```|\Z)", "", text, flags=re.DOTALL)
    text = re.sub(r"^```[^\n]*$", "", text, flags=re.MULTILINE)  # other fence markers
    # Tables: drop separator rows, then flatten cell pipes to spaces.
    text = re.sub(r"^\|?[\s:|-]+\|[\s:|-]*$", "", text, flags=re.MULTILINE)
    text = text.replace("|", " ")
    text = _IMAGE.sub("", text)
    text = _LINK.sub(lambda m: m.group(1), text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"\1", text)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^>\s?", "", text, flags=re.MULTILINE)
    text = re.sub(r"^[-*+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\d+\.\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def truncate(text: str, limit: int = 160) -> str:
    """Trim to a meta-description-friendly length on a word boundary."""
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip(",.;: ") + "…"
