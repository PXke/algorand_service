"""OpenAPI 3.1 document generated from the x402 catalog's live route roster.

Reads `app.modules.x402_catalog.services.catalog.build_catalog()` -- the same
document `/.well-known/x402` re-serves -- rather than re-deriving a second
copy of the route list from `PRODUCTS`/`CatalogRoute`/`Product` directly.
Adding, removing, repricing or re-gating a product route only ever requires
touching catalog.py's roster; this module reshapes whatever `build_catalog()`
already produced (which already re-evaluates each product's live enable
gate, so an OpenAPI path only appears here when the route is actually
registered).

x402 payment auth has no standard OpenAPI security scheme -- it settles via
the HTTP 402 challenge-response on the wire (offer in the `Payment-Required`
header, a signed payment on retry), not a bearer token, API key or cookie --
so no `securitySchemes`/`security` entry is declared. Each paid operation
instead carries `x-x402-*` vendor-extension fields (price, ledger resource
id, preview support) and a `402` response entry; `info.description` spells
this out for a reader unfamiliar with x402.
"""

from __future__ import annotations

import re
from typing import Any

from app.core.config import settings
from app.modules.x402_catalog.services.catalog import build_catalog

WELLKNOWN_PATH = "/.well-known/x402"
OPENAPI_PATH = "/openapi.json"

_PARAM_RE = re.compile(r":([A-Za-z_][A-Za-z0-9_]*)")
_SLUG_RE = re.compile(r"[^a-zA-Z0-9]+")

_AUTH_NOTE = (
    "Paid routes are gated by the x402 payment protocol (HTTP 402 challenge-response, "
    "settled on-chain) -- not a bearer token, API key or cookie. There is no ratified "
    "OpenAPI `securitySchemes` entry for x402, so none is declared here; look at each paid "
    "operation's `x-x402-*` extension fields and its `402` response instead. A client calls "
    "a paid route unauthenticated, reads the payment offer encoded in the 402 response's "
    "Payment-Required header, builds and signs a payment, and retries with it attached. "
    "See GET /.well-known/x402 (or GET /api/v1/x402) for the marketplace-wide facts behind "
    "every offer: network, pay-to address, accepted assets, facilitator URL."
)


def _openapi_path(path: str) -> str:
    """Falcon/catalog's `:param` path-segment convention, rewritten to OpenAPI's `{param}`."""
    return _PARAM_RE.sub(lambda m: "{" + m.group(1) + "}", path)


def _path_param_names(path: str) -> list[str]:
    return _PARAM_RE.findall(path)


def _json_schema_type(value: object) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "string"


def _operation_id(route: dict[str, Any]) -> str:
    slug = _SLUG_RE.sub("_", route["path"]).strip("_")
    return f"{route['method'].lower()}_{slug}"


def _paid_description(route: dict[str, Any]) -> str:
    """Append the structured price/resource note used on paid OpenAPI operations."""
    unit = route.get("price_unit")
    price_note = f"price_usd={route['price_usd']!r}"
    if unit:
        price_note += f" per {unit}"
    return f"{route['description']} Paid: {price_note}, resource={route['resource']!r}."


def _apply_paid_extensions(operation: dict[str, Any], route: dict[str, Any]) -> None:
    """Set the x-x402-* vendor fields every paid operation carries."""
    operation["x-x402-price-usd"] = route["price_usd"]
    if route.get("price_unit"):
        operation["x-x402-price-unit"] = route["price_unit"]
    operation["x-x402-resource"] = route["resource"]
    operation["x-x402-supports-preview"] = route["supports_preview"]


def _operation(route: dict[str, Any]) -> dict[str, Any]:
    """One OpenAPI operation object for one catalog route dict (see catalog._route_json)."""
    param_names = _path_param_names(route["path"])
    parameters: list[dict[str, Any]] = [
        {"name": name, "in": "path", "required": True, "schema": {"type": "string"}}
        for name in param_names
    ]

    description = _paid_description(route) if route["paid"] else route["description"]

    example = route["input_example"]
    request_body: dict[str, Any] | None = None
    if example is not None:
        if route["method"] in {"POST", "PUT", "PATCH"}:
            request_body = {
                "required": True,
                "content": {"application/json": {"schema": {"type": "object"}, "example": example}},
            }
        else:
            for key, value in example.items():
                if key in param_names:
                    continue
                parameters.append(
                    {
                        "name": key,
                        "in": "query",
                        "required": False,
                        "schema": {"type": _json_schema_type(value)},
                        "example": value,
                    }
                )

    responses: dict[str, Any] = {
        "200": {
            "description": "Success.",
            "content": {"application/json": {"schema": {"type": "object"}}},
        },
    }
    if route["paid"]:
        responses["402"] = {
            "description": (
                "Payment required. The x402 payment offer (accepted assets, price, "
                "facilitator) is carried in the Payment-Required response header."
            ),
        }

    operation: dict[str, Any] = {
        "operationId": _operation_id(route),
        "summary": route["description"],
        "description": description,
        "tags": [route["product"]],
        "responses": responses,
        "x-x402-paid": route["paid"],
    }
    if route["paid"]:
        _apply_paid_extensions(operation, route)
    if parameters:
        operation["parameters"] = parameters
    if request_body is not None:
        operation["requestBody"] = request_body
    return operation


def _meta_operation(*, description: str) -> dict[str, Any]:
    """A minimal operation entry for the two bootstrap routes this module itself serves.

    Not in `build_catalog()`'s roster (they are the discovery bootstrap, not
    a marketplace product), so they are added here directly rather than
    invented as fake catalog entries.
    """
    return {
        "summary": description,
        "description": description,
        "tags": ["meta"],
        "responses": {
            "200": {
                "description": "Success.",
                "content": {"application/json": {"schema": {"type": "object"}}},
            }
        },
        "x-x402-paid": False,
    }


def build_openapi() -> dict[str, Any]:
    """An OpenAPI 3.1 document for every currently-live x402 route plus this module's own two."""
    catalog = build_catalog()
    paths: dict[str, dict[str, Any]] = {}
    for route in catalog["routes"]:
        openapi_path = _openapi_path(route["path"])
        paths.setdefault(openapi_path, {})[route["method"].lower()] = _operation(route)

    paths[WELLKNOWN_PATH] = {
        "get": _meta_operation(
            description=(
                "This marketplace's discovery manifest -- the same document as "
                "GET /api/v1/x402, served at the well-known URI agents and directories "
                "look for first."
            )
        )
    }
    paths[OPENAPI_PATH] = {"get": _meta_operation(description="This OpenAPI document.")}

    return {
        "openapi": "3.1.0",
        "info": {
            "title": catalog["name"],
            "version": "1.0.0",
            "description": _AUTH_NOTE,
            "contact": {"email": "px9e@proton.me"},
        },
        "servers": [{"url": settings.x402_public_api_base}],
        "paths": paths,
    }
