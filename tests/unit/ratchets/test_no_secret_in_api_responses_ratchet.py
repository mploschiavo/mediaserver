"""Ratchet: GET handlers must not echo raw provider API keys.

The threat model: a read-scope bearer token should not be enough to
exfiltrate every provider's API key. The v1.0.x regression that
motivated this test was ``GET /api/keys`` returning the full
``discover_api_keys()`` dict to any authenticated caller — a single
compromised read token == full stack compromise.

ADR-0007 Phase E retired ``handlers_get.py``; the GET handlers now
live across the ``api/routes/*.py`` modules. This ratchet now scans
every method named ``handle_*`` / ``_handle_*`` inside those
modules.

This test enforces, by AST analysis, that:

1. No GET handler passes the raw output of ``discover_api_keys`` (or
   similar key-sourcing helpers) directly into ``_json_response`` /
   ``_raw_response``.
2. Every response that includes API-key inventory MUST flow through
   ``core.auth.secret_redaction.redact_api_key_map`` (or equivalent
   redaction) on the same call path.

The check is conservative: a handler that reads keys AND emits a
response AND does not import/reference a redaction helper is
flagged. An explicit ALLOWED list (empty by design) captures
exceptions.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ROUTES_DIR = ROOT / "src" / "media_stack" / "api" / "routes"
SERVICES_DIR = ROOT / "src" / "media_stack" / "api" / "services"

# Handler names that are INTENTIONALLY allowed to reference raw key
# material (each entry documents why). Empty today — every GET that
# emits key inventory goes through ``redact_api_key_map``.
_ALLOWED_RAW_KEY_HANDLERS: frozenset[str] = frozenset()

# Function names that hand back raw key material. If a handler calls
# one of these AND reaches a response emitter without first passing
# through a redaction helper, it's flagged.
_KEY_SOURCES: frozenset[str] = frozenset({
    "discover_api_keys",
})

# Redaction helpers the handler may use. Any of these in the same
# function body satisfies the ratchet.
_REDACTION_HELPERS: frozenset[str] = frozenset({
    "redact_api_key_map",
    "redact_if_secret_key",
    "fingerprint",
})

# Response emitters — reaching one of these means we're about to
# hand data to the client.
_RESPONSE_EMITTERS: frozenset[str] = frozenset({
    "_json_response",
    "_html_response",
    "_raw_response",
})


def _calls_any(node: ast.AST, names: frozenset[str]) -> bool:
    """True if any ``Call`` in ``node`` names a function in ``names``.

    Matches both ``func()`` (Name) and ``obj.func()`` (Attribute) —
    covers ``discover_api_keys()`` and ``health_svc.discover_api_keys()``.
    """
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        if isinstance(func, ast.Name) and func.id in names:
            return True
        if isinstance(func, ast.Attribute) and func.attr in names:
            return True
    return False


def _method_handlers(tree: ast.AST) -> list[ast.FunctionDef]:
    """Return every method whose name is ``_handle_*`` or ``handle_*``.

    The legacy ``handlers_get.py`` used ``_handle_*`` private methods.
    The post-Phase-E route modules use public ``handle_*`` methods
    (Router auto-discovery picks up the ``@get(...)``-decorated
    methods regardless of leading underscore). Both are accepted.
    """
    out: list[ast.FunctionDef] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name.startswith("_handle_") or node.name.startswith("handle_"):
            out.append(node)
    return out


def _iter_scan_files() -> list[Path]:
    """All route + service modules whose handlers might hand back
    key material. Tests + adapter modules are out of scope (they
    don't emit user-facing responses)."""
    out: list[Path] = []
    for d in (ROUTES_DIR, SERVICES_DIR):
        if not d.is_dir():
            continue
        for p in d.rglob("*.py"):
            if p.name.startswith("test_"):
                continue
            out.append(p)
    return out


class NoSecretInApiResponsesRatchet(unittest.TestCase):

    def test_every_handler_that_reads_keys_redacts_before_response(self) -> None:
        violations: list[str] = []
        scanned = 0
        for source_file in _iter_scan_files():
            try:
                tree = ast.parse(
                    source_file.read_text(encoding="utf-8"), str(source_file),
                )
            except (OSError, SyntaxError, UnicodeDecodeError):
                continue
            scanned += 1
            for handler in _method_handlers(tree):
                if handler.name in _ALLOWED_RAW_KEY_HANDLERS:
                    continue
                if not _calls_any(handler, _KEY_SOURCES):
                    continue
                if not _calls_any(handler, _RESPONSE_EMITTERS):
                    # Reads keys but doesn't emit a response (e.g.
                    # a helper); fine.
                    continue
                if _calls_any(handler, _REDACTION_HELPERS):
                    continue
                violations.append(
                    f"{source_file.name}::{handler.name}:{handler.lineno} "
                    f"reads a key source ({sorted(_KEY_SOURCES)}) and "
                    f"emits a response ({sorted(_RESPONSE_EMITTERS)}) "
                    f"without going through a redaction helper "
                    f"({sorted(_REDACTION_HELPERS)})",
                )
        self.assertGreater(
            scanned, 0,
            "ratchet scanned no route/service modules — directories "
            f"missing? routes={ROUTES_DIR}, services={SERVICES_DIR}",
        )
        self.assertFalse(
            violations,
            "Handlers that leak raw API keys to response bodies:\n  - "
            + "\n  - ".join(violations),
        )

    def test_handlers_get_imports_redaction_module(self) -> None:
        # Defensive: if the redaction module is never imported, the
        # main test might false-pass because no handler sees a
        # redaction-helper name. Assert the import exists somewhere
        # across the GET-domain code base. ADR-0007 Phase E split
        # the legacy single-file import; now any of the route modules
        # touching keys must pull in secret_redaction.
        seen = False
        for source_file in _iter_scan_files():
            try:
                src = source_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if "secret_redaction" in src:
                seen = True
                break
        self.assertTrue(
            seen,
            "No route/service module imports from secret_redaction. "
            "If GET handlers genuinely no longer touch key material, "
            "delete this defensive check.",
        )


if __name__ == "__main__":
    unittest.main()
