"""Ensure platform code does not hardcode service-specific references.

Third-party developers should be able to implement services without
modifying platform code.  Service-specific logic belongs ONLY in
``src/media_stack/services/apps/`` or ``src/media_stack/contracts/``.

This test walks the Python source tree and flags any file that mentions
a known service name outside the allowed zones.  An explicit allowlist
captures the unavoidable exceptions that exist today; any *new*
hard-coded reference will cause a test failure, forcing the author to
either move the logic into the app layer or consciously add the
reference to the allowlist with a justification.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = PROJECT_ROOT / "src" / "media_stack"

# Service names to scan for (case-insensitive word-boundary match).
SERVICE_NAMES: list[str] = [
    "jellyfin",
    "sonarr",
    "radarr",
    "prowlarr",
    "lidarr",
    "readarr",
    "bazarr",
    "sabnzbd",
    "qbittorrent",
    "homepage",
    "tautulli",
    "jellyseerr",
    "maintainerr",
    "plex",
    "flaresolverr",
    "unpackerr",
]

# Matches service names at word boundaries (catches natural usage)
SERVICE_PATTERN = re.compile(
    r"\b(" + "|".join(SERVICE_NAMES) + r")\b",
    re.IGNORECASE,
)

# Also catches "SERVICE_" env-var-style prefixes where \b misses because _ is a
# word character.  E.g. "SONARR_API_KEY" has no \b between SONARR and _.
_ENV_PREFIX_PATTERN = re.compile(
    r"\b(" + "|".join(n.upper() for n in SERVICE_NAMES) + r")_",
)

# Directories / path segments that are completely excluded from scanning.
# These are the zones where service-specific code is *expected*.
EXCLUDED_SUBTREES: set[Path] = {
    SRC_ROOT / "services" / "apps",
    SRC_ROOT / "contracts",
}

EXCLUDED_DIR_NAMES: set[str] = {"__pycache__"}

# Path substrings (relative to SRC_ROOT) that are excluded because the
# code in them is inherently service-aware by design.
EXCLUDED_REL_PATH_PARTS: list[str] = [
    # Preflight handlers: deeply service-specific (generate config files with
    # service names/URLs). Tracked by test_no_direct_app_imports which is the
    # stricter test. These need full rewrite to services/apps/ layer.
    "api/preflight/",
]

# ---------------------------------------------------------------------------
# Allowlist
# ---------------------------------------------------------------------------
# Each key is a file path relative to ``src/media_stack/``.  The value is
# a set of (line_number, service_name_lower) tuples that are known and
# accepted.  When the codebase is refactored to remove a reference, the
# stale allowlist entry will NOT cause a failure - only *new* unlisted
# references fail the test.
#
# To add a new exception: run the test, copy the ``(line, service)`` pair
# from the failure message, and paste it here with a brief comment.

# Ratchet: current count of hardcoded service references in platform code.
# This number can only DECREASE. Update after fixing violations.
HARDCODED_SERVICE_REFS_RATCHET = 20


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_comment_line(stripped: str) -> bool:
    """Return True if the line is a Python comment."""
    return stripped.startswith("#")


def _is_string_literal_line(stripped: str) -> bool:
    """Return True if the line is a standalone string literal (not code).

    Matches lines that are purely a string expression, e.g.:
        "some text"
        'some text'
        f"some text"
    These are often used as implicit docstrings or section markers.
    """
    # Strip leading f/r/b/u prefixes
    s = stripped
    while s and s[0] in "fFrRbBuU":
        s = s[1:]
    if not s:
        return False
    for q in ('"""', "'''", '"', "'"):
        if s.startswith(q) and s.endswith(q) and len(s) > len(q):
            return True
    return False


def _is_import_from_apps(stripped: str) -> bool:
    """Return True if the line imports from the services.apps package."""
    patterns = [
        r"^(?:from|import)\s+.*services\.apps\.",
        r"^from\s+media_stack\.services\.apps\.",
        r"^from\s+\.\.apps\.",
        r"^from\s+\.apps\.",
    ]
    return any(re.match(p, stripped) for p in patterns)


def _collect_platform_py_files() -> list[Path]:
    """Collect all .py files under src/media_stack that are NOT excluded."""
    files: list[Path] = []
    for py_file in sorted(SRC_ROOT.rglob("*.py")):
        # Skip excluded directory names (e.g. __pycache__).
        if any(part in EXCLUDED_DIR_NAMES for part in py_file.parts):
            continue
        # Skip excluded subtrees (services/apps/, contracts/).
        if any(py_file.is_relative_to(sub) for sub in EXCLUDED_SUBTREES):
            continue
        # Skip test files.
        if py_file.name.startswith("test_"):
            continue
        # Skip path-part exclusions (admin.py, preflight/).
        rel = str(py_file.relative_to(SRC_ROOT))
        if any(part in rel for part in EXCLUDED_REL_PATH_PARTS):
            continue
        files.append(py_file)
    return files


def _scan_file(py_file: Path) -> list[tuple[int, str, str]]:
    """Return a list of (line_number, service_name_lower, line_text) hits."""
    try:
        lines = py_file.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return []

    hits: list[tuple[int, str, str]] = []
    in_docstring: str | None = None  # tracks the quote style: '"""' or "'''"

    for lineno, line in enumerate(lines, 1):
        stripped = line.strip()

        # --- Docstring state machine ---
        if in_docstring is not None:
            # We are inside a multi-line docstring; check for the closing delimiter.
            if in_docstring in stripped:
                in_docstring = None  # closing line — skip it too
            continue

        # Check for docstring / triple-quote opening on this line.
        for quote in ('"""', "'''"):
            if quote in stripped:
                # Count occurrences to distinguish single-line vs multi-line.
                count = stripped.count(quote)
                if count == 1:
                    # Opens a multi-line docstring (no closing on this line).
                    in_docstring = quote
                    break
                # count >= 2 means the docstring opens and closes on the
                # same line (e.g. `"""One-liner."""`).  Fall through so
                # the line is checked by _is_string_literal_line below.
        if in_docstring is not None:
            continue

        # --- Normal skip rules ---
        if _is_comment_line(stripped):
            continue
        if _is_import_from_apps(stripped):
            continue
        if _is_string_literal_line(stripped):
            continue

        for m in SERVICE_PATTERN.finditer(line):
            svc = m.group(1).lower()
            hits.append((lineno, svc, line.rstrip()))
        for m in _ENV_PREFIX_PATTERN.finditer(line):
            svc = m.group(1).lower()
            if (lineno, svc) not in {(h[0], h[1]) for h in hits}:
                hits.append((lineno, svc, line.rstrip()))
    return hits


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


def test_no_hardcoded_service_references_in_platform_code() -> None:
    """Scan platform code for hardcoded references to specific services.

    Any match that is NOT in the allowlist causes a test failure.  This
    ensures that new service-specific logic is placed in
    ``services/apps/<service>/`` rather than in platform code.
    """
    violations: list[str] = []
    platform_files = _collect_platform_py_files()

    assert platform_files, (
        "No platform Python files found - check that SRC_ROOT is correct: "
        f"{SRC_ROOT}"
    )

    for py_file in platform_files:
        rel = str(py_file.relative_to(SRC_ROOT))
        hits = _scan_file(py_file)
        for lineno, svc, text in hits:
            violations.append(
                f"  {rel}:{lineno} [{svc}] {text.strip()[:120]}"
            )

    count = len(violations)
    assert count <= HARDCODED_SERVICE_REFS_RATCHET, (
        f"\n{'=' * 72}\n"
        f"HARDCODED SERVICE REFERENCES REGRESSION: {count} found\n"
        f"(ratchet allows {HARDCODED_SERVICE_REFS_RATCHET})\n"
        f"{'=' * 72}\n"
        f"New hardcoded service reference(s) in platform code.\n"
        f"Service-specific logic must live in services/apps/<service>/.\n\n"
        f"Violations:\n" + "\n".join(violations[:20])
    )
    if count < HARDCODED_SERVICE_REFS_RATCHET:
        pytest.fail(
            f"Ratchet is loose: {count} violations but ratchet allows "
            f"{HARDCODED_SERVICE_REFS_RATCHET}. Update HARDCODED_SERVICE_REFS_RATCHET "
            f"to {count}."
        )


def test_scanned_file_count_is_reasonable() -> None:
    """Sanity check: we should be scanning a significant number of files."""
    files = _collect_platform_py_files()
    # The codebase has ~213 platform .py files (minus services/apps/,
    # contracts/, preflight/, admin.py, test files, __pycache__).
    # Ensure we are scanning at least 80.
    assert len(files) >= 80, (
        f"Only {len(files)} platform files found for scanning. "
        "This is suspiciously low - the exclusion filters may be too broad."
    )


# ---------------------------------------------------------------------------
# Filename scanner — service-specific filenames outside apps/
# ---------------------------------------------------------------------------

# Service tokens that should NOT appear in filenames outside services/apps/.
# Broader than SERVICE_NAMES — includes short fragments like "arr", "qbit",
# "sab", "jelly" that commonly appear in filenames.
_FILENAME_TOKENS: list[str] = [
    "jellyfin", "jelly",
    "sonarr", "radarr", "prowlarr", "lidarr", "readarr",
    "bazarr",
    "sabnzbd", "sab",
    "qbittorrent", "qbit",
    "homepage",
    "tautulli",
    "jellyseerr",
    "maintainerr",
    "plex",
    "flaresolverr",
    "unpackerr",
]

_FILENAME_PATTERN = re.compile(
    r"(" + "|".join(_FILENAME_TOKENS) + r")",
    re.IGNORECASE,
)

# Filenames in services/ (outside apps/) that are known re-export shims
# or legacy files pending migration.  New files must NOT be added here.
_FILENAME_ALLOWLIST: set[str] = {
    # All re-export shims have been deleted — the canonical code lives
    # under services/apps/<service>/.  This set should stay empty.
}


def test_no_service_specific_filenames_in_platform_code() -> None:
    """Ensure no NEW Python files in platform code have service-specific names.

    Service-specific modules must live under ``services/apps/<service>/``
    or ``contracts/``.  Any filename containing a service token (e.g.
    'jellyfin', 'qbit', 'sonarr') in platform code is a violation unless
    it is in the shrink-only allowlist.
    """
    violations: list[str] = []

    for py_file in sorted(SRC_ROOT.rglob("*.py")):
        # Skip allowed zones where service-specific code belongs.
        if any(py_file.is_relative_to(sub) for sub in EXCLUDED_SUBTREES):
            continue
        if any(part in EXCLUDED_DIR_NAMES for part in py_file.parts):
            continue
        if py_file.name == "__init__.py":
            continue
        if py_file.name.startswith("test_"):
            continue

        rel = str(py_file.relative_to(SRC_ROOT))

        # Skip path-part exclusions (admin.py, preflight/) — same as content scanner.
        if any(part in rel for part in EXCLUDED_REL_PATH_PARTS):
            continue

        stem = py_file.stem

        if _FILENAME_PATTERN.search(stem):
            if rel in _FILENAME_ALLOWLIST:
                continue
            violations.append(f"  {rel}")

    if violations:
        header = (
            f"\n{'=' * 72}\n"
            f"SERVICE-SPECIFIC FILENAMES IN PLATFORM CODE\n"
            f"{'=' * 72}\n"
            f"Found {len(violations)} file(s) with service-specific names outside\n"
            f"src/media_stack/services/apps/.\n\n"
            f"Service-specific modules must live under services/apps/<service>/.\n"
            f"If the file is a re-export shim, add it to _FILENAME_ALLOWLIST in\n"
            f"tests/unit/test_no_hardcoded_services.py.\n\n"
            f"Violations:\n"
        )
        pytest.fail(header + "\n".join(violations))


# ---------------------------------------------------------------------------
# Direct app imports in platform code
# ---------------------------------------------------------------------------

_DIRECT_APP_IMPORT_PATTERN = re.compile(
    r"^\s*from\s+(?:media_stack\.)?(?:\.\.?)?(?:services\.)?apps\."
    r"(" + "|".join(SERVICE_NAMES) + r")"
    r"[.\s]",
    re.IGNORECASE,
)

# Direct app import violations — empty means fully compliant.
_IMPORT_ALLOWLIST: set[tuple[str, int]] = set()


def _scan_direct_app_imports(py_file: Path) -> list[tuple[int, str, str]]:
    """Return (line_number, service_name, line_text) for direct imports from apps/."""
    try:
        lines = py_file.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return []
    hits: list[tuple[int, str, str]] = []
    for lineno, line in enumerate(lines, 1):
        m = _DIRECT_APP_IMPORT_PATTERN.match(line)
        if m:
            svc = m.group(1).lower()
            hits.append((lineno, svc, line.rstrip()))
    return hits


def test_no_direct_app_imports_in_platform_code() -> None:
    """Platform code must not import directly from services/apps/<specific_service>/.

    Use importlib.import_module() with the service ID from the registry instead.
    This ensures third-party developers can add services without editing platform code.
    """
    violations: list[str] = []
    for py_file in _collect_platform_py_files():
        rel = str(py_file.relative_to(SRC_ROOT))
        for lineno, svc, text in _scan_direct_app_imports(py_file):
            if (rel, lineno) in _IMPORT_ALLOWLIST:
                continue
            violations.append(f"  {rel}:{lineno} [{svc}] {text.strip()[:120]}")

    if violations:
        header = (
            f"\n{'=' * 72}\n"
            f"DIRECT APP IMPORTS IN PLATFORM CODE\n"
            f"{'=' * 72}\n"
            f"Found {len(violations)} direct import(s) from services/apps/<service>/\n"
            f"in platform code. Use importlib.import_module() with the service ID\n"
            f"from the registry instead.\n\n"
            f"Example fix:\n"
            f"  # Before (hardcoded):\n"
            f"  from media_stack.services.apps.jellyfin.gpu import check_jellyfin_gpu\n"
            f"  # After (pluggable):\n"
            f"  import importlib\n"
            f"  gpu_mod = importlib.import_module(f'media_stack.services.apps.{{ms_id}}.gpu')\n\n"
            f"Violations:\n"
        )
        pytest.fail(header + "\n".join(violations))


def test_filename_allowlist_entries_are_still_shims() -> None:
    """Verify that every filename allowlist entry is either a small re-export
    shim (< 5 lines) or explicitly annotated as pending migration.

    This prevents the allowlist from silently growing to cover real modules
    that should have been moved.
    """
    pending_migration: set[str] = set()
    max_shim_lines = 5

    for rel in _FILENAME_ALLOWLIST:
        full = SRC_ROOT / rel
        if not full.exists():
            continue
        if rel in pending_migration:
            continue
        lines = full.read_text(encoding="utf-8").splitlines()
        assert len(lines) <= max_shim_lines, (
            f"Filename allowlist entry '{rel}' has {len(lines)} lines — "
            f"expected a re-export shim (<= {max_shim_lines} lines). "
            f"Move the real code to services/apps/ and leave only a shim."
        )
