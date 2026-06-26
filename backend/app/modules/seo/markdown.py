"""Minimal, dependency-free Markdown -> HTML / plain-text conversion.

Article bodies are Markdown produced by the writer pipeline. We only need a
faithful-enough rendering for crawlers (the `<noscript>` body and the JSON-LD
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
    """Escape then apply inline Markdown. Links/code use placeholders so their
    inner text is not re-escaped or re-matched."""
    out = html.escape(text, quote=False)
    out = _IMAGE.sub(lambda m: f'<img src="{html.escape(m.group(2), quote=True)}" '
                               f'alt="{html.escape(m.group(1), quote=True)}">', out)
    out = _LINK.sub(
        lambda m: f'<a href="{html.escape(m.group(2), quote=True)}" '
                  f'rel="noopener nofollow">{m.group(1)}</a>',
        out,
    )
    out = _INLINE_CODE.sub(lambda m: f"<code>{m.group(1)}</code>", out)
    out = _BOLD.sub(lambda m: f"<strong>{m.group(1)}</strong>", out)
    out = _ITALIC.sub(lambda m: f"<em>{m.group(1)}</em>", out)
    return out


def md_to_html(md: str) -> str:
    """Block-level Markdown -> HTML covering headings, lists, blockquotes,
    rules and paragraphs."""
    lines = (md or "").replace("\r\n", "\n").split("\n")
    out: list[str] = []
    list_tag: str | None = None  # "ul" | "ol" while inside a list
    para: list[str] = []

    def flush_para() -> None:
        if para:
            out.append(f"<p>{_inline(' '.join(para).strip())}</p>")
            para.clear()

    def close_list() -> None:
        nonlocal list_tag
        if list_tag:
            out.append(f"</{list_tag}>")
            list_tag = None

    for raw in lines:
        line = raw.rstrip()
        stripped = line.strip()
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
    return "\n".join(out)


def md_to_text(md: str) -> str:
    """Strip Markdown to readable plain text (for meta descriptions and the
    JSON-LD articleBody)."""
    text = md or ""
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
