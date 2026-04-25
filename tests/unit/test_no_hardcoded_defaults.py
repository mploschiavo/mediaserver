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
#
# 2026-04-25 (v1.0.211): the C+E ratchet broadening picked up
# 12 new violations the previous scan missed — product-specific
# media-path / download-path / default-credential literals scattered
# across servarr media-integrity factories, hygiene ops, preflight
# checks, content download settings, and a probe-promises default
# password (``adminadmin``). All are real tech debt that needs to
# move to config; pinning at the current count locks the rule
# going forward, and each removal lowers the number.
HARDCODED_DEFAULTS_RATCHET = 23

# Bug class E (filter-literal hardcoded defaults). The crash that surfaced
# this: ``GATEWAY_DOMAIN_SUFFIX`` defaulted to ".media-stack.local" and
# was used as a regex/endswith filter; on a real deployment with
# gateway_host="m.iomio.io" the filter silently dropped *every* hostname
# from the file-derived inventory. The URL-literal scanner above didn't
# catch it because the value is a domain suffix, not a URL.
#
# We flag two shapes:
#
#   * Any string-literal occurrence of a known product-specific suffix
#     used as a *filter* — ``host.endswith(".media-stack.local")``,
#     ``"media-stack.local" in sni``, ``re.compile(r"...media-stack\.local...")``.
#   * Any module-level constant assigned to a product-specific suffix
#     that is then passed into one of those filter operations.
#
# Constructing a hostname from the suffix (``f"apps.{suffix}"``) is fine —
# that's a default the operator can override. Filtering *against* the
# suffix is what bricks real deployments, because the operator's
# hostname doesn't end with the dev-default suffix.
#
# Each tuple: (label, regex). The regex matches a *filter* call,
# not just the literal occurrence — see ``_scan_filter_literals``.
FILTER_LITERAL_PATTERNS: list[tuple[str, re.Pattern]] = [
    (
        "endswith product-specific suffix",
        re.compile(
            r"""\.endswith\(\s*['"][^'"]*media-stack\.local['"]"""
        ),
    ),
    (
        "membership against product-specific suffix",
        re.compile(
            r"""['"][^'"]*media-stack\.local['"]\s+in\s+\w+"""
        ),
    ),
    (
        "regex filter against product-specific suffix",
        re.compile(
            r"""re\.compile\([^)]*media-stack\\?\.local"""
        ),
    ),
]

# Files where the filter-literal pattern is intentionally and unavoidably
# present — typically because the file's *only* job is to map between
# the compose-default suffix and the operator's real config.
#
# Add an entry here when the literal is provably:
#   (a) executed only against compose-default routes (no production
#       deploy with that suffix exists), or
#   (b) wrapped in a feature flag (``if _K8S_UNIFIED:``) so it can't
#       short-circuit a production hostname filter.
FILTER_LITERAL_ALLOWLIST: set[str] = {
    # CLI promise-prober translates compose-default SNI ("apps.media-stack.local")
    # to whatever the operator actually configured (m.iomio.io, etc.).
    # The "media-stack.local" literal here is the *source* of the
    # rewrite, not the filter that gates real-deployment hostnames —
    # the function explicitly bails out if K8s unified mode isn't set.
    # See _rewrite_sni_for_k8s in probe_promises.py.
    "cli/commands/probe_promises.py",
}

# Ratchet: filter-literal violations currently tolerated. This number
# may only DECREASE. Update after migrating the call site to read the
# suffix from routing config.
FILTER_LITERAL_DEFAULTS_RATCHET = 0


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


def _scan_filter_literals(py_file: Path) -> list[tuple[int, str, str]]:
    """Return (line_number, label, line_text) for every filter-literal
    hit in ``py_file``. Comments are ignored. Files in the allowlist
    are skipped entirely."""
    try:
        rel = str(py_file.relative_to(SRC_ROOT))
    except ValueError:
        return []
    if rel in FILTER_LITERAL_ALLOWLIST:
        return []
    try:
        lines = py_file.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return []
    hits: list[tuple[int, str, str]] = []
    for lineno, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        for label, pattern in FILTER_LITERAL_PATTERNS:
            if pattern.search(line):
                hits.append((lineno, label, line.rstrip()))
    return hits


def test_no_hardcoded_filter_literals() -> None:
    """Bug class E: product-specific suffix used as a filter literal.

    Catches the shape that bricked /api/routing-probe on a real
    deployment: ``GATEWAY_DOMAIN_SUFFIX = ".media-stack.local"`` used
    as the regex/endswith filter for hostname extraction. The
    operator's hostnames (m.iomio.io, *.example.com) never matched the
    compose-default suffix, so the file-derived hostname inventory was
    silently empty and the SPA's Routing tab rendered with no data.

    What's allowed
    --------------
    Constructing a hostname from a configured suffix is fine.
    Filtering *against* a hardcoded suffix is not — read the suffix
    from routing config (``config_svc.get_routing()['base_domain']``
    etc.) so the operator's deploy isn't a second-class citizen.

    What's flagged
    --------------
    See ``FILTER_LITERAL_PATTERNS`` for the exact shapes. The set is
    intentionally narrow — false positives have a clear escape hatch
    in ``FILTER_LITERAL_ALLOWLIST`` with a written justification.
    """
    violations: list[str] = []
    for py_file in _collect_files():
        if py_file.suffix != ".py":
            # Limit this check to Python — TS/JS hostname filtering
            # happens against operator-supplied routing data already.
            continue
        try:
            rel = str(py_file.relative_to(SRC_ROOT))
        except ValueError:
            rel = str(py_file.relative_to(PROJECT_ROOT))
        for lineno, label, text in _scan_filter_literals(py_file):
            violations.append(
                f"  {rel}:{lineno} [{label}] {text.strip()[:140]}"
            )

    count = len(violations)
    assert count <= FILTER_LITERAL_DEFAULTS_RATCHET, (
        f"\n{'=' * 72}\n"
        f"FILTER-LITERAL DEFAULTS REGRESSION: {count} found\n"
        f"(ratchet allows {FILTER_LITERAL_DEFAULTS_RATCHET})\n"
        f"{'=' * 72}\n"
        f"Read the suffix from routing config — see the docstring on\n"
        f"this test for the bug class.\n\n"
        f"Violations:\n" + "\n".join(violations[:20])
    )
    if count < FILTER_LITERAL_DEFAULTS_RATCHET:
        pytest.fail(
            f"Filter-literal ratchet is loose: {count} violations but "
            f"ratchet allows {FILTER_LITERAL_DEFAULTS_RATCHET}. Update "
            f"FILTER_LITERAL_DEFAULTS_RATCHET to {count}."
        )
