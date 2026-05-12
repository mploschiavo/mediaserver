"""Ratchet: refuse to commit known secret patterns into the repo.

Motivated by the 2026-05-12 incident: a real ``/api/backup`` blob
was committed as ``tests/fixtures/api_responses/backup.json`` with
live Google OAuth credentials, Authelia storage encryption key,
Bazarr ``flask_secret_key`` / Plex ``encryption_key``, and three
*arr API keys. All 8 values had to be purged via
``git filter-repo`` before going public.

This ratchet scans every tracked text file (excluding the
ratchet's own pattern list + a documented allowlist for the
intentionally-bogus placeholder strings in docs / openapi
examples) for known secret prefixes:

* Google OAuth client_secret  ``GOCSPX-…`` (≥ 28 chars)
* Google API key              ``AIza…`` (≥ 35 base64-ish chars)
* GitHub PAT                  ``ghp_…`` / ``github_pat_…``
* OpenAI / Anthropic-shaped   ``sk-…`` (≥ 40 chars)
* AWS access key id           ``AKIA…`` (16 chars)
* Slack bot token             ``xoxb-…``
* GCP OAuth refresh token     ``ya29.…``
* PEM block                   ``-----BEGIN .* PRIVATE KEY-----`` (with body content beyond markers)

Pattern-only (no entropy check) on purpose — entropy heuristics
over the whole tree produce hundreds of false positives from
hash-shaped test fixtures, contract IDs, and image digests. The
prefix patterns above are extremely specific and effectively
zero-false-positive in this codebase.

Pre-commit / push protection is a complementary layer (operator
side via ``gitleaks`` or GitHub's built-in secret scanning); this
ratchet runs in CI as a tripwire when those layers fail or are
bypassed.
"""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


_PATTERNS = {
    "google_oauth_client_secret": re.compile(r"GOCSPX-[A-Za-z0-9_-]{20,}"),
    "google_api_key": re.compile(r"AIza[0-9A-Za-z_-]{35}"),
    "github_pat_classic": re.compile(r"ghp_[0-9A-Za-z]{36}"),
    "github_pat_fine_grained": re.compile(r"github_pat_[0-9A-Za-z_]{82}"),
    "openai_key": re.compile(r"sk-[a-zA-Z0-9]{40,}"),
    "aws_access_key_id": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "slack_bot_token": re.compile(r"xoxb-[0-9]{10,}-[0-9]{10,}-[a-zA-Z0-9]{20,}"),
    "gcp_refresh_token": re.compile(r"ya29\.[0-9A-Za-z_-]{40,}"),
    "pem_private_key_block": re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\r\n]+[A-Za-z0-9+/=\r\n]{40,}-----END",
    ),
}


# Paths excluded from the scan. Each entry MUST be justified
# inline — adding a path here is the only sanctioned way to
# bypass the ratchet, and the justification documents the
# threat model the entry is exempt from.
_PATH_ALLOWLIST = {
    # Self — this file's pattern table contains the regex
    # literals it's looking for.
    "tests/unit/ratchets/test_no_committed_secrets_ratchet.py",
    # OpenAPI spec uses placeholder client_id / client_secret
    # values to document the request shape; those placeholders
    # are masked (``***``) or shaped like ``1234567890.apps.
    # googleusercontent.com``.
    "contracts/api/openapi.yaml",
    # CHANGELOG entries reference rotated-and-revoked credential
    # SHAs as part of the 2026-05-12 incident write-up. The
    # values are dead.
    "CHANGELOG.md",
    # TLS install dialog test uses a documented-placeholder PEM
    # block (53-char body ending in ``xxxxxx``) — real ECDSA
    # private keys are 220+ chars without repeating-char runs.
    # The test asserts the UI's file-upload widget recognises
    # the ``BEGIN PRIVATE KEY`` marker; the body content is
    # deliberately invalid.
    "ui/src/features/routing-admin/TlsInstallDialog.test.tsx",
}


def _git_tracked_files() -> list[Path]:
    """Return every git-tracked path. Faster + more accurate than
    walking the filesystem because it inherits .gitignore."""
    result = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files"],
        capture_output=True, text=True, check=True,
    )
    return [
        ROOT / line for line in result.stdout.splitlines() if line
    ]


def _is_text_file(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        with path.open("rb") as f:
            head = f.read(8192)
    except OSError:
        return False
    if b"\x00" in head:
        return False
    return True


class NoCommittedSecretsRatchet(unittest.TestCase):
    """Refuse to commit known-shaped secrets into the repo."""

    def test_no_known_secret_patterns_in_tracked_files(self) -> None:
        hits: list[str] = []
        for path in _git_tracked_files():
            rel = path.relative_to(ROOT).as_posix()
            if rel in _PATH_ALLOWLIST:
                continue
            if not _is_text_file(path):
                continue
            try:
                content = path.read_text(
                    encoding="utf-8", errors="replace",
                )
            except OSError:
                continue
            for name, regex in _PATTERNS.items():
                m = regex.search(content)
                if m is None:
                    continue
                hits.append(
                    f"  {rel}: matches {name} → "
                    f"{m.group(0)[:24]}... (length {len(m.group(0))})",
                )
        self.assertEqual(
            hits, [],
            "Tracked files contain known secret patterns. "
            "Rotate the credential at its source, redact the file, "
            "and run ``git filter-repo --replace-text`` if the "
            "value has already been committed.\n\nFindings:\n"
            + "\n".join(hits),
        )


class NoSecretsInApiResponseFixtures(unittest.TestCase):
    """Test fixtures under ``tests/fixtures/api_responses/`` must
    use placeholder values for sensitive-shaped fields.

    Distinct from the broader secret-pattern ratchet above: the
    fixture-specific check catches the 2026-05-12 failure shape
    where someone captured a real ``/api/backup`` JSON blob and
    committed it as a fixture. Even if the secret values aren't
    in the known-prefix set above, they shouldn't be hash-shaped
    32+-char strings on a ``flask_secret_key`` /
    ``encryption_key`` / ``apikey`` / ``client_secret`` key.
    """

    _SUSPECT_KEYS = (
        "flask_secret_key",
        "encryption_key",
        "client_secret",
        "private_key",
        "secret_key",
        "session_key",
    )

    _SUSPECT_VALUE = re.compile(r"^[A-Za-z0-9_-]{16,}$")

    def test_api_response_fixtures_use_placeholder_values(self) -> None:
        import json

        fixtures_dir = ROOT / "tests" / "fixtures" / "api_responses"
        if not fixtures_dir.is_dir():
            self.skipTest("api_responses fixtures dir missing")
        hits: list[str] = []
        for fixture in fixtures_dir.glob("*.json"):
            try:
                data = json.loads(fixture.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            self._walk(data, fixture.relative_to(ROOT).as_posix(), hits)
        self.assertEqual(
            hits, [],
            "API-response fixtures contain real-looking secret values. "
            "Replace with placeholder strings like 'REDACTED-…' or "
            "'<placeholder>' so the fixture stays committable.\n\n"
            "Findings:\n" + "\n".join(hits),
        )

    def _walk(self, node: object, where: str, hits: list[str]) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                key_lower = str(k).lower()
                if (
                    any(s in key_lower for s in self._SUSPECT_KEYS)
                    and isinstance(v, str)
                    and self._SUSPECT_VALUE.match(v)
                    and not v.upper().startswith("REDACTED")
                    and not v.startswith("placeholder")
                    and not v.startswith("PLACEHOLDER")
                    and not v.startswith("<")
                ):
                    hits.append(
                        f"  {where}: ``{k}`` = "
                        f"{v[:12]}... (length {len(v)}) — looks like a real secret",
                    )
                self._walk(v, where, hits)
        elif isinstance(node, list):
            for item in node:
                self._walk(item, where, hits)


if __name__ == "__main__":
    unittest.main()
