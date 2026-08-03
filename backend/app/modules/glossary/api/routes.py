"""Public JSON API for the glossary — published terms only, no admin auth."""

from __future__ import annotations

from dataclasses import asdict

from robyn import Request, Response, Robyn

from app.core.http_errors import json_error_response
from app.core.query_params import query_param


def list_glossary(request: Request) -> Response:
    """Every published glossary term, English (client-side index page)."""
    _ = request
    from app.modules.glossary.store import list_terms

    terms = list_terms(published_only=True)
    return {"items": [asdict(t) for t in terms]}


def get_glossary_term(request: Request) -> Response:
    """One published glossary term, resolved to ?lang= when given."""
    from app.modules.glossary.store import STATUS_PUBLISHED, get_term

    slug = request.path_params.get("slug", "").strip().lower()
    if not slug:
        return json_error_response(400, "invalid_request", "slug required")
    lang = query_param(request.query_params.get("lang", "")) or None
    term = get_term(slug, lang=lang)
    if term is None or term.status != STATUS_PUBLISHED:
        return json_error_response(404, "not_found", "Glossary entry not found")
    return asdict(term)


def register_glossary_routes(app: Robyn) -> None:
    """Attach the public glossary JSON API to the app."""
    app.get("/api/v1/glossary")(list_glossary)
    app.get("/api/v1/glossary/:slug")(get_glossary_term)
