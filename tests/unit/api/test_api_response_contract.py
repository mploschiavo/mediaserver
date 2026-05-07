"""Live-fixture contract test.

What this catches
-----------------
Drift between three sources of truth:

    1. ``openapi.yaml`` (the contract)
    2. The handler's actual response body (this test's input)
    3. The UI's TypeScript types (auto-generated from #1)

For each captured fixture, validate the response against the GET 200
schema declared for its endpoint. Any of the following fail the test:

* a key in the live response that isn't declared in the schema
  (controller leaked an internal field, OR the spec is stale —
  either way operator attention is wanted).
* a ``required`` schema key missing from the response (spec has
  drifted past the handler).
* a type mismatch (handler returns ``"123"`` where spec says
  ``integer``, etc.) — recursive through nested objects/arrays.

How to refresh fixtures
-----------------------
When you intentionally change a response shape (handler edit, new
field, etc.), update the fixture by re-capturing from a running
controller:

    CTRL_POD=$(kubectl -n media-stack get pod -l app=media-stack-controller -o jsonpath='{.items[0].metadata.name}')
    kubectl -n media-stack exec "$CTRL_POD" -- python3 -c "
    import urllib.request, json
    r = urllib.request.urlopen(urllib.request.Request('http://localhost:9100/api/<PATH>', headers={'Remote-User':'admin'}), timeout=10)
    print(json.dumps(json.loads(r.read()), indent=2, sort_keys=True))
    " > tests/fixtures/api_responses/<filename>.json

Then commit the fixture along with the spec/handler change. The test
running this fixture is the second pair of eyes that says "yes, the
new shape matches the new spec."

Adding a new endpoint
---------------------
1. Capture a fixture (above).
2. Add an entry to ``ENDPOINTS`` mapping fixture filename → API path.
3. Run pytest. If validation fails, either tighten ``openapi.yaml`` or
   align the handler.

When NOT to use this test
-------------------------
Endpoints that intentionally return free-form blobs (``/api/env``,
``/api/envvars``, etc.) won't fit the strict-mode check. Add them to
``FREEFORM_ENDPOINTS`` if their lax shape is intentional.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

import yaml  # noqa: E402

from media_stack.api.contract_validator import (  # noqa: E402
    validate_response,
    validate_response_strict,
)

_SPEC_PATH = ROOT / "contracts" / "api" / "openapi.yaml"

_FIXTURES = ROOT / "tests" / "fixtures" / "api_responses"

# Fixture filename → API path mapping.
#
# By default the path is derived mechanically from the filename:
#
#     `foo_bar.json` → `/api/foo/bar`
#     `health.json`  → `/api/health`
#
# That convention covers the ~100 parameter-free GETs the bulk
# capture script writes (see `bin/ops/recapture-all-fixtures.sh`).
# Override only when the fixture lives somewhere the convention
# doesn't reach: legacy paths without the `/api` prefix, dashed
# filenames that should map to slashed paths, etc.
ENDPOINTS_OVERRIDES: dict[str, str] = {
    # Spec declares `/webhooks` as canonical; the handler accepts both
    # /webhooks and /api/webhooks at the same dispatcher branch
    # (see _HANDLER_ONLY_ALLOWLIST in test_openapi_drift_ratchet).
    "webhooks": "/webhooks",
    # K8s liveness/readiness probes — top-level paths, no /api prefix.
    "healthz": "/healthz",
    "readyz": "/readyz",
    # Top-level state inspectors that pre-date the /api prefix
    # convention. Still documented in the spec at their bare paths.
    "apps": "/apps",
    "config": "/config",
    "status": "/status",
    # ADR-0005 Phase 5a added ``/api/status`` as the dashboard-facing
    # alias of ``/status`` (SPA's nginx only proxies ``/api/*`` to
    # the controller). The default ``api_status`` → ``/api/api/status``
    # would double-prefix, so override.
    "api_status": "/api/status",
    # Hyphenated API paths whose underscore-named fixtures collide
    # with the slash-substitution default. Added during ADR-0007
    # Phase 2 wave 3+4 fixture capture.
    "envoy_admin_summary": "/api/envoy/admin-summary",
    "sw_config": "/api/sw-config",
    "sw_config_json": "/sw-config.json",
    "audit_log_stats": "/api/audit-log/stats",
    "disk_guardrails": "/api/disk-guardrails",
}


def _path_from_filename(stem: str) -> str:
    """Map fixture stem to its API path. ``foo_bar`` → ``/api/foo/bar``
    unless an explicit override is registered."""
    if stem in ENDPOINTS_OVERRIDES:
        return ENDPOINTS_OVERRIDES[stem]
    return "/api/" + stem.replace("_", "/")


def _discover_endpoints() -> dict[str, str]:
    """Walk the fixtures directory and return ``{stem: path}`` for
    every committed JSON fixture. Adding a new endpoint = drop the
    fixture in the directory; the test picks it up automatically."""
    return {
        p.stem: _path_from_filename(p.stem)
        for p in sorted(_FIXTURES.glob("*.json"))
    }


ENDPOINTS = _discover_endpoints()

# Endpoints whose responses ARE validated against the schema, but
# whose schema is intentionally a free-form ``additionalProperties:
# true`` blob (so strict-mode would always flag them). Each entry
# should carry a justification — if the shape can be enumerated, do
# so in openapi.yaml instead of adding here.
FREEFORM_ENDPOINTS: set[str] = {
    # /api/disk's `disk` map is keyed by deployment-specific labels
    # (config / media / torrents / usenet — depends on what's mounted)
    # and its `guardrails` block is a free-form policy bag. Strict-
    # mode would be impossible without enumerating every possible
    # mount label across compose + k8s + bare-metal deploys.
    "/api/disk",
}

# NO TOLERATIONS. The previous revision had a STRICT_KEY_TOLERATIONS
# map that exempted live-emitted keys not declared in the spec —
# rejected on review with "we cant have exceptions for these. its
# broken." If a handler emits a field, the spec MUST declare it.
# Period. If the field is internal-only, remove it from the handler.


def _read_fixture(name: str) -> object:
    path = _FIXTURES / f"{name}.json"
    if not path.is_file():
        raise FileNotFoundError(
            f"missing fixture: {path}. Capture it via the workflow in "
            "the module docstring."
        )
    return json.loads(path.read_text(encoding="utf-8"))


class ApiResponseContractTest(unittest.TestCase):
    """One sub-test per endpoint. Failures are noisy on purpose —
    every error string starts with the JSON pointer to the field so
    the operator knows exactly where to look."""

    def test_every_fixture_is_registered(self) -> None:
        """Catches the case where someone drops a JSON file into
        ``fixtures/api_responses/`` without wiring it into
        ``ENDPOINTS``. Forces the registration so the fixture
        actually gets validated."""
        on_disk = {
            p.stem for p in _FIXTURES.glob("*.json")
        }
        registered = set(ENDPOINTS.keys())
        unregistered = sorted(on_disk - registered)
        self.assertEqual(
            unregistered, [],
            f"fixtures present but not in ENDPOINTS: {unregistered}",
        )

    def test_every_endpoint_has_a_fixture(self) -> None:
        """Inverse — make sure each ENDPOINTS entry has a real
        captured fixture on disk. Without this, a stale entry
        would silently pass with no validation."""
        missing = [
            name for name in ENDPOINTS
            if not (_FIXTURES / f"{name}.json").is_file()
        ]
        self.assertEqual(
            missing, [],
            f"ENDPOINTS entries with no fixture file: {missing}",
        )

    def test_every_documented_endpoint_has_a_fixture(self) -> None:
        """Coverage ratchet: any documented GET endpoint that has a
        200 application/json schema and no path template MUST have
        a captured fixture. Without it, the contract test pool
        silently shrinks and drift creeps in for un-covered paths.

        Bug class C — the SPA's Routing tab broke because
        /api/routing-probe had no captured fixture; the test pool
        therefore never invoked ``validate_response`` against the
        live shape, and the spa<->handler mismatch
        ({routing,services} vs {rows:[…]}) went unflagged. This
        test is the gate that keeps that bug class from recurring.

        Skipped categories (intentional, won't fail this test):
          * Path-templated endpoints (/api/users/{id}) — need
            hand-picked representative parameters.
          * ``x-status: planned`` endpoints — documented ahead of
            implementation.
          * Endpoints whose 200 schema is non-JSON (file downloads,
            openapi.yaml itself, the redoc HTML, etc.).
          * POST-only paths — covered by request-body contract
            tests, not response fixtures. (The symmetric
            ``test_no_orphaned_fixtures`` ensures we don't carry a
            POST-fixture mapped to a path the spec dropped.)

        How to fix
        ----------
        Capture the missing fixture (see CAPTURE.md in fixtures dir)
        and commit it. Re-running this test should now pass.
        Alternatively, if the endpoint is truly internal, mark its
        operation with ``x-status: planned`` in openapi.yaml until
        the implementation lands."""
        spec = yaml.safe_load(_SPEC_PATH.read_text(encoding="utf-8"))
        # Reverse-map: which spec path does each fixture cover?
        covered_paths: set[str] = set(ENDPOINTS.values())

        missing: list[str] = []
        for path, ops in (spec.get("paths") or {}).items():
            if "{" in path:
                continue
            if not isinstance(ops, dict):
                continue
            get_op = ops.get("get")
            if not isinstance(get_op, dict):
                continue
            if str(get_op.get("x-status") or "").lower() == "planned":
                continue
            r200 = (get_op.get("responses") or {}).get("200")
            if r200 is None:
                r200 = (get_op.get("responses") or {}).get(200)
            if not isinstance(r200, dict):
                continue
            content = r200.get("content") or {}
            media = content.get("application/json")
            if not isinstance(media, dict):
                continue
            schema = media.get("schema")
            if not isinstance(schema, dict):
                continue
            if path in covered_paths:
                continue
            missing.append(path)

        if missing:
            self.fail(
                "Documented GET endpoints with no fixture under "
                "tests/fixtures/api_responses/. Capture each one (see "
                "CAPTURE.md) or mark the spec op `x-status: planned`. "
                "Missing:\n  " + "\n  ".join(sorted(missing))
            )

    def test_documented_endpoint_coverage_floor(self) -> None:
        """Floor ratchet: this is the *count* of GET endpoints with a
        200 application/json schema that have a captured fixture. The
        number can only go UP. New endpoints land with a fixture; the
        existing-endpoint cohort never shrinks (which would mean a
        spec entry, fixture, or both got silently dropped).

        Why this is separate from the missing-fixture check above:
        ``test_every_documented_endpoint_has_a_fixture`` triggers when
        a path is added to the spec without a fixture. This test
        triggers when somebody *removes* a covered path from the spec
        without removing the fixture (making it orphan, see below) AND
        without lowering the floor — which would fail loudly here. It
        catches the "delete an endpoint and its tests in the same PR"
        anti-pattern that the orphan check would let through if the
        deleter is thorough."""
        spec = yaml.safe_load(_SPEC_PATH.read_text(encoding="utf-8"))
        covered_paths: set[str] = set(ENDPOINTS.values())
        coverable = 0
        for path, ops in (spec.get("paths") or {}).items():
            if "{" in path:
                continue
            if not isinstance(ops, dict):
                continue
            get_op = ops.get("get")
            if not isinstance(get_op, dict):
                continue
            if str(get_op.get("x-status") or "").lower() == "planned":
                continue
            r200 = (get_op.get("responses") or {}).get("200")
            if r200 is None:
                r200 = (get_op.get("responses") or {}).get(200)
            if not isinstance(r200, dict):
                continue
            content = r200.get("content") or {}
            media = content.get("application/json")
            if not isinstance(media, dict):
                continue
            if not isinstance(media.get("schema"), dict):
                continue
            if path in covered_paths:
                coverable += 1
        # Floor — only goes up. Bump after capturing new fixtures
        # for newly-added GET endpoints. Leave a small slack so
        # parallel agents adding endpoints don't have to bump in
        # lock-step; the missing-fixture check above is the strict
        # gate, this is the no-shrink complement.
        floor = 60
        self.assertGreaterEqual(
            coverable, floor,
            f"Documented GET fixture coverage shrank from floor "
            f"{floor} to {coverable}. Either restore the dropped "
            f"fixture(s) or, if the endpoint was intentionally retired, "
            f"lower the floor in this test.",
        )

    def test_no_orphaned_fixtures(self) -> None:
        """Symmetric coverage: every fixture file must map to a
        path that exists in openapi.yaml. A stale fixture for a
        retired endpoint silently keeps passing schema validation
        because the validator picks up *no* schema for an unknown
        path, so an orphaned fixture is dead weight that hides
        when the spec drops a path the SPA still depends on.

        How to fix
        ----------
        Either restore the deleted spec entry, or remove the
        orphaned fixture file."""
        spec = yaml.safe_load(_SPEC_PATH.read_text(encoding="utf-8"))
        spec_paths = set((spec.get("paths") or {}).keys())
        orphans: list[str] = []
        for stem, path in sorted(ENDPOINTS.items()):
            if path not in spec_paths:
                orphans.append(f"{stem}.json -> {path} (not in openapi.yaml)")
        self.assertEqual(
            orphans, [],
            "Fixtures map to paths not declared in openapi.yaml:\n  "
            + "\n  ".join(orphans),
        )

    def test_response_validates_against_schema(self) -> None:
        """The headline assertion: every captured response satisfies
        its OpenAPI schema. Type drift, missing required fields, and
        ``$ref``-resolution failures all trip this."""
        failures: list[str] = []
        for name, path in sorted(ENDPOINTS.items()):
            body = _read_fixture(name)
            errors = validate_response(path, body)
            if errors:
                failures.append(
                    f"\n  {path} ({name}.json):\n    "
                    + "\n    ".join(errors)
                )
        if failures:
            self.fail(
                "Live response fixtures don't match openapi.yaml schema:"
                + "".join(failures)
            )

    def test_no_undeclared_top_level_keys(self) -> None:
        """Strict mode: every top-level key the handler emits must be
        declared in the schema's ``properties``. Catches the bug class
        where a new field ships in the handler but not the spec, the
        UI then guesses wrong about what to render. ``FREEFORM_ENDPOINTS``
        exempts intentional ``additionalProperties: true`` bags only —
        otherwise NO exceptions: if the handler emits it, the spec
        documents it."""
        failures: list[str] = []
        for name, path in sorted(ENDPOINTS.items()):
            if path in FREEFORM_ENDPOINTS:
                continue
            body = _read_fixture(name)
            errors = validate_response_strict(path, body)
            extras = [e for e in errors if "undeclared top-level keys" in e]
            if extras:
                failures.append(
                    f"\n  {path} ({name}.json):\n    " + "\n    ".join(extras)
                )
        if failures:
            self.fail(
                "Endpoints emit fields not declared in openapi.yaml:"
                + "".join(failures)
            )


if __name__ == "__main__":
    unittest.main()
