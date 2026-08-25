"""Public JSON API for the glossary — published terms only, no admin auth."""

from __future__ import annotations

from dataclasses import asdict

from app.core import serialization
from app.core.http import Request, Response, Router
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


def list_glossary_term_articles(request: Request) -> Response:
    """Published articles that reference this glossary term (by stable slug, not display text -- see SearchService.list_by_glossary_slug), for the term page's "referenced in" list."""
    from app.modules.search.services.search_service import SearchService

    slug = request.path_params.get("slug", "").strip().lower()
    if not slug:
        return json_error_response(400, "invalid_request", "slug required")
    lang = query_param(request.query_params.get("lang", "")) or None
    limit_param = query_param(request.query_params.get("limit", "20"))
    limit = int(limit_param) if limit_param.isdigit() else 20
    limit = min(max(1, limit), 50)
    items = SearchService().list_by_glossary_slug(slug, limit=limit, lang=lang)
    return {"items": serialization.to_builtins(items)}


def register_glossary_routes(app: Router) -> None:
    """Attach the public glossary JSON API to the app."""
    app.get("/api/v1/glossary")(list_glossary)
    app.get("/api/v1/glossary/:slug")(get_glossary_term)
    app.get("/api/v1/glossary/:slug/articles")(list_glossary_term_articles)
