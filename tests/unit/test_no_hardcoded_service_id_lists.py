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

ROOT = Path(__file__).resolve().parents[2]
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


# ----------------------------------------------------------------------
# #3 — wizard text must use _techBindings, not a literal media-server name
# ----------------------------------------------------------------------


_DASHBOARD = (
    ROOT / "src" / "media_stack" / "api" / "dashboard.html"
).read_text(encoding="utf-8")


class WizardBindingRatchet(unittest.TestCase):

    def test_wizard_does_not_hardcode_media_server_name(self) -> None:
        """The wizard must construct media-server-related copy from
        the profile binding (``_techBindings.media_server`` →
        SVC_MAP lookup), not from string literals. Pin: inside
        ``renderWizardSteps``, the strings "Jellyfin", "Plex",
        "Emby" must NOT appear as literal copy. They may appear in
        comments or as IDs inside SVC_MAP lookups."""
        idx = _DASHBOARD.find("function renderWizardSteps")
        self.assertGreater(idx, -1)
        # Walk to the end of the function — naïve close-brace match
        # at depth 0 would miss this for sure, but every closing
        # </div> + the troubleshooting section gives us a stable
        # end marker.
        end = _DASHBOARD.find("function dismissWizard", idx)
        if end == -1:
            end = idx + 8000
        body = _DASHBOARD[idx:end]
        # Strip JS comments + commented HTML before scanning.
        # Block comments first, then line comments.
        cleaned = re.sub(r"/\*.*?\*/", "", body, flags=re.DOTALL)
        cleaned = re.sub(r"//[^\n]*", "", cleaned)
        cleaned = re.sub(r"<!--.*?-->", "", cleaned, flags=re.DOTALL)
        # Now look for media-server names inside string literals
        # that aren't part of an attribute list (style, etc.).
        bad: list[str] = []
        for media_name in ("Jellyfin", "Plex", "Emby"):
            pattern = re.compile(
                # JS string literal containing the word, but NOT
                # immediately preceded by ``mediaName`` (template
                # build) or appearing inside _techBindings/SVC_MAP
                # context.
                r"['\"][^'\"]*\b" + media_name + r"\b[^'\"]*['\"]"
            )
            for m in pattern.finditer(cleaned):
                snippet = m.group(0)
                # Whitelist patterns: bookmarks/groups text or the
                # docstring at the top.
                if "Open <b>" in cleaned[max(0, m.start()-20):m.end()+20]:
                    continue  # this is template that uses mediaName already
                if media_name in ("Jellyfin",):
                    # The fallback default in mediaName resolver is
                    # legitimate ("|| 'jellyfin'").
                    if cleaned[max(0, m.start()-15):m.start()].endswith("|| '"):
                        continue
                    if cleaned[max(0, m.start()-15):m.start()].endswith("|| \""):
                        continue
                bad.append(snippet[:80])
        self.assertFalse(
            bad,
            "Wizard text hardcodes a media-server name as a literal "
            "string:\n  - " + "\n  - ".join(bad)
            + "\n\nUse the ``mediaName`` variable (resolved from "
              "_techBindings.media_server). Hardcoded names produce "
              "wrong copy on stacks that deployed a different "
              "media server.",
        )


if __name__ == "__main__":
    unittest.main()
