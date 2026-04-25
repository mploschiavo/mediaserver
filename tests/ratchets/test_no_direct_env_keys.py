"""Ratchet #10 (static analysis half) — no direct env reads of
service API keys outside the canonical resolver.

The bug class we're guarding against: a future contributor adds
``key = os.environ.get("SONARR_API_KEY", "")`` somewhere in the
codebase, the K8s Secret is empty on first boot, the new caller
silently sends a blank credential, and the dashboard tile counts
quietly degrade to zero.

Single chokepoint: ``runtime_keys.read_service_api_key`` is the
only legitimate caller. Test files are allowed to mock
``os.environ`` directly. ``STACK_ADMIN_*`` env reads are admin
credentials, not service API keys, and are out of scope.

Allow-list discipline: the ``_KNOWN_OFFENDERS`` set captures the
existing direct reads in the codebase as of the ratchet's
introduction. New offenders fail the test; cleaning up an
existing offender means deleting it from the allow-list. The
allow-list is intentionally a hard list, not a glob — moving
the call to a different file requires updating this test, which
forces the migration to be visible in code review.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src"


# Matches direct service-API-key reads:
#
#     os.environ.get("FOO_API_KEY"...)
#     os.environ["FOO_API_KEY"]
#
# We deliberately exclude:
#   - ``TELEMETRY_API_KEY`` (controller telemetry, not a service key)
#   - ``PRECONFIGURE_API_KEYS`` (a boolean control flag)
#   - ``STACK_ADMIN_*`` (admin credentials, different lifecycle)
#   - ``JELLYFIN_API_KEY_APP_NAME`` (a configuration, not the key)
_PATTERN = re.compile(
    r'os\.environ'
    r'(?:\.get\(\s*["\']([A-Z][A-Z0-9_]*_API_KEY)["\']'
    r'|\[\s*["\']([A-Z][A-Z0-9_]*_API_KEY)["\']\s*\])'
)


# Names that match the regex but are not service API keys —
# these are stripped before deciding whether a hit counts.
_NON_SERVICE_KEY_NAMES = {
    "TELEMETRY_API_KEY",
    "JELLYFIN_API_KEY_APP_NAME",  # not actually matched, but kept for clarity
}


# (relative path, env-var name) pairs that already exist and are
# tolerated until they're migrated to runtime_keys. Anything new
# OR anything whose path drifts will fail the test.
_KNOWN_OFFENDERS: set[tuple[str, str]] = {
    # Jellyfin admin/reconcile paths — pre-runtime_keys; targeted
    # for migration in a follow-up PR. Not in scope for this
    # ratchet because they're owned by adjacent agents.
    ("media_stack/services/apps/jellyfin/admin_ops.py",
     "JELLYFIN_API_KEY"),
    ("media_stack/services/apps/jellyfin/cli/"
     "reconcile_jellyfin_home_rails_main.py",
     "JELLYFIN_API_KEY"),
}


# Files that are *allowed* to read API keys directly because
# they implement the helper or its tests.
_ALLOWED_PATHS: set[str] = {
    "media_stack/api/services/runtime_keys.py",
}


def _iter_python_files() -> list[Path]:
    return sorted(p for p in _SRC_ROOT.rglob("*.py")
                  if "__pycache__" not in p.parts)


def _scan(path: Path) -> list[str]:
    """Return the list of disallowed env-var names referenced
    directly in ``path``."""
    rel = path.relative_to(_SRC_ROOT).as_posix()
    if rel in _ALLOWED_PATHS:
        return []
    text = path.read_text(encoding="utf-8", errors="replace")
    hits: list[str] = []
    for m in _PATTERN.finditer(text):
        name = m.group(1) or m.group(2) or ""
        if not name or name in _NON_SERVICE_KEY_NAMES:
            continue
        hits.append(name)
    return hits


class NoDirectEnvKeysRatchet(unittest.TestCase):

    def test_only_runtime_keys_reads_api_key_envs(self) -> None:
        offenders: dict[str, list[str]] = {}
        for path in _iter_python_files():
            hits = _scan(path)
            if not hits:
                continue
            rel = path.relative_to(_SRC_ROOT).as_posix()
            for name in hits:
                if (rel, name) in _KNOWN_OFFENDERS:
                    continue
                offenders.setdefault(rel, []).append(name)

        if offenders:
            lines = [
                "Direct os.environ reads of service API keys are "
                "banned outside runtime_keys.read_service_api_key.",
                "Found new offender(s):",
            ]
            for path, names in sorted(offenders.items()):
                lines.append(f"  - {path}: {', '.join(sorted(set(names)))}")
            lines.append(
                "Either route the read through "
                "media_stack.api.services.runtime_keys."
                "read_service_api_key, or — if you're cleaning up "
                "a known offender — delete the matching tuple from "
                "_KNOWN_OFFENDERS in this test file."
            )
            self.fail("\n".join(lines))

    def test_known_offenders_still_match_reality(self) -> None:
        """Catch the lazy "I migrated the call but forgot to
        update the allow-list" case. If a path in
        ``_KNOWN_OFFENDERS`` no longer contains the matching
        env-var name, the entry must be removed.
        """
        stale: list[tuple[str, str]] = []
        for rel, name in _KNOWN_OFFENDERS:
            target = _SRC_ROOT / rel
            if not target.is_file():
                stale.append((rel, name))
                continue
            text = target.read_text(encoding="utf-8", errors="replace")
            if name not in text:
                stale.append((rel, name))
        if stale:
            self.fail(
                "_KNOWN_OFFENDERS contains entries that no longer "
                "match reality — delete them:\n  "
                + "\n  ".join(f"{r}: {n}" for r, n in stale)
            )


if __name__ == "__main__":
    unittest.main()
