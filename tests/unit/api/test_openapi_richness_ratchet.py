"""Ratchet: ``GET /api/openapi.json`` must return the rich
``contracts/api/openapi.yaml`` content, not a hardcoded stub.

Why this exists: the legacy ``_get_openapi_spec()`` in server.py was a
hardcoded list of ~50 endpoints with no operationIds, no examples, no
x-codeSamples. The api-docs page (``/app/media-stack-ui/api-docs/``)
points Stoplight Elements at ``/api/openapi.json``, so for the entire
post-Phase-4 era it loaded an EMPTY-looking spec — operators saw a
viewer with no operations even though the real spec has 200+ ops with
360+ examples and 20+ Postman-quality x-codeSamples blocks.

This ratchet enforces a floor on the operator-visible richness so a
future "let's revert to the stub" regression fails CI loudly.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

_SPEC_PATH = ROOT / "contracts" / "api" / "openapi.yaml"


def _load_spec() -> dict:
    return yaml.safe_load(_SPEC_PATH.read_text(encoding="utf-8")) or {}


class OpenApiRichnessFloor(unittest.TestCase):
    """Floors enforce that the spec doesn't shrink. Numbers can only
    go UP (or be deliberately bumped down with reason)."""

    def test_operations_count_floor(self) -> None:
        spec = _load_spec()
        op_ids = [
            op.get("operationId")
            for path_item in (spec.get("paths") or {}).values()
            for op in (path_item or {}).values()
            if isinstance(op, dict) and op.get("operationId")
        ]
        # Floor as of v1.0.234 — 200+ operations. Ratchet to 200 so
        # there's wiggle room; bump up over time as new endpoints land.
        self.assertGreaterEqual(
            len(op_ids), 200,
            f"openapi.yaml has only {len(op_ids)} operations with "
            f"operationIds; expected at least 200. The api-docs page "
            f"will look anaemic. Verify spec edits didn't lose ops.",
        )

    def test_examples_count_floor(self) -> None:
        # Count `example:` keys anywhere in the parsed spec — both
        # request-body examples and response examples land here.
        # Stoplight Elements renders these as the Postman-style "Try
        # it" panels; without them the api-docs page is just bare
        # schema definitions.
        text = _SPEC_PATH.read_text(encoding="utf-8")
        example_lines = sum(1 for L in text.splitlines() if L.lstrip().startswith("example:"))
        self.assertGreaterEqual(
            example_lines, 300,
            f"openapi.yaml has only {example_lines} example: keys; "
            f"expected at least 300. Operator UX on /api-docs degrades "
            f"sharply when examples vanish (no more 'click to see what "
            f"this returns').",
        )

    def test_code_samples_count_floor(self) -> None:
        spec = _load_spec()
        ops_with_samples = sum(
            1
            for path_item in (spec.get("paths") or {}).values()
            for op in (path_item or {}).values()
            if isinstance(op, dict) and op.get("x-codeSamples")
        )
        # x-codeSamples are the curl/Python/JS Postman-style blocks
        # that render as "Try in shell" / "Try in Python" tabs.
        self.assertGreaterEqual(
            ops_with_samples, 20,
            f"openapi.yaml has only {ops_with_samples} operations "
            f"with x-codeSamples; expected at least 20. The high-leverage "
            f"endpoints (rotate-keys, routing, deploy, snapshots, etc.) "
            f"need code samples for the api-docs page to be useful as a "
            f"copy-paste reference.",
        )


class OpenApiJsonHandlerReadsRealSpec(unittest.TestCase):
    """The class-of-bug this ratchet exists to catch: the JSON handler
    was a hardcoded stub for years even though the rich YAML lived
    next to it. A regression here means /api-docs goes empty again."""

    def test_handler_reads_yaml_path(self) -> None:
        src = (
            ROOT / "src" / "media_stack" / "api" / "handlers_get.py"
        ).read_text(encoding="utf-8")
        # Anchor on the dispatch elif, not the bare path string —
        # the path appears in the candidate-list comment too, which
        # would land us in the wrong block.
        anchor = 'elif path == "/api/openapi.json":'
        idx = src.find(anchor)
        self.assertNotEqual(
            idx, -1, "/api/openapi.json branch missing from handlers_get.py"
        )
        # Window of ~1000 chars after anchor — should reference
        # _OPENAPI_YAML and yaml.safe_load.
        block = src[idx:idx + 1000]
        self.assertIn(
            "_OPENAPI_YAML",
            block,
            "/api/openapi.json branch must read _OPENAPI_YAML (the "
            "parsed contracts/api/openapi.yaml). If you reverted to "
            "_get_openapi_spec() the api-docs page goes empty.",
        )
        self.assertIn(
            "safe_load",
            block,
            "/api/openapi.json branch must yaml.safe_load the YAML "
            "spec. The legacy stub at _get_openapi_spec() returned a "
            "hardcoded list and the api-docs page rendered empty.",
        )

    def test_legacy_stub_present_only_as_fallback(self) -> None:
        """The legacy `_get_openapi_spec()` is allowed to remain as a
        defensive fallback when YAML parsing fails — but the PRIMARY
        path must hit the real spec. Test asserts the primary call is
        through `safe_load(_OPENAPI_YAML)`, not directly through
        `handler._get_openapi_spec()` as the first action."""
        src = (
            ROOT / "src" / "media_stack" / "api" / "handlers_get.py"
        ).read_text(encoding="utf-8")
        # Find the JSON branch — assert safe_load appears BEFORE
        # _get_openapi_spec in the same block.
        # Anchor on the dispatch elif, not the bare path string —
        # the path appears in the candidate-list comment too, which
        # would land us in the wrong block.
        anchor = 'elif path == "/api/openapi.json":'
        idx = src.find(anchor)
        block = src[idx:idx + 1500]
        # Use call-syntax patterns so this regex doesn't match the
        # explanatory comment text earlier in the block. Match either
        # ``yaml.safe_load(_OPENAPI_YAML)`` or ``_yaml.safe_load(...)``
        # (the handler imports yaml as _yaml in the local scope).
        import re
        load_match = re.search(r"\b_?yaml\.safe_load\(_OPENAPI_YAML\)", block)
        load_pos = load_match.start() if load_match else -1
        stub_pos = block.find("handler._get_openapi_spec()")
        self.assertGreater(load_pos, 0, "safe_load missing")
        if stub_pos > 0:
            self.assertLess(
                load_pos, stub_pos,
                "The yaml.safe_load call must come BEFORE the "
                "_get_openapi_spec() fallback. If the stub runs "
                "first, the rich spec never gets served.",
            )


if __name__ == "__main__":
    unittest.main()
