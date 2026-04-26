"""Ratchet: GET handlers must not echo raw provider API keys.

The threat model: a read-scope bearer token should not be enough to
exfiltrate every provider's API key. The v1.0.x regression that
motivated this test was ``GET /api/keys`` returning the full
``discover_api_keys()`` dict to any authenticated caller — a single
compromised read token == full stack compromise.

This test enforces, by AST analysis, that:

1. No GET handler in ``src/media_stack/api/handlers_get.py`` passes
   the raw output of ``discover_api_keys`` (or similar key-sourcing
   helpers) directly into ``_json_response`` / ``_raw_response``.
2. Every response that includes API-key inventory MUST flow through
   ``core.auth.secret_redaction.redact_api_key_map`` (or equivalent
   redaction) on the same call path.

The check is conservative: a handler that reads keys AND emits a
response AND does not import/reference a redaction helper is
flagged. An explicit ALLOWED list (empty by design) captures
exceptions.

A ratchet rather than a forever-static count because some handlers
may legitimately need to touch key discovery for metadata (e.g.
``/api/healthz``). When they do, they show up in the allowlist with
a reason.
"""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HANDLERS_GET = ROOT / "src" / "media_stack" / "api" / "handlers_get.py"

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
    """Return every method named ``_handle_*`` in the module."""
    out: list[ast.FunctionDef] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name.startswith("_handle_"):
            out.append(node)
    return out


class NoSecretInApiResponsesRatchet(unittest.TestCase):

    def test_every_handler_that_reads_keys_redacts_before_response(self) -> None:
        self.assertTrue(
            HANDLERS_GET.is_file(),
            f"handlers_get.py not found: {HANDLERS_GET}",
        )
        tree = ast.parse(
            HANDLERS_GET.read_text(encoding="utf-8"), str(HANDLERS_GET),
        )
        violations: list[str] = []
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
                f"{handler.name}:{handler.lineno} reads a key source "
                f"({sorted(_KEY_SOURCES)}) and emits a response "
                f"({sorted(_RESPONSE_EMITTERS)}) without going through "
                f"a redaction helper ({sorted(_REDACTION_HELPERS)})",
            )
        self.assertFalse(
            violations,
            "Handlers that leak raw API keys to response bodies:\n  - "
            + "\n  - ".join(violations),
        )

    def test_allowlist_only_names_existing_handlers(self) -> None:
        """Prevent stale allowlist entries after a rename."""
        if not _ALLOWED_RAW_KEY_HANDLERS:
            return  # nothing to validate
        tree = ast.parse(HANDLERS_GET.read_text(encoding="utf-8"))
        names = {h.name for h in _method_handlers(tree)}
        for allowed in _ALLOWED_RAW_KEY_HANDLERS:
            self.assertIn(
                allowed, names,
                f"allowlist names unknown handler: {allowed}",
            )

    def test_handlers_get_imports_redaction_module(self) -> None:
        # Defensive: if the redaction module is never imported, the
        # main test might false-pass because no handler sees a
        # redaction-helper name. Assert the import exists somewhere.
        src = HANDLERS_GET.read_text(encoding="utf-8")
        self.assertIn(
            "secret_redaction", src,
            "handlers_get.py must import from secret_redaction so the "
            "ratchet can verify redaction happens before response",
        )


if __name__ == "__main__":
    unittest.main()
