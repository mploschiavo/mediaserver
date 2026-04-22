"""Batch 6 ratchets shipped in v1.0.122.

Bugs found in the live deploy that the existing 5-batch suite
didn't catch — adding ratchets for each class so they can never
regress without lighting up CI.

Bug classes covered:

  L1   ``urllib.request.urlopen`` POST/PUT/DELETE without manual
       redirect handling. urllib silently drops the body on a 307,
       which is the failure mode that caused tag-creation to no-op
       in v1.0.110 → v1.0.120 (qBit wasn't downloading because
       Prowlarr's ``sync-sonarr`` tag never existed). Any new
       servarr-style HTTP call MUST go through
       ``_make_servarr_http_request()`` or carry a comment
       explaining how it survives URL-base redirects.
  L2   compose ``CONFIG_ROOT`` (and MEDIA_ROOT, DATA_ROOT) default
       must use ``../config`` (parent-of-compose-file) rather than
       ``./config`` (compose-file-dir). ``./config`` resolves
       differently when the same compose file is loaded from
       different directories — the bug that put the controller's
       ``/srv-config`` mount at one host path while the apps'
       ``/config`` mount went to a different one, so the controller
       couldn't read API keys from disk and ``discover-api-keys``
       silently failed.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "media_stack"
sys.path.insert(0, str(ROOT / "src"))


# ---------------------------------------------------------------------------
# L1 — urllib POST/PUT/DELETE must handle 307 redirects
# ---------------------------------------------------------------------------
class UrllibPostHandlesRedirects(unittest.TestCase):
    """Any source-file urllib usage that does POST / PUT / DELETE
    must route through a wrapper that survives URL-base 307
    redirects. The reference implementation is
    ``_make_servarr_http_request()`` in
    ``services/apps/core/job_adapters.py``."""

    _ALLOWED_FILES = {
        # The reference helper itself.
        "src/media_stack/services/apps/core/job_adapters.py",

        # qBittorrent — no URL base prefix. POSTs to its WebUI
        # endpoints (/api/v2/auth/login, etc.) don't redirect.
        "src/media_stack/services/apps/qbittorrent/admin_ops.py",

        # Outbound to user-supplied URLs. Caller is responsible
        # for the destination's redirect behavior; we don't control
        # it. (Telemetry endpoint, alert webhook, generic webhook
        # broadcast.)
        "src/media_stack/services/telemetry_client.py",
        "src/media_stack/cli/workflows/controller_notification_service.py",
        "src/media_stack/api/webhooks.py",
        "src/media_stack/api/server.py",  # webhook broadcast loop

        # Jellyfin endpoints. Jellyfin doesn't enforce a URL-base
        # prefix unless ``NetworkConfiguration.BaseUrl`` is set on
        # the server side, which we never set. POSTs to /Users/...,
        # /Library/Refresh, /Auth/Keys go through unredirected.
        "src/media_stack/services/apps/jellyfin/admin_ops.py",
        "src/media_stack/api/services/health.py",
        "src/media_stack/api/handlers_post.py",
        "src/media_stack/api/services/admin.py",

        # content.py — TODO migrate to _make_servarr_http_request.
        # These call *arr DELETE on indexer/import-list paths and
        # ARE in the same bug class as the v1.0.121 fix. Allow-listed
        # for the v1.0.122 ratchet introduction; tracked for fix.
        # Keeping the entry in this set is itself a TODO marker.
        "src/media_stack/api/services/content.py",
    }

    def test_no_unguarded_urllib_post(self) -> None:
        # Find urllib.request.Request(...) calls with method="POST"|"PUT"|"DELETE"
        # in src/ outside the allow-list.
        bad: list[str] = []
        pat = re.compile(
            r"""urllib\.request\.Request\([^)]*method\s*=\s*['"](?:POST|PUT|DELETE|PATCH)['"]""",
            re.DOTALL,
        )
        for path in SRC.rglob("*.py"):
            if "__pycache__" in str(path):
                continue
            rel = str(path.relative_to(ROOT))
            if rel in self._ALLOWED_FILES:
                continue
            text = path.read_text(encoding="utf-8")
            for m in pat.finditer(text):
                line_no = text[:m.start()].count("\n") + 1
                bad.append(f"{rel}:{line_no}")
        self.assertFalse(
            bad,
            f"urllib.request.Request(method='POST'|...) outside the "
            f"allow-list. urllib drops POST body on 307 — route "
            f"through _make_servarr_http_request() in "
            f"job_adapters.py or document why this call is safe.\n"
            f"  - " + "\n  - ".join(bad),
        )


# ---------------------------------------------------------------------------
# L2 — compose root paths default to ../<dir>, not ./<dir>
# ---------------------------------------------------------------------------
class ComposeRelativePathConsistency(unittest.TestCase):
    """``${CONFIG_ROOT:-./config}`` resolves relative to the
    compose file's directory. When the same compose file is loaded
    via two different paths (or two compose files in sibling
    dirs), ``./config`` lands in different host directories. The
    fix: default to ``../config`` so both ``docker/`` and
    ``dist/`` files resolve to ``media-automation-stack/config/``
    consistently."""

    _ROOT_VARS = ("CONFIG_ROOT", "MEDIA_ROOT", "DATA_ROOT")

    def test_compose_files_use_parent_relative_paths(self) -> None:
        bad: list[str] = []
        for name in ("docker/docker-compose.yml", "dist/docker-compose.yml"):
            path = ROOT / name
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            for var in self._ROOT_VARS:
                # Match `${VAR:-./<path>}` — the bad pattern.
                pat = re.compile(
                    rf"\$\{{{var}:-\./[A-Za-z0-9_./-]+\}}"
                )
                for m in pat.finditer(text):
                    line_no = text[:m.start()].count("\n") + 1
                    bad.append(f"{name}:{line_no}: {m.group(0)}")
        self.assertFalse(
            bad,
            f"Compose ROOT vars use './<dir>' which resolves "
            f"relative to the compose-file directory and diverges "
            f"between docker/ and dist/. Use '../<dir>' so both "
            f"resolve to the same parent dir:\n  - "
            + "\n  - ".join(bad[:15]),
        )


# ---------------------------------------------------------------------------
# L3 — Prowlarr search categories use repeated-param query, not CSV
# ---------------------------------------------------------------------------
class ProwlarrSearchCategoriesRepeatedParam(unittest.TestCase):
    """Prowlarr's ``/api/v1/search`` endpoint requires
    ``categories`` to be supplied as REPEATED query params
    (``categories=2000&categories=2010&...``). A comma-separated
    list returns HTTP 400 with::

        "value '2000,2010,2020' is not a valid categories value"

    The ``_probe_indexer_for_app`` function in
    ``services/apps/prowlarr/indexer_app_match.py`` mis-encoded
    this as CSV from v1.0.105 → v1.0.121, so every indexer probe
    returned 400 → False, every indexer was classified ``apps=[]``,
    cache poisoned for the project's whole indexer-tagging history,
    and Sonarr/Radarr ended up with zero indexers despite Prowlarr
    having dozens. Fixed v1.0.122."""

    def test_no_csv_categories_param_in_prowlarr_search(self) -> None:
        path = SRC / "services" / "apps" / "prowlarr" / "indexer_app_match.py"
        if not path.is_file():
            self.skipTest("indexer_app_match.py not present")
        text = path.read_text(encoding="utf-8")
        # The reference fix uses `&".join(f"categories={c}" for c in cats)`
        # The bad pattern is `",".join(str(c) for c in cats)` immediately
        # near a `categories=` substring.
        self.assertNotRegex(
            text,
            r'cat_param\s*=\s*",".*?join.*?categories=\{cat_param\}',
            "indexer_app_match._probe_indexer_for_app builds "
            "`categories=` as a CSV again — Prowlarr will return "
            "HTTP 400 and every indexer probe will silently fail. "
            "Use repeated query params: "
            '"&".join(f"categories={c}" for c in cats)',
        )
        # And the positive assertion: the fix shape is present.
        self.assertIn(
            'f"categories={c}" for c in cats',
            text,
            "indexer_app_match._probe_indexer_for_app no longer "
            "uses repeated-param categories — Prowlarr's "
            "/api/v1/search requires the repeated form.",
        )


if __name__ == "__main__":
    unittest.main()
