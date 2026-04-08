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
import textwrap
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[2]
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

SERVICE_PATTERN = re.compile(
    r"\b(" + "|".join(SERVICE_NAMES) + r")\b",
    re.IGNORECASE,
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
    # Admin handlers: qBittorrent/Jellyfin password resets that cannot be
    # registry-driven because each service uses a unique auth mechanism.
    "api/services/admin.py",
    # Preflight discovery: reads service config files to discover keys
    # before the registry is available.
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

ALLOWLIST: dict[str, set[tuple[int, str]]] = {
    # ── Adapters ──────────────────────────────────────────────────────
    # These adapter modules are service-specific by nature.  They provide
    # config-file helpers consumed by the app layer.  Long-term they
    # should migrate into services/apps/<svc>/ but today they live under
    # adapters/ which is platform code.
    "adapters/bazarr.py": {
        "WHOLE_FILE",
    },
    "adapters/__init__.py": {
        (3, "bazarr"),
        (13, "homepage"),
        (14, "homepage"),
        (16, "jellyfin"),
        (33, "bazarr"),
    },
    "adapters/homepage.py": {
        # The entire file is a Homepage-specific adapter with service
        # name lists, default hosts, etc.  Should eventually migrate to
        # services/apps/homepage/ but is currently here.
        "WHOLE_FILE",
    },
    "adapters/jellyfin.py": {
        "WHOLE_FILE",
    },

    # ── Service layer: runtime factory ────────────────────────────────
    # The runtime factory builds the bootstrap runtime state.  It reads
    # per-service config keys like ``configure_jellyfin_libraries`` from
    # the resolved config and passes them as named fields.  Refactoring
    # this to a fully dynamic model is planned but not yet done.
    "services/runtime_factory/models.py": {
        "WHOLE_FILE",
    },
    "services/runtime_factory/runtime_builder.py": {
        "WHOLE_FILE",
    },
    "services/runtime_factory/config_loader.py": {
        (50, "jellyfin"),
        (51, "bazarr"),
        (54, "bazarr"),
        (54, "jellyseerr"),
        (54, "homepage"),
        (54, "maintainerr"),
        (54, "flaresolverr"),
        (54, "jellyfin"),
        (73, "sonarr"),
        (73, "radarr"),
    },
    "services/runtime_factory/plan_builder.py": {
        "WHOLE_FILE",
    },
    "services/runtime_factory/binding_resolver.py": {
        # Default fallback when no request-manager technology is bound.
        (92, "jellyseerr"),
    },

    # ── Service layer: platform services ──────────────────────────────
    "services/runtime_models.py": {
        # Runtime state model: prowlarr_url/key fields, media_server_backend
        # and request_manager_backend defaults.  Pervasive references.
        "WHOLE_FILE",
    },
    "services/operation_wiring.py": {
        "WHOLE_FILE",
    },
    "services/runner_phase_plan_service.py": {
        (27, "prowlarr"),
        (28, "prowlarr"),
        (29, "prowlarr"),
    },
    "services/api_keys_service.py": {
        "WHOLE_FILE",
    },
    "services/config_artifacts_service.py": {
        "WHOLE_FILE",
    },
    "services/arr_service.py": {
        # Default host string for SABnzbd download client, and default
        # implementation string for qBittorrent.
        (72, "sabnzbd"),
        (207, "qbittorrent"),
    },
    "services/arr_indexer_sync_service.py": {
        "WHOLE_FILE",
    },
    "services/servarr_adapters.py": {
        "WHOLE_FILE",
    },
    "services/controller_service.py": {
        (252, "prowlarr"),
        (253, "prowlarr"),
        (304, "jellyfin"),
    },
    # top_level_config_model.py: prowlarr_indexers and similar identifiers
    # do not trigger word-boundary matches (underscore is \w).
    # auth_service.py line 161: comment line (filtered by # check).
    # Both kept out of allowlist since no actual matches occur.
    "services/disk_guardrails_service.py": {
        (113, "qbittorrent"),
        (202, "maintainerr"),
    },

    # ── Service layer: media-server & download-client adapters ────────
    # These adapter directories host per-technology implementations that
    # are effectively service-specific code living under services/ rather
    # than services/apps/.  Long-term they could be reorganised.
    "services/media_server_adapters/__init__.py": {
        (7, "jellyfin"),
        (10, "plex"),
        (18, "jellyfin"),
    },
    "services/media_server_adapters/jellyfin.py": {
        "WHOLE_FILE",
    },
    "services/media_server_adapters/plex.py": {
        "WHOLE_FILE",
    },
    "services/media_server_adapters/plans.py": {
        (22, "prowlarr"),
        (23, "prowlarr"),
    },
    "services/download_client_adapters/__init__.py": {
        (13, "qbittorrent"),
        (14, "sabnzbd"),
        (25, "qbittorrent"),
        (26, "sabnzbd"),
    },
    "services/download_client_adapters/qbittorrent.py": {
        "WHOLE_FILE",
    },
    "services/download_client_adapters/sabnzbd.py": {
        "WHOLE_FILE",
    },

    # ── Service layer: discovery lists ────────────────────────────────
    "services/discovery_lists/sonarr_seed.py": {
        "WHOLE_FILE",
    },
    "services/discovery_lists/ops.py": {
        # No word-boundary matches; stale entries left for reference.
        (17, "sonarr"),
        (18, "sonarr"),
        (30, "sonarr"),
    },
    "services/discovery_lists/kickoff.py": {
        # Implementation name checks: Lidarr / Readarr branch logic.
        (25, "lidarr"),
        (27, "readarr"),
        (37, "lidarr"),
        (39, "readarr"),
    },
    "services/discovery_lists/import_lists.py": {
        # Implementation name checks for Lidarr/Readarr metadata profile.
        (218, "lidarr"),
        (218, "readarr"),
    },

    # ── Service layer: media hygiene ──────────────────────────────────
    "services/media_hygiene_ops/duplicate_prune.py": {
        (34, "qbittorrent"),
    },
    "services/media_hygiene_ops/ipfilter.py": {
        (40, "qbittorrent"),
    },
    "services/media_hygiene_ops/queue_guardrails.py": {
        (40, "qbittorrent"),
    },

    # ── CLI commands ──────────────────────────────────────────────────
    "cli/commands/action_handlers.py": {
        (31, "jellyfin"),
        (43, "prowlarr"),
        (44, "prowlarr"),
        (45, "prowlarr"),
        (66, "prowlarr"),
    },
    "cli/commands/controller_main.py": {
        # Default media server ID, SABnzbd dynamic import fallback,
        # argparse description, and auto-prowlarr CLI flag.
        "WHOLE_FILE",
    },
    "cli/commands/maintenance.py": {
        "WHOLE_FILE",
    },
    "cli/commands/generate_envoy_config_main.py": {
        "WHOLE_FILE",
    },
    "cli/commands/run_controller_job_main.py": {
        (480, "homepage"),
        (482, "prowlarr"),
        (484, "homepage"),
        (486, "prowlarr"),
    },
    "cli/workflows/controller_component_resolver.py": {
        (471, "prowlarr"),
    },

    # ── API services ──────────────────────────────────────────────────
    # These API handler modules query services directly by name/port.
    # They should eventually be driven by the service registry, but
    # today they hardcode hostnames and ports.
    "api/server.py": {
        # Jellyfin hard-reset endpoint — unique auth mechanism can't be
        # registry-driven (SQLite DB password reset).
        "WHOLE_FILE",
    },
    "api/services/content.py": {
        "WHOLE_FILE",
    },
    "api/services/health.py": {
        "WHOLE_FILE",
    },
    "api/services/ops.py": {
        "WHOLE_FILE",
    },
    "api/services/config.py": {
        "WHOLE_FILE",
    },
    "api/services/disk.py": {
        (155, "qbittorrent"),
        (159, "qbittorrent"),
    },

    # ── Core: platform providers ──────────────────────────────────────
    "core/platforms/compose/services/edge_http_smoke.py": {
        # Hardcoded Arr API version paths and display names for HTTP smoke
        # tests. Should eventually be registry-driven.
        "WHOLE_FILE",
    },
    "core/platforms/compose/edge/providers/envoy/dynamic_config.py": {
        (725, "jellyfin"),
        (727, "homepage"),
        (727, "jellyfin"),
        (782, "homepage"),
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_comment_line(stripped: str) -> bool:
    """Return True if the line is a Python comment."""
    return stripped.startswith("#")


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
    for lineno, line in enumerate(lines, 1):
        stripped = line.strip()
        if _is_comment_line(stripped):
            continue
        if _is_import_from_apps(stripped):
            continue
        for m in SERVICE_PATTERN.finditer(line):
            svc = m.group(1).lower()
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
        if not hits:
            continue

        # Look up allowlist for this file.
        allowed = ALLOWLIST.get(rel, set())
        whole_file_allowed = "WHOLE_FILE" in allowed

        for lineno, svc, text in hits:
            if whole_file_allowed:
                continue
            if (lineno, svc) in allowed:
                continue
            violations.append(
                f"  {rel}:{lineno} [{svc}] {text.strip()[:120]}"
            )

    if violations:
        header = (
            f"\n{'=' * 72}\n"
            f"HARDCODED SERVICE REFERENCES IN PLATFORM CODE\n"
            f"{'=' * 72}\n"
            f"Found {len(violations)} new hardcoded service reference(s) in platform code.\n"
            f"Service-specific logic must live in src/media_stack/services/apps/<service>/\n"
            f"or src/media_stack/contracts/.\n\n"
            f"If the reference is unavoidable, add it to the ALLOWLIST in\n"
            f"tests/unit/test_no_hardcoded_services.py with a brief justification.\n\n"
            f"Violations:\n"
        )
        pytest.fail(header + "\n".join(violations))


def test_allowlist_has_no_stale_whole_file_entries() -> None:
    """Verify that every WHOLE_FILE allowlist entry refers to a real file."""
    for rel_path, allowed in ALLOWLIST.items():
        if "WHOLE_FILE" not in allowed:
            continue
        full = SRC_ROOT / rel_path
        assert full.exists(), (
            f"Stale WHOLE_FILE allowlist entry: {rel_path} does not exist"
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
