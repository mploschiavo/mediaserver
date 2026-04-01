import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import bootstrap_services.entrypoint_runtime as MODULE
import bootstrap_services.disk_guardrails_service as DISK_GUARDRAILS_SERVICE
import bootstrap_services.jellyfin_livetv_source_service as JELLYFIN_LIVETV_SOURCE_SERVICE
import bootstrap_services.media_hygiene_ops.duplicate_prune as DUPLICATE_PRUNE
import bootstrap_services.media_hygiene_ops.ipfilter as IPFILTER
import bootstrap_services.media_hygiene_ops.queue_guardrails as QUEUE_GUARDRAILS
import bootstrap_services.runtime_media_ops as MEDIA_OPS
import bootstrap_services.runtime_servarr.hygiene_ops as HYGIENE_OPS


class DiskGuardrailsTests(unittest.TestCase):
    def test_guardrails_skip_cleanup_when_usage_is_under_threshold(self):
        cfg = {
            "disk_guardrails": {
                "enabled": True,
                "monitor_path": "/srv-stack",
                "max_used_percent": 65,
                "target_used_percent": 58,
            }
        }
        qbit_cfg = {"url": "http://qbittorrent:8080"}

        with (
            mock.patch.object(
                HYGIENE_OPS, "_disk_usage_percent", return_value=(42.0, 1_000_000, 580_000)
            ),
            mock.patch.object(HYGIENE_OPS, "qbit_login") as login_mock,
        ):
            MODULE.enforce_disk_guardrails(cfg, "/srv-config", qbit_cfg, "admin", "secret")
            login_mock.assert_not_called()

    def test_guardrails_delete_old_completed_torrents_when_usage_is_high(self):
        cfg = {
            "disk_guardrails": {
                "enabled": True,
                "monitor_path": "/srv-stack",
                "max_used_percent": 65,
                "target_used_percent": 58,
                "qbit_cleanup": {
                    "enabled": True,
                    "categories": ["tv", "movies"],
                    "min_completion_age_hours": 24,
                    "min_ratio": 1.0,
                    "min_seeding_time_minutes": 720,
                    "max_delete_per_run": 10,
                    "delete_files": True,
                },
            }
        }
        qbit_cfg = {"url": "http://qbittorrent:8080"}
        now = 2_000_000
        torrents = [
            {
                "hash": "hash-1",
                "category": "movies",
                "completion_on": now - (72 * 3600),
                "ratio": 1.7,
                "seeding_time": 60 * 60 * 18,
                "size": 4_000_000_000,
            },
            {
                "hash": "hash-2",
                "category": "tv",
                "completion_on": now - (4 * 3600),
                "ratio": 2.0,
                "seeding_time": 60 * 60 * 20,
                "size": 500_000_000,
            },
            {
                "hash": "hash-3",
                "category": "other",
                "completion_on": now - (80 * 3600),
                "ratio": 3.0,
                "seeding_time": 60 * 60 * 20,
                "size": 300_000_000,
            },
        ]

        with (
            mock.patch.object(
                HYGIENE_OPS,
                "_disk_usage_percent",
                side_effect=[(75.0, 1_000_000, 250_000), (60.0, 1_000_000, 400_000)],
            ),
            mock.patch.object(HYGIENE_OPS, "qbit_login", return_value=object()),
            mock.patch.object(HYGIENE_OPS, "qbit_list_completed_torrents", return_value=torrents),
            mock.patch.object(HYGIENE_OPS, "qbit_delete_torrents") as delete_mock,
            mock.patch.object(DISK_GUARDRAILS_SERVICE.time, "time", return_value=now),
        ):
            MODULE.enforce_disk_guardrails(cfg, "/srv-config", qbit_cfg, "admin", "secret")

        delete_mock.assert_called_once()
        args, kwargs = delete_mock.call_args
        self.assertEqual(args[1], "http://qbittorrent:8080")
        self.assertEqual(args[2], ["hash-1"])
        self.assertTrue(kwargs.get("delete_files"))


class MediaHygieneQbitDuplicatePruneTests(unittest.TestCase):
    def test_qbit_duplicate_prune_is_noop_when_disabled(self):
        hygiene_cfg = {"qbit_duplicate_prune": {"enabled": False}}
        qbit_cfg = {"url": "http://qbittorrent:8080"}

        with mock.patch.object(HYGIENE_OPS, "qbit_login") as login_mock:
            summary = MODULE.run_qbit_duplicate_prune(
                hygiene_cfg,
                qbit_cfg,
                "admin",
                "secret",
            )
        login_mock.assert_not_called()
        self.assertFalse(summary.get("enabled"))
        self.assertEqual(summary.get("deleted"), 0)

    def test_qbit_duplicate_prune_removes_newer_duplicate_by_name_size(self):
        now = 3_000_000
        hygiene_cfg = {
            "qbit_duplicate_prune": {
                "enabled": True,
                "dry_run": False,
                "match_on_hash": True,
                "match_on_name_size": True,
                "include_category_in_key": True,
                "categories": ["movies", "tv"],
                "min_completion_age_hours": 24,
                "max_delete_per_run": 5,
                "keep": "oldest",
                "delete_files": False,
            }
        }
        qbit_cfg = {"url": "http://qbittorrent:8080"}
        torrents = [
            {
                "hash": "keep-old",
                "name": "The Same Movie",
                "size": 2_000_000_000,
                "category": "movies",
                "completion_on": now - (72 * 3600),
            },
            {
                "hash": "delete-new",
                "name": "The Same Movie",
                "size": 2_000_000_000,
                "category": "movies",
                "completion_on": now - (48 * 3600),
            },
            {
                "hash": "different-category",
                "name": "The Same Movie",
                "size": 2_000_000_000,
                "category": "tv",
                "completion_on": now - (96 * 3600),
            },
            {
                "hash": "too-young",
                "name": "Another Movie",
                "size": 1_000_000_000,
                "category": "movies",
                "completion_on": now - (2 * 3600),
            },
        ]

        with (
            mock.patch.object(HYGIENE_OPS, "qbit_login", return_value=object()),
            mock.patch.object(HYGIENE_OPS, "qbit_list_completed_torrents", return_value=torrents),
            mock.patch.object(HYGIENE_OPS, "qbit_delete_torrents") as delete_mock,
            mock.patch.object(DUPLICATE_PRUNE.time, "time", return_value=now),
        ):
            summary = MODULE.run_qbit_duplicate_prune(
                hygiene_cfg,
                qbit_cfg,
                "admin",
                "secret",
            )

        delete_mock.assert_called_once()
        args, kwargs = delete_mock.call_args
        self.assertEqual(args[1], "http://qbittorrent:8080")
        self.assertEqual(args[2], ["delete-new"])
        self.assertFalse(kwargs.get("delete_files"))
        self.assertEqual(summary.get("deleted"), 1)
        self.assertEqual(summary.get("candidates"), 1)


class MediaHygieneQbitIpFilterTests(unittest.TestCase):
    class _Resp:
        def __init__(self, payload: bytes):
            self._payload = payload

        def read(self):
            return self._payload

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            del exc_type, exc, tb
            return False

    def test_qbit_ipfilter_refresh_downloads_and_applies_preferences(self):
        hygiene_cfg = {
            "qbit_ipfilter": {
                "enabled": True,
                "url": "https://example.invalid/ipfilter.dat",
                "min_refresh_interval_hours": 24,
            }
        }
        qbit_cfg = {"url": "http://qbittorrent:8080"}
        ipfilter_bytes = b"1.0.0.0/8\n2.0.0.0/8\n" + (b"#" * 2048)

        with tempfile.TemporaryDirectory() as tmp:
            target_path = Path(tmp) / "ipfilter.dat"
            state_path = Path(tmp) / "ipfilter-state.json"
            hygiene_cfg["qbit_ipfilter"]["target_path"] = str(target_path)
            hygiene_cfg["qbit_ipfilter"]["state_path"] = str(state_path)
            hygiene_cfg["qbit_ipfilter"]["qbit_filter_path"] = "/data/torrents/ipfilter.dat"

            with (
                mock.patch.object(
                    IPFILTER.request,
                    "urlopen",
                    return_value=self._Resp(ipfilter_bytes),
                ),
                mock.patch.object(HYGIENE_OPS, "qbit_login", return_value=object()),
                mock.patch.object(HYGIENE_OPS, "qbit_set_preferences") as prefs_mock,
                mock.patch.object(IPFILTER.time, "time", return_value=5_000_000),
            ):
                summary = MODULE.run_qbit_ipfilter_refresh(
                    hygiene_cfg,
                    qbit_cfg,
                    "admin",
                    "secret",
                )

            self.assertTrue(summary.get("enabled"))
            self.assertTrue(summary.get("downloaded"))
            self.assertTrue(summary.get("applied"))
            self.assertEqual(target_path.read_bytes(), ipfilter_bytes)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(int(state.get("bytes", 0)), len(ipfilter_bytes))
            self.assertEqual(
                state.get("qbit_filter_path"),
                "/data/torrents/ipfilter.dat",
            )
            prefs_mock.assert_called_once()
            prefs_payload = prefs_mock.call_args.args[2]
            self.assertTrue(bool(prefs_payload.get("ip_filter_enabled")))
            self.assertEqual(
                prefs_payload.get("ip_filter_path"),
                "/data/torrents/ipfilter.dat",
            )

    def test_qbit_ipfilter_refresh_uses_cached_file_when_source_unavailable(self):
        hygiene_cfg = {
            "qbit_ipfilter": {
                "enabled": True,
                "url": "https://example.invalid/ipfilter.dat",
                "apply_existing_on_download_failure": True,
            }
        }
        qbit_cfg = {"url": "http://qbittorrent:8080"}

        with tempfile.TemporaryDirectory() as tmp:
            target_path = Path(tmp) / "ipfilter.dat"
            state_path = Path(tmp) / "ipfilter-state.json"
            target_path.write_bytes(b"cached-ipfilter-data\n" + (b"#" * 2048))
            hygiene_cfg["qbit_ipfilter"]["target_path"] = str(target_path)
            hygiene_cfg["qbit_ipfilter"]["state_path"] = str(state_path)
            hygiene_cfg["qbit_ipfilter"]["qbit_filter_path"] = "/data/torrents/ipfilter.dat"
            hygiene_cfg["qbit_ipfilter"]["min_refresh_interval_hours"] = 0

            with (
                mock.patch.object(
                    IPFILTER.request,
                    "urlopen",
                    side_effect=RuntimeError("source down"),
                ),
                mock.patch.object(HYGIENE_OPS, "qbit_login", return_value=object()),
                mock.patch.object(HYGIENE_OPS, "qbit_set_preferences") as prefs_mock,
            ):
                summary = MODULE.run_qbit_ipfilter_refresh(
                    hygiene_cfg,
                    qbit_cfg,
                    "admin",
                    "secret",
                )

            self.assertTrue(summary.get("enabled"))
            self.assertFalse(summary.get("downloaded"))
            self.assertTrue(summary.get("applied"))
            self.assertEqual(
                summary.get("skipped_reason"),
                "source_unavailable_using_cached_filter",
            )
            prefs_mock.assert_called_once()


class QbitQueueGuardrailsTests(unittest.TestCase):
    def test_qbit_queue_guardrails_noop_when_disabled(self):
        qbit_cfg = {"url": "http://qbittorrent:8080", "queue_guardrails": {"enabled": False}}
        with mock.patch.object(HYGIENE_OPS, "qbit_login") as login_mock:
            summary = MODULE.run_qbit_queue_guardrails(qbit_cfg, "admin", "secret")
        login_mock.assert_not_called()
        self.assertFalse(summary.get("enabled"))
        self.assertEqual(summary.get("over_limit_deleted"), 0)
        self.assertEqual(summary.get("stale_deleted"), 0)

    def test_qbit_queue_guardrails_prunes_over_limit_and_stale(self):
        now = 4_000_000
        qbit_cfg = {
            "url": "http://qbittorrent:8080",
            "queue_guardrails": {
                "enabled": True,
                "dry_run": False,
                "default_max_queued": 99,
                "max_queued_by_category": {"music": 1},
                "include_uncategorized": False,
                "count_states": ["queuedDL", "stalledDL", "metaDL", "downloading"],
                "prune_when_over_limit": True,
                "prune_states": ["queuedDL", "stalledDL", "metaDL"],
                "over_limit_max_delete_per_category": 10,
                "over_limit_delete_files": True,
                "stale_prune": {
                    "enabled": True,
                    "max_age_hours": 48,
                    "max_stalled_hours": 24,
                    "max_eta_seconds": 7 * 24 * 3600,
                    "min_progress": 0.98,
                    "max_download_speed_bps": 32768,
                    "states": ["downloading", "queuedDL", "stalledDL", "metaDL"],
                    "max_delete_per_run": 10,
                    "delete_files": True,
                },
            },
        }

        torrents = [
            {
                "hash": "music-keep",
                "name": "Artist One",
                "category": "music",
                "state": "queuedDL",
                "progress": 0.20,
                "added_on": now - (6 * 3600),
                "last_activity": now - 60,
                "dlspeed": 50_000,
                "eta": 3600,
            },
            {
                "hash": "music-drop",
                "name": "Artist Two",
                "category": "music",
                "state": "queuedDL",
                "progress": 0.05,
                "added_on": now - (12 * 3600),
                "last_activity": now - (2 * 3600),
                "dlspeed": 0,
                "eta": 200000,
            },
            {
                "hash": "books-stale",
                "name": "Book One",
                "category": "books",
                "state": "downloading",
                "progress": 0.10,
                "added_on": now - (10 * 24 * 3600),
                "last_activity": now - (30 * 3600),
                "dlspeed": 0,
                "eta": 20 * 24 * 3600,
            },
        ]

        with (
            mock.patch.object(HYGIENE_OPS, "qbit_login", return_value=object()),
            mock.patch.object(HYGIENE_OPS, "qbit_list_torrents", return_value=torrents),
            mock.patch.object(HYGIENE_OPS, "qbit_delete_torrents") as delete_mock,
            mock.patch.object(QUEUE_GUARDRAILS.time, "time", return_value=now),
        ):
            summary = MODULE.run_qbit_queue_guardrails(qbit_cfg, "admin", "secret")

        self.assertEqual(delete_mock.call_count, 2)
        first_args, first_kwargs = delete_mock.call_args_list[0]
        second_args, second_kwargs = delete_mock.call_args_list[1]
        self.assertEqual(first_args[2], ["music-drop"])
        self.assertTrue(first_kwargs.get("delete_files"))
        self.assertEqual(second_args[2], ["books-stale"])
        self.assertTrue(second_kwargs.get("delete_files"))
        self.assertEqual(summary.get("over_limit_deleted"), 1)
        self.assertEqual(summary.get("stale_deleted"), 1)
        self.assertEqual((summary.get("by_category") or {}).get("music", {}).get("limit"), 1)


class JellyfinLiveTvRefreshTests(unittest.TestCase):
    def test_refresh_is_requested_even_when_live_tv_entries_already_exist(self):
        cfg = {
            "jellyfin_livetv": {
                "enabled": True,
                "refresh_on_bootstrap": True,
                "cleanup_duplicates": False,
                "recreate_managed_guides": False,
                "url": "http://jellyfin:8096",
                "tuners": [
                    {
                        "type": "m3u",
                        "url": "https://iptv-org.github.io/iptv/countries/us.m3u",
                    }
                ],
                "guides": [
                    {
                        "type": "xmltv",
                        "path": "https://iptv-epg.org/files/epg-us.xml",
                    }
                ],
            }
        }

        existing_state = {
            "tuner_keys": {("m3u", "https://iptv-org.github.io/iptv/countries/us.m3u")},
            "guide_keys": {("xmltv", "https://iptv-epg.org/files/epg-us.xml")},
            "tuner_ids_by_key": {
                ("m3u", "https://iptv-org.github.io/iptv/countries/us.m3u"): "tuner-1"
            },
        }
        calls = []

        def fake_jellyfin_request(base_url, path, api_key, method="GET", payload=None, timeout=30):
            calls.append((path, method))
            if path == "/LiveTv/Info":
                return 200, {}, ""
            if path in ("/LiveTv/RefreshChannels", "/LiveTv/RefreshGuide"):
                return 204, {}, ""
            raise AssertionError(f"Unexpected Live TV API call: {path}")

        with (
            mock.patch.object(MEDIA_OPS, "wait_for_service"),
            mock.patch.object(MEDIA_OPS, "resolve_jellyfin_api_key", return_value="jellyfin-key"),
            mock.patch.object(MEDIA_OPS, "load_jellyfin_livetv_state", return_value=existing_state),
            mock.patch.object(MEDIA_OPS, "resolve_jellyfin_tuner_type_id", return_value="m3u"),
            mock.patch.object(MEDIA_OPS, "jellyfin_request", side_effect=fake_jellyfin_request),
        ):
            MODULE.ensure_jellyfin_livetv(cfg, "/srv-config", 30)

        called_paths = [item[0] for item in calls]
        self.assertIn("/LiveTv/RefreshChannels", called_paths)
        self.assertIn("/LiveTv/RefreshGuide", called_paths)
        self.assertNotIn("/LiveTv/TunerHosts", called_paths)
        self.assertNotIn("/LiveTv/ListingProviders", called_paths)

    def test_recreate_managed_guide_and_fallback_to_enable_all_tuners(self):
        cfg = {
            "jellyfin_livetv": {
                "enabled": True,
                "refresh_on_bootstrap": True,
                "cleanup_duplicates": True,
                "recreate_managed_guides": True,
                "fallback_enable_all_tuners_when_mapping_missing": True,
                "url": "http://jellyfin:8096",
                "tuners": [
                    {
                        "type": "m3u",
                        "url": "https://iptv-org.github.io/iptv/countries/us.m3u",
                    }
                ],
                "guides": [
                    {
                        "type": "xmltv",
                        "path": "https://iptv-epg.org/files/epg-us.xml",
                        "enable_all_tuners": False,
                        "enabled_tuners": [
                            "tuner-url:https://iptv-org.github.io/iptv/countries/us.m3u"
                        ],
                    }
                ],
            }
        }

        existing_state = {
            "tuner_keys": {("m3u", "https://iptv-org.github.io/iptv/countries/us.m3u")},
            "guide_keys": {("xmltv", "https://iptv-epg.org/files/epg-us.xml")},
            "tuner_ids_by_key": {},
            "tuners_by_key": {
                ("m3u", "https://iptv-org.github.io/iptv/countries/us.m3u"): [
                    {
                        "id": "tuner-keep",
                        "type": "m3u",
                        "url": "https://iptv-org.github.io/iptv/countries/us.m3u",
                    },
                    {
                        "id": "tuner-dup",
                        "type": "m3u",
                        "url": "https://iptv-org.github.io/iptv/countries/us.m3u",
                    },
                ]
            },
            "guides_by_key": {
                ("xmltv", "https://iptv-epg.org/files/epg-us.xml"): [
                    {
                        "id": "guide-old",
                        "type": "xmltv",
                        "path": "https://iptv-epg.org/files/epg-us.xml",
                        "enabled_tuners": [],
                        "enable_all_tuners": False,
                    }
                ]
            },
        }
        refreshed_state = {
            "tuner_keys": {("m3u", "https://iptv-org.github.io/iptv/countries/us.m3u")},
            "guide_keys": set(),
            "tuner_ids_by_key": {},
            "tuners_by_key": {
                ("m3u", "https://iptv-org.github.io/iptv/countries/us.m3u"): [
                    {
                        "id": "tuner-keep",
                        "type": "m3u",
                        "url": "https://iptv-org.github.io/iptv/countries/us.m3u",
                    }
                ]
            },
            "guides_by_key": {},
        }
        calls = []
        created_payloads = []

        def fake_jellyfin_request(base_url, path, api_key, method="GET", payload=None, timeout=30):
            calls.append((path, method, payload))
            if path == "/LiveTv/Info":
                return 200, {}, ""
            if path.startswith("/LiveTv/TunerHosts?id=") and method == "DELETE":
                return 204, {}, ""
            if path.startswith("/LiveTv/ListingProviders?id=") and method == "DELETE":
                return 204, {}, ""
            if path == "/LiveTv/ListingProviders" and method == "POST":
                created_payloads.append(payload)
                return 200, {"Id": "guide-new"}, ""
            if path in ("/LiveTv/RefreshChannels", "/LiveTv/RefreshGuide"):
                return 204, {}, ""
            raise AssertionError(f"Unexpected Live TV API call: {path} ({method})")

        with (
            mock.patch.object(MEDIA_OPS, "wait_for_service"),
            mock.patch.object(MEDIA_OPS, "resolve_jellyfin_api_key", return_value="jellyfin-key"),
            mock.patch.object(
                MEDIA_OPS,
                "load_jellyfin_livetv_state",
                side_effect=[existing_state, refreshed_state, refreshed_state],
            ),
            mock.patch.object(MEDIA_OPS, "resolve_jellyfin_tuner_type_id", return_value="m3u"),
            mock.patch.object(MEDIA_OPS, "jellyfin_request", side_effect=fake_jellyfin_request),
        ):
            MODULE.ensure_jellyfin_livetv(cfg, "/srv-config", 30)

        called_paths = [item[0] for item in calls]
        self.assertIn("/LiveTv/TunerHosts?id=tuner-dup", called_paths)
        self.assertIn("/LiveTv/ListingProviders", called_paths)
        self.assertTrue(created_payloads)
        created = created_payloads[0]
        self.assertTrue(created.get("EnableAllTuners"))
        self.assertNotIn("EnabledTuners", created)

    def test_refresh_is_not_requested_when_disabled_and_no_changes(self):
        cfg = {
            "jellyfin_livetv": {
                "enabled": True,
                "refresh_on_bootstrap": False,
                "url": "http://jellyfin:8096",
                "tuners": [],
                "guides": [],
            }
        }

        with (
            mock.patch.object(MEDIA_OPS, "wait_for_service") as wait_mock,
            mock.patch.object(MEDIA_OPS, "resolve_jellyfin_api_key") as api_key_mock,
            mock.patch.object(MEDIA_OPS, "jellyfin_request") as request_mock,
        ):
            MODULE.ensure_jellyfin_livetv(cfg, "/srv-config", 30)

        wait_mock.assert_not_called()
        api_key_mock.assert_not_called()
        request_mock.assert_not_called()

    def test_materialized_playlist_normalizes_ids_and_prunes_unmanaged_tuner(self):
        cfg = {
            "jellyfin_livetv": {
                "enabled": True,
                "refresh_on_bootstrap": True,
                "cleanup_duplicates": False,
                "recreate_managed_guides": False,
                "prune_unmanaged_tuners": True,
                "prune_unmanaged_guides": False,
                "url": "http://jellyfin:8096",
                "tuners": [
                    {
                        "type": "m3u",
                        "url": "https://iptv-org.github.io/iptv/countries/us.m3u",
                        "normalize_tvg_id_suffix": True,
                        "filter_to_guide_channels": True,
                        "materialized_output_path": "jellyfin/livetv-tuners/iptv-org-us-epg.m3u",
                    }
                ],
                "guides": [
                    {
                        "type": "xmltv",
                        "path": "https://iptv-epg.org/files/epg-us.xml",
                    }
                ],
            }
        }

        existing_state = {
            "tuner_keys": {
                ("m3u", "https://legacy.example.invalid/movies.m3u"),
                ("m3u", "/config/livetv-tuners/iptv-org-us-epg.m3u"),
            },
            "guide_keys": {("xmltv", "https://iptv-epg.org/files/epg-us.xml")},
            "tuner_ids_by_key": {},
            "tuners_by_key": {
                ("m3u", "https://legacy.example.invalid/movies.m3u"): [
                    {
                        "id": "legacy-tuner",
                        "type": "m3u",
                        "url": "https://legacy.example.invalid/movies.m3u",
                    }
                ],
            },
            "guides_by_key": {
                ("xmltv", "https://iptv-epg.org/files/epg-us.xml"): [
                    {
                        "id": "guide-1",
                        "type": "xmltv",
                        "path": "https://iptv-epg.org/files/epg-us.xml",
                    }
                ]
            },
        }
        refreshed_state = {
            "tuner_keys": set(),
            "guide_keys": {("xmltv", "https://iptv-epg.org/files/epg-us.xml")},
            "tuner_ids_by_key": {},
            "tuners_by_key": {},
            "guides_by_key": {
                ("xmltv", "https://iptv-epg.org/files/epg-us.xml"): [
                    {
                        "id": "guide-1",
                        "type": "xmltv",
                        "path": "https://iptv-epg.org/files/epg-us.xml",
                    }
                ]
            },
        }
        calls = []
        created_tuner_payloads = []

        def fake_jellyfin_request(base_url, path, api_key, method="GET", payload=None, timeout=30):
            del base_url, api_key, timeout
            calls.append((path, method, payload))
            if path == "/LiveTv/Info":
                return 200, {}, ""
            if path.startswith("/LiveTv/TunerHosts?id=") and method == "DELETE":
                return 204, {}, ""
            if path == "/LiveTv/TunerHosts" and method == "POST":
                created_tuner_payloads.append(payload or {})
                return 200, {"Id": "new-managed-tuner"}, ""
            if path == "/LiveTv/ListingProviders" and method == "POST":
                return 200, {}, ""
            if path in ("/LiveTv/RefreshChannels", "/LiveTv/RefreshGuide"):
                return 204, {}, ""
            raise AssertionError(f"Unexpected Live TV API call: {path} ({method})")

        class _Resp:
            def __init__(self, payload: bytes):
                self._payload = payload

            def read(self):
                return self._payload

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                del exc_type, exc, tb
                return False

        m3u_payload = (
            "#EXTM3U\n"
            '#EXTINF:-1 tvg-id="ABCWBMA.us@SD",ABC Example\n'
            "https://example.invalid/abc.m3u8\n"
            '#EXTINF:-1 tvg-id="NoGuide.us@SD",No Guide Example\n'
            "https://example.invalid/noguide.m3u8\n"
        ).encode("utf-8")
        epg_payload = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            "<tv>\n"
            '  <channel id="ABCWBMA.us"><display-name>ABC</display-name></channel>\n'
            "</tv>\n"
        ).encode("utf-8")

        def fake_urlopen(url, timeout=60):
            del timeout
            if "countries/us.m3u" in str(url):
                return _Resp(m3u_payload)
            if "epg-us.xml" in str(url):
                return _Resp(epg_payload)
            raise AssertionError(f"Unexpected URL fetch: {url}")

        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(MEDIA_OPS, "wait_for_service"),
            mock.patch.object(MEDIA_OPS, "resolve_jellyfin_api_key", return_value="jellyfin-key"),
            mock.patch.object(
                MEDIA_OPS,
                "load_jellyfin_livetv_state",
                side_effect=[existing_state, refreshed_state, refreshed_state],
            ),
            mock.patch.object(MEDIA_OPS, "resolve_jellyfin_tuner_type_id", return_value="m3u"),
            mock.patch.object(MEDIA_OPS, "jellyfin_request", side_effect=fake_jellyfin_request),
            mock.patch.object(
                JELLYFIN_LIVETV_SOURCE_SERVICE.request, "urlopen", side_effect=fake_urlopen
            ),
        ):
            MODULE.ensure_jellyfin_livetv(cfg, tmp, 30)

            rendered = (Path(tmp) / "jellyfin" / "livetv-tuners" / "iptv-org-us-epg.m3u").read_text(
                encoding="utf-8"
            )

        called_paths = [item[0] for item in calls]
        self.assertIn("/LiveTv/TunerHosts?id=legacy-tuner", called_paths)
        self.assertTrue(created_tuner_payloads)
        created = created_tuner_payloads[0]
        self.assertEqual(
            created.get("Url"),
            "/config/livetv-tuners/iptv-org-us-epg.m3u",
        )
        self.assertIn('tvg-id="ABCWBMA.us"', rendered)
        self.assertNotIn("@SD", rendered)
        self.assertNotIn("No Guide Example", rendered)


if __name__ == "__main__":
    unittest.main()
