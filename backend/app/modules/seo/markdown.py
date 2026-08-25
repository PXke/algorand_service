"""Minimal, dependency-free Markdown -> HTML / plain-text conversion.

Article bodies are Markdown produced by the writer pipeline. We only need a
faithful-enough rendering for crawlers (the `#ssr-body` content and the JSON-LD
`articleBody`), not a full CommonMark implementation. Everything is HTML-escaped
first so generated content can never inject markup.
"""

from __future__ import annotations

import html
import re

from app.core.config import settings

_INLINE_CODE = re.compile(r"`([^`]+)`")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC = re.compile(r"(?<!\*)\*([^*]+)\*(?!\*)")
# The URL is either absolute (http/https) or site-relative (a leading "/", as
# produced by the glossary auto-linker's `[text](/glossary/slug "title")` —
# see workers/app/modules/newspaper/glossary_linker.py). An optional
# `"title"` (CommonMark link-title syntax) may follow the URL; it's matched so
# it doesn't spill into the href, but it isn't rendered (matching prior
# behavior for plain links, which never had titles).
_LINK = re.compile(r'\[([^\]]+)\]\(((?:https?://|/)[^\s)]+)(?:\s+"[^"]*")?\)')
_IMAGE = re.compile(r'!\[([^\]]*)\]\(((?:https?://|/)[^\s)]+)(?:\s+"[^"]*")?\)')
_IMG_ONLY_P = re.compile(r"^<p><img[^>]*></p>$")


def _href(url: str) -> str:
    """Site-relative URLs (glossary links) need an absolute href once they leave the site's own HTML — RSS readers and other off-site consumers have no base URL to resolve `/glossary/x` against."""
    if url.startswith(("http://", "https://")):
        return url
    return f"{settings.public_site_url.rstrip('/')}/{url.lstrip('/')}"


def _inline(text: str) -> str:
    """Escape then apply inline Markdown. Links/code use placeholders so their inner text is not re-escaped or re-matched."""
    out = html.escape(text, quote=False)
    out = _IMAGE.sub(
        lambda m: (
            f'<img src="{html.escape(_href(m.group(2)), quote=True)}" '
            f'alt="{html.escape(m.group(1), quote=True)}">'
        ),
        out,
    )
    out = _LINK.sub(
        lambda m: (
            f'<a href="{html.escape(_href(m.group(2)), quote=True)}" '
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


class _MdBlockParser:
    """Line-by-line accumulator state for md_to_html's block-level parser."""

    def __init__(self) -> None:
        self.out: list[str] = []
        self.list_tag: str | None = None  # "ul" | "ol" while inside a list
        self.para: list[str] = []
        self.fence_info: str | None = None  # fence language while inside ``` ... ```
        self.fence_lines: list[str] = []
        self.table_rows: list[str] = []

    def flush_para(self) -> None:
        if self.para:
            rendered = f"<p>{_inline(' '.join(self.para).strip())}</p>"
            self.para.clear()
            # Writers often lead the body with the same image markdown twice
            # back-to-back (e.g. a hero embed immediately followed by a
            # duplicate the writer pipeline also inserted) — collapse an
            # image-only paragraph that's an exact repeat of the previous one
            # rather than rendering the same picture twice in a row.
            if self.out and self.out[-1] == rendered and _IMG_ONLY_P.fullmatch(rendered):
                return
            self.out.append(rendered)

    def close_list(self) -> None:
        if self.list_tag:
            self.out.append(f"</{self.list_tag}>")
            self.list_tag = None

    def flush_table(self) -> None:
        if self.table_rows:
            self.out.append(_table_html(self.table_rows))
            self.table_rows.clear()

    def _handle_fence(self, stripped: str, line: str) -> bool:
        """Consume a line that's inside, or opens, a fenced block. Returns whether the line was consumed."""
        if self.fence_info is not None:
            if stripped.startswith("```"):
                block = _fence_html(self.fence_info, self.fence_lines)
                if block:
                    self.out.append(block)
                self.fence_info = None
                self.fence_lines.clear()
            else:
                self.fence_lines.append(line)
            return True
        if stripped.startswith("```"):
            self.flush_para()
            self.close_list()
            self.flush_table()
            self.fence_info = stripped[3:].strip().lower()
            return True
        return False

    def _handle_table(self, stripped: str) -> bool:
        if stripped.startswith("|") and stripped.count("|") >= 2:
            self.flush_para()
            self.close_list()
            self.table_rows.append(stripped)
            return True
        return False

    def _handle_block(self, stripped: str) -> bool:
        """Heading, horizontal rule, list item, or blockquote. Returns whether the line was consumed."""
        heading = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading:
            self.flush_para()
            self.close_list()
            level = len(heading.group(1))
            self.out.append(f"<h{level}>{_inline(heading.group(2).strip())}</h{level}>")
            return True
        if re.match(r"^([-*_])\1{2,}$", stripped):  # --- *** ___ horizontal rule
            self.flush_para()
            self.close_list()
            self.out.append("<hr>")
            return True
        ol = re.match(r"^\d+\.\s+(.*)$", stripped)
        ul = re.match(r"^[-*+]\s+(.*)$", stripped)
        if ol or ul:
            self.flush_para()
            want = "ol" if ol else "ul"
            if self.list_tag != want:
                self.close_list()
                self.out.append(f"<{want}>")
                self.list_tag = want
            self.out.append(f"<li>{_inline((ol or ul).group(1).strip())}</li>")
            return True
        if stripped.startswith(">"):
            self.flush_para()
            self.close_list()
            self.out.append(f"<blockquote>{_inline(stripped[1:].strip())}</blockquote>")
            return True
        return False

    def feed(self, raw: str) -> None:
        line = raw.rstrip()
        stripped = line.strip()
        if self._handle_fence(stripped, line) or self._handle_table(stripped):
            return
        self.flush_table()
        if not stripped:
            self.flush_para()
            self.close_list()
            return
        if self._handle_block(stripped):
            return
        self.para.append(stripped)

    def finish(self) -> str:
        self.flush_para()
        self.close_list()
        self.flush_table()
        if self.fence_info is not None:  # unterminated fence — flush what was collected
            block = _fence_html(self.fence_info, self.fence_lines)
            if block:
                self.out.append(block)
        return "\n".join(self.out)


def md_to_html(md: str) -> str:
    """Block-level Markdown -> HTML covering headings, lists, tables, fenced blocks (charts get a caption, not raw JSON), blockquotes, rules and paragraphs."""
    parser = _MdBlockParser()
    for raw in (md or "").replace("\r\n", "\n").split("\n"):
        parser.feed(raw)
    return parser.finish()


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
