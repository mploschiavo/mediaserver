"""Tests for v1.0.104 fixes + the test patterns that catch this
class of "controller ships restrictive defaults that quietly
reject everything" bug.

The bug pattern, as seen during fresh-install testing:

  * Sonarr finds 1365 RSS releases, grabs 0 — all rejected
    by quality profile.
  * Radarr finds 74 releases for a movie, only 3 approved —
    rest rejected because TMDB metadata mislabels the movie's
    original language and the default profile says
    "language=Original" (i.e., reject anything that isn't the
    movie's original language).
  * Lidarr finds 263 releases for an artist, approves 129 — but
    several are rejected for "size > 298 MB" because the per-
    quality maxSize defaults are MP3-tuned and FLAC blows past
    them.
  * Readarr's eBook profile excludes "Unknown Text" so loosely-
    tagged book indexer results get rejected.

The unit tests below pin the fix functions; the larger pattern
question is documented in TestPatternForRestrictiveDefaults
(below) — describes the integration tests we should add but are
beyond the scope of pure unit testing.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.services.apps.servarr.arr_runtime_defaults import (
    apply_arr_runtime_defaults,
    patch_lidarr_quality_sizes,
    patch_radarr_language_any,
    patch_readarr_allow_unknown_text,
)


def _stub_log_collector() -> tuple[list[str], object]:
    captured: list[str] = []

    def _log(msg: str) -> None:
        captured.append(msg)

    return captured, _log


class FakeArrServer:
    """Minimal in-memory stub for the *arr REST surface this
    module touches. Each test seeds it with the response shapes
    *arr returns, then asserts on the PUT calls received."""

    def __init__(self, responses: dict) -> None:
        self.responses = responses
        self.put_calls: list[tuple[str, dict]] = []

    def request(self, base, path, *, api_key="", method="GET",
                payload=None, timeout=15):
        if method == "GET":
            return 200, self.responses.get(path), b""
        if method == "PUT":
            self.put_calls.append((path, payload))
            return 200, {}, b""
        return 405, None, b""


class RadarrLanguageAnyPatch(unittest.TestCase):

    def test_flips_original_to_any(self) -> None:
        srv = FakeArrServer({
            "/api/v3/qualityprofile": [
                {"id": 1, "name": "Any",
                 "language": {"id": -2, "name": "Original"}},
                {"id": 4, "name": "HD-1080p",
                 "language": {"id": -2, "name": "Original"}},
            ],
        })
        captured, log = _stub_log_collector()
        n = patch_radarr_language_any(
            radarr_url="http://radarr:7878",
            api_key="K",
            http_request=srv.request,
            log=log,
        )
        self.assertEqual(n, 2)
        # Both PUTs must include language=Any.
        for path, payload in srv.put_calls:
            self.assertIn("/api/v3/qualityprofile/", path)
            self.assertEqual(payload["language"], {"id": -1, "name": "Any"})

    def test_idempotent_when_already_any(self) -> None:
        srv = FakeArrServer({
            "/api/v3/qualityprofile": [
                {"id": 1, "name": "Any",
                 "language": {"id": -1, "name": "Any"}},
            ],
        })
        _, log = _stub_log_collector()
        self.assertEqual(0, patch_radarr_language_any(
            radarr_url="http://radarr:7878",
            api_key="K",
            http_request=srv.request,
            log=log,
        ))
        self.assertEqual(srv.put_calls, [])

    def test_skips_when_endpoint_unreachable(self) -> None:
        def boom(*a, **kw): return 503, None, "down"
        captured, log = _stub_log_collector()
        n = patch_radarr_language_any(
            radarr_url="http://radarr:7878", api_key="K",
            http_request=boom, log=log,
        )
        self.assertEqual(n, 0)
        # Surfaces a WARN — operator can see something tried.
        self.assertTrue(any("WARN" in c for c in captured))


class LidarrQualitySizePatch(unittest.TestCase):

    def test_unlimits_flac_and_mp3_320(self) -> None:
        srv = FakeArrServer({
            "/api/v1/qualitydefinition": [
                {"id": 1, "quality": {"name": "Unknown"},
                 "maxSize": 350, "preferredSize": 195},
                {"id": 25, "quality": {"name": "MP3-320"},
                 "maxSize": 350, "preferredSize": 195},
                {"id": 26, "quality": {"name": "FLAC"},
                 "maxSize": 5, "preferredSize": 1},
                {"id": 27, "quality": {"name": "FLAC 24bit"},
                 "maxSize": 8, "preferredSize": 1},
            ],
        })
        _, log = _stub_log_collector()
        n = patch_lidarr_quality_sizes(
            lidarr_url="http://lidarr:8686", api_key="K",
            http_request=srv.request, log=log,
        )
        # 3 patched (MP3-320, FLAC, FLAC 24bit). Unknown is left alone.
        self.assertEqual(n, 3)
        for path, payload in srv.put_calls:
            self.assertIsNone(payload["maxSize"], path)
            self.assertGreaterEqual(payload["preferredSize"], 500, path)

    def test_skips_qualities_not_in_unlimited_set(self) -> None:
        srv = FakeArrServer({
            "/api/v1/qualitydefinition": [
                {"id": 5, "quality": {"name": "MP3-128"},
                 "maxSize": 140, "preferredSize": 95},
            ],
        })
        _, log = _stub_log_collector()
        n = patch_lidarr_quality_sizes(
            lidarr_url="http://lidarr:8686", api_key="K",
            http_request=srv.request, log=log,
        )
        self.assertEqual(n, 0)
        self.assertEqual(srv.put_calls, [])


class ReadarrUnknownTextPatch(unittest.TestCase):

    def test_enables_unknown_text_when_disabled(self) -> None:
        srv = FakeArrServer({
            "/api/v1/qualityprofile": [
                {"id": 1, "name": "eBook", "items": [
                    {"quality": {"name": "Unknown Text"}, "allowed": False},
                    {"quality": {"name": "EPUB"}, "allowed": True},
                ]},
            ],
        })
        _, log = _stub_log_collector()
        self.assertEqual(1, patch_readarr_allow_unknown_text(
            readarr_url="http://readarr:8787", api_key="K",
            http_request=srv.request, log=log,
        ))
        self.assertEqual(len(srv.put_calls), 1)
        items = srv.put_calls[0][1]["items"]
        unknown = next(i for i in items
                       if (i.get("quality") or {}).get("name") == "Unknown Text")
        self.assertTrue(unknown["allowed"])

    def test_handles_grouped_items(self) -> None:
        srv = FakeArrServer({
            "/api/v1/qualityprofile": [
                {"id": 1, "name": "eBook", "items": [
                    {"name": "Other", "allowed": True, "items": [
                        {"quality": {"name": "Unknown Text"}, "allowed": False},
                    ]},
                ]},
            ],
        })
        _, log = _stub_log_collector()
        self.assertEqual(1, patch_readarr_allow_unknown_text(
            readarr_url="http://readarr:8787", api_key="K",
            http_request=srv.request, log=log,
        ))


class DispatcherSelectsConfiguredArrApps(unittest.TestCase):

    def test_only_runs_for_configured_apps(self) -> None:
        srv = FakeArrServer({
            "/api/v3/qualityprofile": [],
            "/api/v1/qualitydefinition": [],
            "/api/v1/qualityprofile": [],
        })
        _, log = _stub_log_collector()
        # Only Radarr + Lidarr in arr_apps, so Readarr patch must
        # be skipped even though we have a key for it.
        summary = apply_arr_runtime_defaults(
            arr_apps=[
                {"implementation": "Radarr"},
                {"implementation": "Lidarr"},
            ],
            app_keys={"radarr": "K", "lidarr": "K", "readarr": "K"},
            service_url=lambda s: f"http://{s}",
            http_request=srv.request,
            log=log,
        )
        self.assertIn("radarr", summary)
        self.assertIn("lidarr", summary)
        self.assertNotIn("readarr", summary)


class ContractRegistration(unittest.TestCase):
    """Pin the contract entry so a refactor can't silently drop
    the apply-arr-runtime-defaults job from the bootstrap DAG."""

    def test_apply_arr_runtime_defaults_in_contract(self) -> None:
        text = (ROOT / "contracts/services/core.yaml").read_text(encoding="utf-8")
        self.assertIn("apply-arr-runtime-defaults:", text)
        self.assertIn(
            "media_stack.services.apps.core.job_adapters:"
            "apply_arr_runtime_defaults", text,
        )


# ---------------------------------------------------------------------------
# Test patterns for catching "controller ships restrictive defaults
# that quietly reject everything" — documented here even though the
# implementations are integration-shaped (live container required).
# ---------------------------------------------------------------------------
class TestPatternForRestrictiveDefaults(unittest.TestCase):
    """These are the *kinds* of tests that would have caught the
    bugs we keep finding.  Each pattern below is documented; some
    are unit-testable here (this file), some need an integration
    harness (deferred to a follow-up).

    The bug shape: the controller writes a config that PARSES but
    is functionally too restrictive.  Unit tests on the writer
    can't catch this because the writer behaves correctly for the
    inputs it gets.  You need to either:

    1. Test the *output behaviour* of the *arr app post-config:
       "given the controller's defaults, would this *arr accept a
       randomly-sampled set of common torrent format strings?"
    2. Test settings drift between intent and reality: read each
       *arr's saved settings via API after configure-arr-clients
       runs, assert they match the values the controller intended.
    3. Test the full bootstrap end-to-end against a live stack
       with a synthetic indexer that returns predictable releases.

    Of the three, (2) is the cheapest to maintain — it doesn't
    need a live indexer, just a live *arr container.  Worth
    building as a Playwright-style integration suite."""

    def test_doc_only(self) -> None:
        """No assertion — this docstring IS the deliverable.  The
        ratchet is the comment block that guides future test
        development."""
        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main()
