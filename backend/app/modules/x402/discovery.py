"""A `declare_discovery_extension` wrapper that cannot reproduce the OutputConfig bug.

Both KYC paid routes (see the 2026-08-30 fix) called
`declare_discovery_extension(..., output={"example": {...}})` -- a plain
dict -- because that is exactly what the challenge's own submission-guide
docs show. The installed x402-avm==2.0.2 package reads `output.example` as
an attribute, so a bare dict 500s the route before it ever emits a 402.
x402_directory's route got this right by hand; KYC's two routes did not.

describe_json_endpoint exists so no future module has to remember the
OutputConfig wrapping by hand at every call site -- it is structurally
impossible to pass a bare dict here.
"""

from __future__ import annotations

from typing import Any

from x402.extensions.bazaar import declare_discovery_extension
from x402.extensions.bazaar.resource_service import OutputConfig
from x402.extensions.bazaar.types import BodyType


def describe_json_endpoint(
    *,
    input: dict[str, Any] | None = None,  # noqa: A002 -- matches the wrapped function's own param name
    input_schema: dict[str, Any] | None = None,
    output_example: dict[str, Any] | None = None,
    body_type: BodyType | None = None,
) -> dict[str, Any]:
    """Declare a Bazaar discovery extension for a JSON endpoint.

    `body_type="json"` for EVERY POST/PUT/PATCH route, including one that
    takes no body at all (a path-parameter-only action such as a vote,
    follow or renew): the query-params declaration's schema only admits
    `method` in GET/HEAD/DELETE, and the resource server injects the real
    method at request time, so a body-method route declared without
    body_type emits an extension that fails the facilitator's own
    `validate_discovery_extension` and is silently never catalogued in the
    Bazaar (found live 2026-09-05 on the feature-vote route). With
    body_type="json" and no `input`, the declared example body is `{}`,
    which validates. Leave it None only for a GET/HEAD/DELETE whose input is
    query params.
    """
    return declare_discovery_extension(
        input=input,
        input_schema=input_schema,
        body_type=body_type,
        output=OutputConfig(example=output_example) if output_example is not None else None,
    )
