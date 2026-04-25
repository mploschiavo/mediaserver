"""OpenAPI-driven response contract validator.

What this exists for
--------------------
Three sources of truth must agree for a dashboard tile to render:

    1. ``openapi.yaml``         — the contract
    2. handler response body    — what flows over the wire
    3. UI TypeScript types      — what the dashboard compiles against

Drift between any two surfaces as user-visible bugs (silent fallbacks,
empty tables, "12/31/1969" timestamps). The historic catch — hand
review of PRs — has missed several of them. This validator turns the
spec into a machine-checkable assertion: given a path and a response
body, return the list of schema violations.

Used by
-------
- ``tests/unit/test_api_response_contract.py``: validates committed
  fixture responses (captured from a live cluster) against
  ``openapi.yaml``. CI fails when the spec or live shape drifts.
- ``tests/unit/test_*.py``: any handler unit test that wants to
  assert response-shape compliance. Pass the captured response dict
  to ``validate_response(path, body)`` and assert ``[] == errors``.

What the validator catches
--------------------------
- Live emits a key not in the schema (only when
  ``additionalProperties: false`` or absent).
- Live missing a ``required`` key.
- Type drift (string vs number, object vs array).
- ``$ref`` to a missing component.
- Nested-property drift (recursive).

What it does NOT catch
----------------------
- TypeScript-side drift (handled by ``no-handrolled-shapes.test.ts``).
- Cross-field invariants (e.g. ``status == "ok" → ms is not null``)
  — write a domain test for those.
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator, RefResolver
from jsonschema.exceptions import ValidationError

_SPEC_PATH = (
    Path(__file__).resolve().parents[3] / "contracts" / "api" / "openapi.yaml"
)


@functools.lru_cache(maxsize=1)
def _load_spec() -> dict[str, Any]:
    """Load and cache the OpenAPI spec. The lru_cache means tests
    that hit the validator hundreds of times only pay the YAML
    parse once."""
    with _SPEC_PATH.open("r", encoding="utf-8") as fh:
        spec = yaml.safe_load(fh)
    if not isinstance(spec, dict):
        raise RuntimeError(f"openapi.yaml did not parse to a dict: {_SPEC_PATH}")
    return spec


def is_planned(spec: dict[str, Any], path: str) -> bool:
    """True when the endpoint is marked ``x-status: planned`` — i.e.
    documented in the spec ahead of the implementation. The contract
    test skips planned endpoints because their live response is a
    stub by design, not a contract violation."""
    paths = spec.get("paths") or {}
    entry = paths.get(path)
    if not isinstance(entry, dict):
        return False
    get_op = entry.get("get")
    if not isinstance(get_op, dict):
        return False
    return str(get_op.get("x-status") or "").lower() == "planned"


def _resolve_get_200_schema(
    spec: dict[str, Any], path: str,
) -> dict[str, Any] | None:
    """Walk the spec to ``paths.<path>.get.responses.'200'.content.
    application/json.schema``. Returns the schema dict or ``None`` if
    any leg is missing — the caller should treat ``None`` as "no
    contract declared" and skip rather than fail."""
    paths = spec.get("paths") or {}
    entry = paths.get(path)
    if not isinstance(entry, dict):
        return None
    get_op = entry.get("get")
    if not isinstance(get_op, dict):
        return None
    responses = get_op.get("responses") or {}
    # OpenAPI allows the 200 key as either int or str depending on
    # how the YAML was authored. Try both forms.
    r200 = responses.get("200") or responses.get(200)
    if not isinstance(r200, dict):
        return None
    content = r200.get("content") or {}
    media = content.get("application/json")
    if not isinstance(media, dict):
        return None
    schema = media.get("schema")
    if not isinstance(schema, dict):
        return None
    return schema


def _normalize_openapi_3_0(node: Any) -> Any:
    """Translate OpenAPI 3.0 idioms into JSON Schema 2020-12 so the
    jsonschema validator honors them.

    Specifically:
    * ``nullable: true`` + ``type: X``  →  ``type: [X, "null"]`` and
      drop the ``nullable`` key. OpenAPI 3.0 uses ``nullable`` to
      mean "may also be null"; OpenAPI 3.1 / JSON Schema 2020-12
      use the array form. Without this rewrite, every nullable
      field in the spec produces a false "None is not of type X"
      validation error.
    * No-op for any other shape.

    Recurses into nested ``properties``, ``items``, ``oneOf``,
    ``anyOf``, ``allOf``, and ``additionalProperties`` so it covers
    nested schemas as well."""
    if isinstance(node, list):
        return [_normalize_openapi_3_0(x) for x in node]
    if not isinstance(node, dict):
        return node
    out = {k: _normalize_openapi_3_0(v) for k, v in node.items()}
    if out.pop("nullable", False) is True:
        existing_type = out.get("type")
        if isinstance(existing_type, str) and existing_type != "null":
            out["type"] = [existing_type, "null"]
        elif isinstance(existing_type, list) and "null" not in existing_type:
            out["type"] = list(existing_type) + ["null"]
    return out


def _build_validator(
    spec: dict[str, Any], schema: dict[str, Any],
) -> Draft202012Validator:
    """Build a jsonschema validator with ``#/components/schemas/...``
    references resolved against the same spec. Without the resolver,
    every ``$ref`` would error out as "not resolvable" — handlers that
    return a top-level ``$ref`` (the canonical OpenAPI pattern) would
    be impossible to validate.

    The schema and spec are run through ``_normalize_openapi_3_0``
    so OpenAPI-3.0-style ``nullable: true`` declarations carry the
    intended "may be null" semantic into the 2020-12 validator."""
    normalized_schema = _normalize_openapi_3_0(schema)
    normalized_spec = _normalize_openapi_3_0(spec)
    resolver = RefResolver.from_schema(normalized_spec)
    return Draft202012Validator(normalized_schema, resolver=resolver)


def validate_response(path: str, body: Any) -> list[str]:
    """Validate ``body`` against the GET 200-response schema for
    ``path``. Returns a list of human-readable error strings (empty
    when the body fully complies). When the path has no schema in
    the spec, returns ``["no schema declared for {path}"]`` —
    treating "spec doesn't know about this endpoint" as an error
    keeps the ratchet honest. Callers with an intentional reason
    to skip can compare-then-ignore.

    ``x-status: planned`` endpoints are skipped — their live response
    is a stub by spec design, not a contract violation. Once the
    implementation lands, the spec author drops the marker and the
    full validator kicks in.

    Errors are formatted with the JSON pointer to the offending
    field so test failures are immediately actionable, e.g.:
        ``$.live[0].item_count: 'foo' is not of type 'integer'``
    """
    spec = _load_spec()
    if is_planned(spec, path):
        return []
    schema = _resolve_get_200_schema(spec, path)
    if schema is None:
        return [f"no schema declared for GET {path} 200 response"]
    validator = _build_validator(spec, schema)
    errors: list[str] = []
    for err in validator.iter_errors(body):
        ptr = "$" + "".join(f"[{p!r}]" for p in err.absolute_path)
        errors.append(f"{ptr}: {err.message}")
    return errors


def validate_response_strict(path: str, body: Any) -> list[str]:
    """Like ``validate_response`` but ALSO flags any top-level key
    in ``body`` that isn't declared in the schema's ``properties``,
    even when ``additionalProperties: true`` would normally permit
    it. This is the ratchet mode: extra keys mean either (a) the
    spec is stale and a real field was added without doc, or (b) a
    handler accidentally leaked an internal field. Both want
    operator attention.

    Skips ``x-status: planned`` endpoints (same rationale as
    ``validate_response``). The vanilla ``validate_response`` is
    more permissive — use it when the freeform shape is intentional
    (e.g. ``/api/env``)."""
    errors = validate_response(path, body)
    spec = _load_spec()
    if is_planned(spec, path):
        return []
    schema = _resolve_get_200_schema(spec, path)
    if schema is None or not isinstance(body, dict):
        return errors
    declared = set((schema.get("properties") or {}).keys())
    if declared:
        extra = sorted(set(body.keys()) - declared)
        if extra:
            errors.append(
                f"undeclared top-level keys in response: {extra}. "
                f"Either tighten the schema or document why these are "
                f"intentionally freeform."
            )
    return errors
