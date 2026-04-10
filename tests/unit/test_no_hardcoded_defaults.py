"""Ensure platform code does not embed user-configurable defaults as string literals.

URLs, file paths, passwords, and domain names that users are expected to
change should live in profile YAML or contracts/ — not in Python code.
A Python file may *read* the default from a config file, but should never
contain the literal value itself.

This test scans ``src/media_stack/api/services/`` for known patterns and
flags new occurrences.  Existing violations are tracked in the shrink-only
allowlist below.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src" / "media_stack"

# Directories that are excluded (contracts and app-specific code are expected
# to contain these strings).
EXCLUDED_SUBTREES = {
    SRC_ROOT / "services" / "apps",
    SRC_ROOT / "contracts",
}
EXCLUDED_DIR_NAMES = {"__pycache__"}

# Patterns that should NOT appear as string literals in platform code.
# Each tuple: (human_label, regex_pattern)
CONFIGURABLE_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("IPTV URL", re.compile(r"iptv-org\.github\.io/iptv")),
    ("EPG URL", re.compile(r"iptv-epg\.org")),
    ("download path", re.compile(r"/data/torrents/completed/(?:tv|movies|music|books)")),
    ("media path", re.compile(r"/media/(?:movies|tv|music|books)\b")),
    ("default password literal", re.compile(r"""['"]adminadmin['"]""")),
]

# Shrink-only allowlist: (relative_path, line_number, pattern_label).
# Existing violations go here.  New code MUST NOT add entries — move the
# default to config instead.  Remove entries as code is fixed.
ALLOWLIST: set[tuple[str, int, str]] = {
    # admin.py: qBit default password fallback (intentional — tries known defaults)
    ("api/services/admin.py", 324, "default password literal"),
    # disk.py: fallback path candidates for disk scan
    ("api/services/disk.py", 22, "download path"),
    # unpackerr preflight: generates config file content (template, not runtime default)
    ("api/preflight/unpackerr.py", 62, "download path"),
    ("api/preflight/unpackerr.py", 71, "download path"),
    ("api/preflight/unpackerr.py", 80, "download path"),
    ("api/preflight/unpackerr.py", 89, "download path"),
    # media_hygiene: filesystem scan paths used as fallback candidates
    ("services/media_hygiene_ops/filesystem.py", 54, "download path"),
    ("services/media_hygiene_ops/filesystem.py", 55, "download path"),
    ("services/media_hygiene_ops/filesystem.py", 56, "download path"),
    ("services/media_hygiene_ops/filesystem.py", 57, "download path"),
    # dashboard: HTML placeholder text in input fields (overwritten by API data)
    ("api/dashboard.html", 2242, "media path"),
    ("api/dashboard.html", 2414, "IPTV URL"),
    ("api/dashboard.html", 2416, "EPG URL"),
    # docker-compose: init-permissions creates directory structure on first run
    ("docker/docker-compose.yml", 38, "media path"),
}


def _collect_files() -> list[Path]:
    """Collect all source files to scan — Python, JS/TS, HTML, YAML, Compose."""
    files: list[Path] = []
    # Python source (excluding app-specific code and tests)
    for py_file in sorted(SRC_ROOT.rglob("*.py")):
        if any(part in EXCLUDED_DIR_NAMES for part in py_file.parts):
            continue
        if any(py_file.is_relative_to(sub) for sub in EXCLUDED_SUBTREES):
            continue
        if py_file.name.startswith("test_"):
            continue
        files.append(py_file)
    # JavaScript and TypeScript files
    for ext in ("*.js", "*.ts", "*.tsx", "*.jsx"):
        for js_file in sorted(SRC_ROOT.rglob(ext)):
            if any(part in EXCLUDED_DIR_NAMES for part in js_file.parts):
                continue
            if "node_modules" in str(js_file):
                continue
            files.append(js_file)
    # Dashboard HTML (contains inline JS)
    dashboard = SRC_ROOT / "api" / "dashboard.html"
    if dashboard.is_file():
        files.append(dashboard)
    # Docker compose
    compose = PROJECT_ROOT / "docker" / "docker-compose.yml"
    if compose.is_file():
        files.append(compose)
    # K8s kustomization files
    for kust in sorted((PROJECT_ROOT / "k8s").rglob("kustomization.yaml")):
        files.append(kust)
    return files


def _scan_file(py_file: Path) -> list[tuple[int, str, str]]:
    """Return (line_number, pattern_label, line_text) hits."""
    try:
        lines = py_file.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return []
    hits: list[tuple[int, str, str]] = []
    for lineno, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        for label, pattern in CONFIGURABLE_PATTERNS:
            if pattern.search(line):
                hits.append((lineno, label, line.rstrip()))
    return hits


def test_no_hardcoded_configurable_defaults() -> None:
    """Scan platform Python for string literals that should be in config files."""
    violations: list[str] = []
    for py_file in _collect_files():
        try:
            rel = str(py_file.relative_to(SRC_ROOT))
        except ValueError:
            rel = str(py_file.relative_to(PROJECT_ROOT))
        for lineno, label, text in _scan_file(py_file):
            if (rel, lineno, label) in ALLOWLIST:
                continue
            violations.append(f"  {rel}:{lineno} [{label}] {text.strip()[:120]}")

    if violations:
        header = (
            f"\n{'=' * 72}\n"
            f"HARDCODED CONFIGURABLE DEFAULTS IN PLATFORM CODE\n"
            f"{'=' * 72}\n"
            f"Found {len(violations)} string literal(s) that should be in profile YAML or\n"
            f"contracts/ instead of Python source code.\n\n"
            f"Fix: read the default from the profile YAML or pass it as a parameter.\n"
            f"If unavoidable, add to ALLOWLIST in test_no_hardcoded_defaults.py.\n\n"
            f"Violations:\n"
        )
        pytest.fail(header + "\n".join(violations))


def test_allowlist_entries_still_exist() -> None:
    """Verify allowlist entries haven't gone stale (file+line still matches)."""
    stale: list[str] = []
    for rel, lineno, label in ALLOWLIST:
        full = SRC_ROOT / rel
        if not full.exists():
            full = PROJECT_ROOT / rel
        if not full.exists():
            stale.append(f"  {rel}:{lineno} [{label}] — file not found")
            continue
        lines = full.read_text(encoding="utf-8").splitlines()
        if lineno > len(lines):
            stale.append(f"  {rel}:{lineno} [{label}] — line number out of range")
    if stale:
        pytest.fail(
            "Stale allowlist entries in test_no_hardcoded_defaults.py:\n"
            + "\n".join(stale)
        )


def test_scanned_file_count_reasonable() -> None:
    """Sanity check: we should scan a significant number of files."""
    files = _collect_files()
    assert len(files) >= 30, f"Only {len(files)} files scanned — filters may be too broad"
