"""Ratchets that prevent two related anti-patterns we keep hitting:

1. **Hardcoded service-id lists in code** — e.g. the wizard's
   ``SVCS.find(x => x.id === 'jellyfin' || x.id === 'plex' ||
   x.id === 'emby')`` that produced "Connect to Emby" copy on a
   Jellyfin-only stack. The registry already knows what's
   deployed — code that reinvents that knowledge with a literal
   list will drift.

2. **UI text that mentions a media server but doesn't come from
   the profile binding** — same root cause, different surface.
   The wizard's "Open Jellyfin to watch" needs to read the
   actual ``technology_bindings.media_server`` value, not
   substitute a guess from the registry."""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))


# ----------------------------------------------------------------------
# #2 — no hardcoded ID lists in api/services or services/apps
# ----------------------------------------------------------------------


# Two or more known service ids appearing in a single literal
# array/list/tuple is the bug shape. Every contract under
# contracts/services/<id>.yaml gives us the canonical id set.
def _registry_service_ids() -> set[str]:
    out: set[str] = set()
    for yaml_file in (ROOT / "contracts" / "services").glob("*.yaml"):
        if yaml_file.name == "_template.yaml":
            continue
        out.add(yaml_file.stem)
    return out


_KNOWN_IDS = _registry_service_ids()


# Dirs whose code is allowed to mention service ids freely (these
# are the *registries* of service ids — the source of truth).
_EXEMPT_FILE_PATTERNS = (
    re.compile(r".*/contracts/.*"),
    re.compile(r".*/registry\.py$"),
    re.compile(r".*/test_.*\.py$"),     # tests can pin specific IDs
    re.compile(r".*/tests/.*"),
    # The plugin-manifest loader walks contracts; ID strings inside
    # are field names from that schema, not hardcoding.
    re.compile(r".*/plugin_manifest_loader\.py$"),
    # Default fallback constants that explicitly model "what a
    # vanilla deploy looks like" — kept here for the dashboard's
    # offline rendering; the rest of the code reads from /api/services.
    re.compile(r".*/dashboard\.html$"),
    # SERVICE_PROBES / AUTH_PROBES dicts in health.py are derived
    # from the registry programmatically; the IDs there are
    # registry-driven, not literals.
    re.compile(r".*/api/services/health\.py$"),
)


# IDs we actually care about catching as "hardcoded" — common
# media-server / *arr / downloader names. Service ids like
# "_template" or one-off internal ones don't matter.
_HOT_IDS = frozenset({
    "jellyfin", "plex", "emby", "sonarr", "radarr", "lidarr",
    "readarr", "bazarr", "prowlarr", "qbittorrent", "sabnzbd",
    "jellyseerr", "overseerr", "tautulli", "maintainerr",
    "homepage", "authelia", "authentik",
})


def _file_is_exempt(path: Path) -> bool:
    rel = str(path).replace("\\", "/")
    return any(p.match(rel) for p in _EXEMPT_FILE_PATTERNS)


# Pattern: array/list/tuple/set literal containing two or more
# string literals from _HOT_IDS. Matches both Python and JS quoting.
_HOT_ID_RE = "|".join(re.escape(i) for i in _HOT_IDS)
_LIST_LITERAL_RE = re.compile(
    r"""[\[\(\{]\s*           # opening bracket
        (?:['"](?:""" + _HOT_ID_RE + r""")['"]\s*[,\s]\s*){2,}
                              # >= 2 hot IDs separated by commas/spaces
        ['"](?:""" + _HOT_ID_RE + r""")['"]
                              # final hot ID
        \s*[\]\)\}]           # closing bracket
    """,
    re.VERBOSE,
)


# Justified exceptions — a hardcoded list here is a *domain concept*
# (e.g. "the download pipeline consists of these specific services"),
# not the buggy "let's iterate the registry by guessing what's there"
# pattern. Each entry is a (relative_path, one-line justification).
# To add: write the justification, run the test, copy the line number
# the failure cites.
_JUSTIFIED_LITERALS = {
    "src/media_stack/api/services/health_stories.py":
        "_DOWNLOAD_PATH defines which services constitute the "
        "downloads pipeline — domain concept used by the composite "
        "story rules, not a 'find what's installed' guess.",
    "src/media_stack/services/apps/prowlarr/cli/"
    "prowlarr_auto_indexers_runtime.py":
        "Prowlarr's auto-indexer skip list — services that don't "
        "consume indexers regardless of deployment shape.",
    # Defensive fallback when ``api.services.registry`` import fails
    # — the literal mirrors the ``category=='media'`` query the
    # primary path runs against the registry.
    "src/media_stack/services/edge/envoy_config_generator.py":
        "Media-server fallback set used only when registry import "
        "fails; matches the registry's category=='media' filter.",
    # The *arr media-collection family (sonarr/radarr/lidarr/readarr)
    # is a real domain concept — these are the apps that consume
    # download-client API keys in the same shape and are paired
    # together in every Servarr workflow. Prowlarr is intentionally
    # excluded (it's an indexer, not a collection app).
    "src/media_stack/services/apps/core/job_adapters.py":
        "Servarr media-collection family — sonarr/radarr/lidarr/"
        "readarr share API-key shape and download-client wiring; "
        "prowlarr is an indexer and lives in its own loop.",
    # Lifecycle module is the *arr-shaped lifecycle adapter; the
    # frozenset declares the contract surface the module supports.
    "src/media_stack/adapters/servarr/lifecycle.py":
        "_SUPPORTED_SERVICE_IDS — the contract surface for the "
        "Servarr lifecycle adapter (the apps that share the *arr "
        "shape: sonarr/radarr/lidarr/readarr/prowlarr).",
    # ADR-0005 Phase 5c.1 (wide) — same Servarr contract surface as
    # the lifecycle module above, declared on the wirer so the
    # constructor can validate the per-call ``service_id`` against
    # the supported family.
    "src/media_stack/adapters/servarr/api_key_wiring.py":
        "_SUPPORTED_SERVICE_IDS — Servarr api-key-discoverable wirer "
        "supports the same family as ServarrLifecycle "
        "(sonarr/radarr/lidarr/readarr/prowlarr).",
}


class HardcodedServiceIdListRatchet(unittest.TestCase):

    def test_no_multi_id_literal_in_application_code(self) -> None:
        offenders: list[str] = []
        for py in (ROOT / "src" / "media_stack").rglob("*.py"):
            if _file_is_exempt(py):
                continue
            rel_str = str(py.relative_to(ROOT)).replace("\\", "/")
            if rel_str in _JUSTIFIED_LITERALS:
                continue
            text = py.read_text(encoding="utf-8", errors="ignore")
            for match in _LIST_LITERAL_RE.finditer(text):
                line_no = text[:match.start()].count("\n") + 1
                snippet = match.group(0)[:80].replace("\n", " ")
                offenders.append(f"{rel_str}:{line_no}: {snippet!r}")
        self.assertFalse(
            offenders,
            "Hardcoded service-id list literal found:\n  - "
            + "\n  - ".join(offenders)
            + "\n\nReplace with `registry.SERVICES` filtered by an "
              "appropriate predicate (is_service_enabled, "
              "technology_bindings.media_server, etc.). The bug shape: "
              "the registry contains every shipped contract, so a "
              "literal find/in check returns whichever id appears "
              "first in the array — even when the user didn't deploy "
              "it. (2026-04-21 wizard 'Connect to Emby on Jellyfin "
              "stack' bug.)\n\nIf this list is genuinely a domain "
              "concept (not a deployment guess), add the file path "
              "to _JUSTIFIED_LITERALS in this test with a one-line "
              "justification.",
        )


# #3 — wizard text must use _techBindings, not a literal media-server
# name. Retired with dashboard.html in v1.0.193 — the SPA wizard at
# ``ui/src/components/wizard/`` owns this assertion now via its own
# vitest suite.
if __name__ == "__main__":
    unittest.main()
