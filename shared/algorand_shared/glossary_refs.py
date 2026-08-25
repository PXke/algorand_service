"""Which glossary terms an article body references, for search cross-referencing.

The glossary auto-linker (workers/app/modules/newspaper/glossary_linker.py)
deterministically inserts `[text](/glossary/slug "definition")` links into an
article's English body at compose time. Translation then carries that
markdown straight through -- the anchor text and title get translated, but
the `/glossary/slug` href is a URL, not prose, so it survives unchanged. That
makes the SLUG (not the term's display text) the stable, locale-independent
key: an article's set of referenced glossary slugs is the same whether you
scan its English body or its French one.

Lives here (not duplicated backend/workers copies) so the extraction regex
can't drift between the two upsert_article_document implementations the way
indexnow.py once did (see shared/algorand_shared/__init__.py) -- one wrong
copy would silently under- or over-populate the Typesense
`glossary_slugs` field on whichever service's write path forgot to update.
"""

from __future__ import annotations

import re

# Mirrors glossary_linker.py's own _EXISTING_GLOSSARY_SLUG_RE: a slug is
# always followed by either the closing `)` (no title) or whitespace before
# a `"title"` segment -- never anything else, since the linker is the only
# thing that ever writes this link shape into a body.
_GLOSSARY_LINK_RE = re.compile(r"\]\(/glossary/([a-z0-9-]+)(?=[\s)])")


def extract_glossary_slugs(*bodies: str | None) -> list[str]:
    """Slugs of every glossary term linked across all given markdown bodies.

    Pass the English body plus every stored translation's body -- the same
    slug found in any of them is unioned into one sorted, de-duplicated
    list. A missing/empty body is simply skipped, so callers can pass
    `article.body, *translated_bodies` without pre-filtering.
    """
    slugs: set[str] = set()
    for body in bodies:
        if not body:
            continue
        slugs.update(_GLOSSARY_LINK_RE.findall(body))
    return sorted(slugs)
