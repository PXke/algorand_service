"""request.query_params.get(...) must always pass an explicit default.

query_params is a plain dict, so an omitted default silently returns None —
and downstream code (`.strip()`, `int(...)`, string concatenation) then
raises AttributeError/TypeError at request time, not at import time — so
ruff, svelte-check and the whole suite stay green while the route 500s in
production. That is exactly how /api/v1/admin/compose-sessions shipped
broken on 2026-07-29. This pins the call shape across the admin routes.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROUTES = Path(__file__).resolve().parents[1] / "app" / "modules" / "admin" / "api" / "routes.py"


def test_query_params_get_always_passes_a_default() -> None:
    """Every request.query_params.get(...) must supply the default argument."""
    tree = ast.parse(ROUTES.read_text(encoding="utf-8"))
    offenders: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "get":
            continue
        target = node.func.value
        # Match `<anything>.query_params.get(...)`
        if (
            isinstance(target, ast.Attribute)
            and target.attr == "query_params"
            and len(node.args) < 2
        ):
            offenders.append(node.lineno)
    assert not offenders, (
        f"{ROUTES.name}: query_params.get() without a default at lines {offenders}. "
        f"An omitted default silently returns None — omitting it is a 500 at request time only."
    )
