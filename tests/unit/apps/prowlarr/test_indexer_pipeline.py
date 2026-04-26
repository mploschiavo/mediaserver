"""Ratchets for v1.0.110: stop the indexer-churn cycle that left
qBittorrent with nothing to download on a fresh install.

Three coordinated fixes; each test pins one of them.

  A. **Reputation quarantine doesn't propagate during initial
     discovery.** Without this, every fresh bootstrap probed ~629
     candidates, ~556 failed → quarantined → disabled in Prowlarr
     → ApplicationIndexerSync deletes them from each *arr. Each
     subsequent reconcile re-adds with a NEW *arr indexer id;
     *arr's RSS-cached releases reference the OLD ids; grabs fail
     with "IndexerDefinition with ID N does not exist". Symptom:
     SABnzbd-fed usenet downloads work, qBit-fed torrents fail
     mysteriously.

  B. **Per-app indexer tagging via Prowlarr's tag system.** Many
     anime/TV indexers claim movie capability in their schema but
     probe empty for movies → Radarr (which tests on add) rejects
     all of them with HTTP 400 → Radarr stays at 0 indexers. The
     new pipeline step probes each (indexer, app) pair, tags
     indexers with ``sync-sonarr``/``sync-radarr``/etc. based on
     real result counts, and configures each Prowlarr application
     to filter by its sync tag. Prowlarr's normal sync then only
     pushes known-good indexers per app.

  C. **applications use ``addOnly``, not ``fullSync``.** With
     fullSync, a transient disable in Prowlarr (e.g. from
     reputation quarantine) propagates as a delete in *arr. With
     addOnly, indexers added to *arr stay until the operator
     removes them via the *arr UI — no deletion churn.
"""

from __future__ import annotations

import sys
import unittest
import unittest.mock as _mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))


class FixA_NoQuarantineDuringInitialDiscovery(unittest.TestCase):

    def test_quarantine_propagates_skipped_when_state_fresh(self) -> None:
        path = ROOT / "src/media_stack/services/apps/prowlarr/reputation_ops.py"
        text = path.read_text(encoding="utf-8")
        self.assertIn("quarantine_propagates", text,
                      "FIX A regressed: reputation quarantine no "
                      "longer gates on initial-discovery state.")
        self.assertIn("is_initial_discovery", text)
        # The disable-in-Prowlarr branch must check the gate.
        self.assertRegex(
            text,
            r"if not quarantine_propagates:",
            "Quarantine path no longer skips the Prowlarr-disable "
            "step on initial discovery.",
        )

    def test_config_hook_to_force_quarantine_during_discovery(self) -> None:
        """Operators who want the old behavior can opt back in
        via ``reputation.quarantine_during_discovery: true``."""
        path = ROOT / "src/media_stack/services/apps/prowlarr/reputation_ops.py"
        text = path.read_text(encoding="utf-8")
        self.assertIn(
            'reputation_cfg.get("quarantine_during_discovery", False)',
            text,
            "Operator escape hatch removed.",
        )


class FixB_PerAppIndexerTagging(unittest.TestCase):

    def setUp(self) -> None:
        from media_stack.services.apps.prowlarr import indexer_app_match
        self.mod = indexer_app_match

    def test_app_categories_match_newznab_standard(self) -> None:
        """Sonarr=TV (5xxx), Radarr=Movies (2xxx), Lidarr=Audio
        (3xxx), Readarr=Books (7xxx). Standard Newznab numbering."""
        cats = self.mod.APP_CATEGORIES
        self.assertTrue(all(2000 <= c < 3000 for c in cats["radarr"]))
        self.assertTrue(all(5000 <= c < 6000 for c in cats["sonarr"]))
        self.assertTrue(all(3000 <= c < 4000 for c in cats["lidarr"]))
        self.assertTrue(all(c >= 7000 for c in cats["readarr"]))

    def test_one_tag_per_app(self) -> None:
        self.assertEqual(
            set(self.mod.APP_TAGS.keys()),
            {"sonarr", "radarr", "lidarr", "readarr"},
        )

    def test_pipeline_creates_tags_assigns_per_indexer_and_per_app(self) -> None:
        """End-to-end: given a Prowlarr stub, the pipeline:
          - creates the four sync-* tags
          - probes each indexer per-app
          - PUTs the indexer with its matched tag(s)
          - PUTs each application with its sync-tag filter."""
        # Prowlarr state: 1 anime indexer (TV+anime sub-cat only),
        # 1 movie indexer (Movies cats), no existing tags.
        anime_indexer = {
            "id": 10, "name": "Bangumi", "implementation": "Cardigann",
            "tags": [],
            "capabilities": {"categories": [
                {"id": 5070, "subCategories": []},  # Anime
            ]},
        }
        movie_indexer = {
            "id": 20, "name": "MovieSite", "implementation": "Cardigann",
            "tags": [],
            "capabilities": {"categories": [
                {"id": 2000, "subCategories": [{"id": 2010}, {"id": 2020}]},
            ]},
        }
        radarr_app = {
            "id": 100, "name": "Radarr", "implementation": "Radarr",
            "tags": [],
        }
        sonarr_app = {
            "id": 101, "name": "Sonarr", "implementation": "Sonarr",
            "tags": [],
        }

        responses = {
            ("GET", "/api/v1/tag"): (200, [], b""),
            ("POST", "/api/v1/tag"): None,  # see counter below
            ("GET", "/api/v1/indexer"): (200, [anime_indexer, movie_indexer], b""),
            ("GET", "/api/v1/applications"): (200, [radarr_app, sonarr_app], b""),
        }
        # Track tag creation via a counter so each POST gets a new id.
        next_tag_id = [1]
        # Track per-call mutations.
        put_calls = []

        def http_request(base, path, *, api_key="", method="GET",
                         payload=None, timeout=30):
            if method == "POST" and path == "/api/v1/tag":
                tid = next_tag_id[0]; next_tag_id[0] += 1
                return 201, {"id": tid, "label": payload["label"]}, b""
            if path.startswith("/api/v1/search"):
                # Indexer 10 (Bangumi/anime) only returns results
                # for sonarr (TV cats). Indexer 20 (movie) only for
                # radarr. Both return empty for lidarr/readarr.
                if "indexerIds=10" in path:
                    cats = path.split("categories=")[1].split("&")[0]
                    cat_ids = [int(c) for c in cats.split(",") if c]
                    if any(5000 <= c < 6000 for c in cat_ids):
                        return 200, [{"title": "x"}], b""
                    return 200, [], b""
                if "indexerIds=20" in path:
                    cats = path.split("categories=")[1].split("&")[0]
                    cat_ids = [int(c) for c in cats.split(",") if c]
                    if any(2000 <= c < 3000 for c in cat_ids):
                        return 200, [{"title": "y"}], b""
                    return 200, [], b""
            if method == "PUT":
                put_calls.append((path, payload))
                return 202, {}, b""
            return responses.get((method, path), (404, None, b""))

        captured_logs: list[str] = []
        result = self.mod.apply_indexer_app_tags(
            prowlarr_url="http://prowlarr:9696",
            prowlarr_api_key="K",
            http_request=http_request,
            log=captured_logs.append,
            cache_path=Path("/tmp/test-iam-cache.json"),
        )
        # Cleanup
        Path("/tmp/test-iam-cache.json").unlink(missing_ok=True)

        # All four tags created.
        self.assertEqual(set(result["tags"].keys()),
                         {"sonarr", "radarr", "lidarr", "readarr"})
        # Bangumi → sync-sonarr only; MovieSite → sync-radarr only.
        bang_tag = result["tags"]["sonarr"]
        movie_tag = result["tags"]["radarr"]
        # Find the indexer PUTs.
        idx_puts = [p for p in put_calls if p[0].startswith("/api/v1/indexer/")]
        self.assertEqual(len(idx_puts), 2)
        for path, body in idx_puts:
            if path.endswith("/10"):
                self.assertEqual(body["tags"], [bang_tag])
            elif path.endswith("/20"):
                self.assertEqual(body["tags"], [movie_tag])
        # Find the app PUTs.
        app_puts = [p for p in put_calls if p[0].startswith("/api/v1/applications/")]
        self.assertEqual(len(app_puts), 2)
        for path, body in app_puts:
            if path.endswith("/100"):  # Radarr
                self.assertEqual(body["tags"], [movie_tag])
            elif path.endswith("/101"):  # Sonarr
                self.assertEqual(body["tags"], [bang_tag])

    def test_capability_prefilter_avoids_pointless_probes(self) -> None:
        """If an indexer's schema doesn't claim ANY of an app's
        category ids, skip the probe — saves a network round-trip
        per (indexer, app) pair where it's structurally impossible."""
        anime_only = {
            "id": 10, "name": "AnimeOnly", "implementation": "X",
            "tags": [],
            "capabilities": {"categories": [
                {"id": 5070, "subCategories": []},  # only anime
            ]},
        }
        probes_called = []
        def http_request(base, path, *, api_key="", method="GET",
                         payload=None, timeout=30):
            if path.startswith("/api/v1/tag") and method == "GET":
                return 200, [], b""
            if path == "/api/v1/tag" and method == "POST":
                return 201, {"id": 1, "label": payload["label"]}, b""
            if path.startswith("/api/v1/search"):
                probes_called.append(path)
                return 200, [{"title": "x"}], b""
            if path == "/api/v1/indexer" and method == "GET":
                return 200, [anime_only], b""
            if path == "/api/v1/applications" and method == "GET":
                return 200, [], b""
            if method == "PUT":
                return 202, {}, b""
            return 404, None, b""

        self.mod.apply_indexer_app_tags(
            prowlarr_url="http://prowlarr:9696",
            prowlarr_api_key="K",
            http_request=http_request,
            log=lambda _m: None,
            cache_path=Path("/tmp/test-iam-cache2.json"),
        )
        Path("/tmp/test-iam-cache2.json").unlink(missing_ok=True)
        # Capability says only 5070 → only sonarr's category set
        # (5xxx) overlaps. Should probe ONLY sonarr.
        self.assertEqual(len(probes_called), 1,
                         "Capability prefilter not applied — would "
                         "probe all 4 apps when only 1 overlaps.")
        self.assertIn("indexerIds=10", probes_called[0])

    def test_cache_skips_reprobe_within_ttl(self) -> None:
        """On reconcile, indexers already classified within the TTL
        skip the probe entirely."""
        from pathlib import Path as _P
        import json, time
        cache_file = _P("/tmp/test-iam-cache3.json")
        cache_file.write_text(json.dumps({
            "version": 1,
            "indexers": {
                "10:X:Cached": {
                    "id": 10, "implementation": "X", "name": "Cached",
                    "apps": ["sonarr"],
                    "probed_at_epoch": int(time.time()),
                },
            },
        }))
        cached_indexer = {
            "id": 10, "name": "Cached", "implementation": "X",
            "tags": [],
            "capabilities": {"categories": [{"id": 5070, "subCategories": []}]},
        }
        probe_count = 0
        def http_request(base, path, *, api_key="", method="GET",
                         payload=None, timeout=30):
            nonlocal probe_count
            if path.startswith("/api/v1/tag") and method == "GET":
                return 200, [], b""
            if path == "/api/v1/tag" and method == "POST":
                return 201, {"id": 1, "label": payload["label"]}, b""
            if path == "/api/v1/indexer" and method == "GET":
                return 200, [cached_indexer], b""
            if path == "/api/v1/applications" and method == "GET":
                return 200, [], b""
            if path.startswith("/api/v1/search"):
                probe_count += 1
                return 200, [], b""
            if method == "PUT":
                return 202, {}, b""
            return 404, None, b""
        self.mod.apply_indexer_app_tags(
            prowlarr_url="http://prowlarr:9696",
            prowlarr_api_key="K",
            http_request=http_request,
            log=lambda _m: None,
            cache_path=cache_file,
        )
        cache_file.unlink(missing_ok=True)
        self.assertEqual(probe_count, 0,
                         "Cached indexer was re-probed — TTL gate "
                         "broken.")


class FixC_AddOnlyApplicationSync(unittest.TestCase):

    def test_application_registered_with_addOnly_not_fullSync(self) -> None:
        path = ROOT / "src/media_stack/services/apps/prowlarr/application_ops.py"
        text = path.read_text(encoding="utf-8")
        self.assertIn(
            '"syncLevel": "addOnly"', text,
            "FIX C regressed: applications back to fullSync; "
            "Prowlarr will delete *arr indexers on disable, "
            "orphaning RSS-cached releases.",
        )
        self.assertNotIn(
            '"syncLevel": "fullSync"', text,
            "Stray fullSync left in source — partial regression.",
        )


class ContractRegistration(unittest.TestCase):

    def test_tag_indexers_for_apps_in_contract(self) -> None:
        text = (ROOT / "contracts/services/core.yaml").read_text(encoding="utf-8")
        self.assertIn("tag-indexers-for-apps:", text,
                      "tag-indexers-for-apps job dropped from "
                      "contract — FIX B pipeline disabled.")
        self.assertIn(
            "media_stack.services.apps.core.job_adapters:"
            "tag_indexers_for_apps", text,
        )

    def test_runs_between_discover_and_push(self) -> None:
        """Phase + priority chain: discover-indexers (30) →
        tag-indexers-for-apps (35) → push-indexers (40)."""
        import yaml
        text = (ROOT / "contracts/services/core.yaml").read_text(encoding="utf-8")
        data = yaml.safe_load(text)
        jobs = (data.get("plugin", {}) or {}).get("jobs", {})
        self.assertIn("discover-indexers", jobs)
        self.assertIn("tag-indexers-for-apps", jobs)
        self.assertIn("push-indexers", jobs)
        self.assertEqual(jobs["discover-indexers"]["priority"], 30)
        self.assertEqual(jobs["tag-indexers-for-apps"]["priority"], 35)
        self.assertEqual(jobs["push-indexers"]["priority"], 40)
