"""Ratchet: no source file in ``src/media_stack/`` may build a URL
that carries an ``api_key`` / ``apikey`` / ``api-key`` query
parameter.

Why a ratchet
-------------
Placing a credential in the query string is NOT equivalent to placing
it in a header:

- Access logs capture the full request line, including the query.
  Every proxy between the controller and the upstream sees the key in
  cleartext for the lifetime of its log retention.
- Browser history, DOM ``Referer``, and URL-autofill caches ingest
  query strings but not headers. A key in ``?api_key=`` can escape
  to the user's desktop via innocuous client-side behaviour.
- k8s access logs (nginx + Envoy + the app's own logs) pile on
  three separate copies.

Jellyfin 10.11 accepts ``X-Emby-Token: <api_key>`` as a drop-in
replacement for ``?api_key=...`` (verified against the Jellyfin
10.11 docs). The Servarr stack reads ``X-Api-Key`` natively. Every
call site can be migrated.

Allowlist policy
----------------
Known violators that aren't yet migrated are listed in
``_ALLOWED_VIOLATIONS`` with a short reason. The ratchet enforces
that the set does NOT grow over time — any NEW occurrence trips CI.
A migration PR removes entries from the allowlist as it lands.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src" / "media_stack"

# case-insensitive: ``api_key`` | ``apikey`` | ``api-key``
_URL_QUERY_KEY_RE = re.compile(r"[?&]api[_-]?key=", re.IGNORECASE)

# Files that reference the pattern for REDACTION purposes — not for
# sending the key in the URL. Scanning them would create false-
# positives, so they're excluded by basename.
_SCANNER_OR_REDACTOR_FILES: frozenset[str] = frozenset({
    # Pattern is used to REDACT api_key from logged URLs.
    "secret_redaction.py",
    # Structural exception scrubber — references the query-string
    # shape to strip it, not to send it.
    "secret_scrub.py",
    # This ratchet itself is one long regex comment.
    "test_api_key_not_in_url_query_ratchet.py",
})


_ALLOWED_VIOLATIONS: frozenset[str] = frozenset({
    # Format: "<relative_path>:<line>".
    # Each entry is a known-but-not-yet-migrated call site. The
    # number is the CURRENT line number in the source file; any
    # drift (refactor, rename, format) fails the allowlist
    # consistency check and forces the author to refresh the
    # entry or remove it.
    #
    # Paths reflect ADR-0002 Phase 16 layout: callers in
    # ``services/apps/*/cli/`` moved to ``infrastructure/<svc>/`` /
    # ``application/<svc>/``; ``core/platforms/compose/services/``
    # moved to ``adapters/compose/services/``.
    #
    # ---- Jellyfin callers (use X-Emby-Token in follow-up) ----
    "src/media_stack/application/jellyfin/plugin_activation_service.py:54",
    "src/media_stack/infrastructure/jellyfin/controller_api_key_service.py:64",
    "src/media_stack/infrastructure/jellyfin/controller_api_key_service.py:74",
    "src/media_stack/api/services/admin.py:140",
    "src/media_stack/api/services/content.py:106",
    "src/media_stack/api/services/content.py:114",
    "src/media_stack/api/services/content_download_settings_mixin.py:65",
    "src/media_stack/api/services/content_download_settings_mixin.py:157",
    # ---- Servarr callers (use X-Api-Key in follow-up) ----
    "src/media_stack/adapters/compose/services/edge_http_smoke.py:389",
    # ---- SABnzbd (accepts ?apikey= natively; header variant not
    #      supported by the SAB API, legitimate outlier) ----
    "src/media_stack/api/services/content.py:778",
    # ---- Redactor function docstring — the pattern is cited here
    #      to describe what's being stripped, not a call site. ----
    "src/media_stack/services/apps/core/job_adapters.py:161",
})


def _iter_source_files():
    for path in SRC.rglob("*.py"):
        if path.name in _SCANNER_OR_REDACTOR_FILES:
            continue
        yield path


def _find_violations(path: Path) -> list[int]:
    """Return a list of 1-indexed line numbers matching the query
    pattern in ``path``. Skips lines that are pure comments (the
    redaction helpers live in comments documenting the shape)."""
    out: list[int] = []
    for lineno, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1,
    ):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        if _URL_QUERY_KEY_RE.search(line):
            out.append(lineno)
    return out


class ApiKeyNotInUrlQueryRatchet(unittest.TestCase):

    def test_no_new_violations(self) -> None:
        actual_keys: set[str] = set()
        for path in _iter_source_files():
            rel = str(path.relative_to(ROOT))
            for lineno in _find_violations(path):
                actual_keys.add(f"{rel}:{lineno}")
        unexpected = actual_keys - _ALLOWED_VIOLATIONS
        self.assertFalse(
            unexpected,
            "New ``api_key=`` URL query parameter detected. Move the "
            "credential to a header (X-Emby-Token for Jellyfin, "
            "X-Api-Key for Servarr). New sites:\n  - "
            + "\n  - ".join(sorted(unexpected)),
        )

    def test_allowlist_does_not_grow_silently(self) -> None:
        """If an allowlisted line moved (refactor / format) the
        entry is stale. Force the author to refresh it."""
        actual_keys: set[str] = set()
        for path in _iter_source_files():
            rel = str(path.relative_to(ROOT))
            for lineno in _find_violations(path):
                actual_keys.add(f"{rel}:{lineno}")
        stale = _ALLOWED_VIOLATIONS - actual_keys
        self.assertFalse(
            stale,
            "Allowlist entries no longer match source — refresh or "
            "remove. Stale entries:\n  - "
            + "\n  - ".join(sorted(stale)),
        )


if __name__ == "__main__":
    unittest.main()
