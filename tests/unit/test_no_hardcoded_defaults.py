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

# Ratchet: current count of hardcoded configurable defaults in platform code.
# This number can only DECREASE. Update after moving defaults to config YAML.
HARDCODED_DEFAULTS_RATCHET = 10


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
            violations.append(f"  {rel}:{lineno} [{label}] {text.strip()[:120]}")

    count = len(violations)
    assert count <= HARDCODED_DEFAULTS_RATCHET, (
        f"\n{'=' * 72}\n"
        f"HARDCODED DEFAULTS REGRESSION: {count} found\n"
        f"(ratchet allows {HARDCODED_DEFAULTS_RATCHET})\n"
        f"{'=' * 72}\n"
        f"Move defaults to profile YAML or contracts/.\n\n"
        f"Violations:\n" + "\n".join(violations[:20])
    )
    if count < HARDCODED_DEFAULTS_RATCHET:
        pytest.fail(
            f"Ratchet is loose: {count} violations but ratchet allows "
            f"{HARDCODED_DEFAULTS_RATCHET}. Update HARDCODED_DEFAULTS_RATCHET "
            f"to {count}."
        )


def test_scanned_file_count_reasonable() -> None:
    """Sanity check: we should scan a significant number of files."""
    files = _collect_files()
    assert len(files) >= 30, f"Only {len(files)} files scanned — filters may be too broad"
