"""Ratchet: every CLI ``*_main.py`` is registered in the index.

The repo has 20+ Python CLI commands under
``src/media_stack/cli/commands/`` (deploy_stack_main, build_*,
reset_admin, etc.). The point of the registry at
``.ratchets/cli-commands-registry.yaml`` is to make sure that
ANYONE adding a new command — human or AI agent — first reviews
the existing inventory and either re-uses an existing entry or
adds a deliberately-distinct purpose line.

Three assertions:

1. Every ``*_main.py`` file in ``src/media_stack/cli/commands/`` is
   listed in the registry.
2. Every registry entry corresponds to a file that exists (no
   stale entries left over after a delete / rename).
3. No two registry entries share the same purpose line — if you
   needed to add a duplicate, the existing command should have
   been extended instead.

Add a new command? Append it to the registry in the same format
and add the file. The ratchet's failure message tells you
exactly what's missing. Don't suppress the test.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
COMMANDS_DIR = REPO_ROOT / "src" / "media_stack" / "cli" / "commands"
REGISTRY_FILE = REPO_ROOT / ".ratchets" / "cli-commands-registry.yaml"


def _existing_main_files() -> set[str]:
    if not COMMANDS_DIR.is_dir():
        return set()
    return {
        p.name for p in COMMANDS_DIR.glob("*_main.py")
    }


def _registry() -> dict[str, str]:
    raw = yaml.safe_load(REGISTRY_FILE.read_text(encoding="utf-8"))
    return dict((raw or {}).get("commands") or {})


def test_every_main_py_is_registered() -> None:
    existing = _existing_main_files()
    registered = set(_registry().keys())

    unregistered = sorted(existing - registered)
    assert not unregistered, (
        "These CLI command files exist but are not listed in "
        ".ratchets/cli-commands-registry.yaml. Add a one-line "
        "purpose for each:\n  - "
        + "\n  - ".join(unregistered)
        + "\n\nWhy: the registry forces every new command to be "
        "compared against the existing 20+ before it lands. Skipping "
        "registration is exactly how we end up with two CLIs that "
        "do the same thing under slightly different names."
    )


def test_every_registry_entry_has_a_file() -> None:
    existing = _existing_main_files()
    registered = set(_registry().keys())

    stale = sorted(registered - existing)
    assert not stale, (
        "These registry entries refer to files that no longer "
        "exist. Remove them from "
        ".ratchets/cli-commands-registry.yaml:\n  - "
        + "\n  - ".join(stale)
    )


def test_no_two_commands_have_the_same_purpose() -> None:
    registry = _registry()
    by_purpose: dict[str, list[str]] = {}
    for name, purpose in registry.items():
        key = " ".join(str(purpose or "").lower().split())
        if not key:
            continue
        by_purpose.setdefault(key, []).append(name)

    duplicates = {p: names for p, names in by_purpose.items() if len(names) > 1}
    assert not duplicates, (
        "Two or more CLI commands share the same purpose line. "
        "Either extend the existing one, or write a more specific "
        "purpose for the new one:\n  - "
        + "\n  - ".join(
            f"{p!r}: {names}" for p, names in duplicates.items()
        )
    )
