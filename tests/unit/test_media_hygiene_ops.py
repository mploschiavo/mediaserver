"""Unit tests for media hygiene operations modules.

Covers: filesystem.py, duplicate_prune.py, ipfilter.py, queue_guardrails.py
"""

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.services.media_hygiene_ops.duplicate_prune import run_qbit_duplicate_prune
from media_stack.services.media_hygiene_ops.filesystem import (
    run_filesystem_hygiene,
    walk_existing_files,
)
from media_stack.services.media_hygiene_ops.ipfilter import run_qbit_ipfilter_refresh
from media_stack.services.media_hygiene_ops.queue_guardrails import run_qbit_queue_guardrails


def _make_ops(**overrides):
    """Build a mock ops object with sensible defaults."""
    ops = MagicMock()
    ops.log = MagicMock()
    ops.bool_cfg = lambda cfg, key, default: bool(cfg.get(key, default))
    ops.coerce_list = lambda v: v if isinstance(v, list) else []
    ops.to_int = lambda v, default=None: int(v) if v is not None else default
    ops.to_float = lambda v, default=None: float(v) if v is not None else default
    ops.normalize_token = lambda v: str(v).strip().lower()
    ops.normalize_url = lambda v: str(v).rstrip("/")
    ops.qbit_login = MagicMock(return_value="opener")
    ops.qbit_list_completed_torrents = MagicMock(return_value=[])
    ops.qbit_list_torrents = MagicMock(return_value=[])
    ops.qbit_delete_torrents = MagicMock()
    ops.qbit_set_preferences = MagicMock()
    for k, v in overrides.items():
        setattr(ops, k, v)
    return ops


# ---------------------------------------------------------------------------
# filesystem.py tests
# ---------------------------------------------------------------------------


class TestWalkExistingFiles(unittest.TestCase):
    def test_walk_yields_files_from_existing_dirs(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "a.txt").write_text("a")
            sub = Path(d) / "sub"
            sub.mkdir()
            (sub / "b.txt").write_text("b")
            result = list(walk_existing_files([Path(d)]))
            names = sorted(p.name for p in result)
            self.assertEqual(names, ["a.txt", "b.txt"])

    def test_walk_skips_nonexistent_roots(self):
        result = list(walk_existing_files([Path("/nonexistent_dir_xyz_12345")]))
        self.assertEqual(result, [])


class TestFilesystemHygiene(unittest.TestCase):
    def test_disabled_returns_zero_summary(self):
        ops = _make_ops()
        result = run_filesystem_hygiene(ops, {"filesystem": {"enabled": False}})
        self.assertEqual(result["removed_temp"], 0)
        self.assertEqual(result["removed_zero"], 0)
        self.assertEqual(result["removed_dupes"], 0)
        self.assertEqual(result["removed_empty_dirs"], 0)

    def test_removes_zero_byte_files(self):
        ops = _make_ops()
        with tempfile.TemporaryDirectory() as d:
            zero_file = Path(d) / "empty.dat"
            zero_file.write_bytes(b"")
            # Set mtime far in the past to meet age threshold
            old_time = time.time() - 200_000
            os.utime(zero_file, (old_time, old_time))
            result = run_filesystem_hygiene(ops, {
                "filesystem": {
                    "enabled": True,
                    "roots": [d],
                    "remove_zero_byte_files": True,
                    "min_file_age_hours": 24.0,
                    "remove_empty_dirs": False,
                },
            })
            self.assertGreaterEqual(result["removed_zero"], 1)
            self.assertFalse(zero_file.exists())

    def test_removes_temp_extension_files(self):
        ops = _make_ops()
        with tempfile.TemporaryDirectory() as d:
            tmp_file = Path(d) / "download.part"
            tmp_file.write_bytes(b"partial data")
            old_time = time.time() - 200_000
            os.utime(tmp_file, (old_time, old_time))
            result = run_filesystem_hygiene(ops, {
                "filesystem": {
                    "enabled": True,
                    "roots": [d],
                    "remove_zero_byte_files": False,
                    "min_file_age_hours": 24.0,
                    "remove_empty_dirs": False,
                },
            })
            self.assertGreaterEqual(result["removed_temp"], 1)
            self.assertFalse(tmp_file.exists())

    def test_does_not_remove_young_files(self):
        ops = _make_ops()
        with tempfile.TemporaryDirectory() as d:
            recent_file = Path(d) / "recent.part"
            recent_file.write_bytes(b"recent data")
            # Leave mtime as now (too young)
            result = run_filesystem_hygiene(ops, {
                "filesystem": {
                    "enabled": True,
                    "roots": [d],
                    "min_file_age_hours": 24.0,
                    "remove_empty_dirs": False,
                },
            })
            self.assertEqual(result["removed_temp"], 0)
            self.assertTrue(recent_file.exists())

    def test_removes_empty_dirs_but_preserves_configured(self):
        ops = _make_ops()
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "root"
            keep = root / "keep_me"
            drop = root / "drop_me"
            keep.mkdir(parents=True)
            drop.mkdir(parents=True)
            result = run_filesystem_hygiene(ops, {
                "filesystem": {
                    "enabled": True,
                    "roots": [str(root)],
                    "remove_empty_dirs": True,
                    "preserve_empty_dirs": [str(keep)],
                },
            })
            self.assertTrue(keep.exists())
            self.assertFalse(drop.exists())
            self.assertEqual(result["removed_empty_dirs"], 1)

    def test_dedupe_dry_run_does_not_delete(self):
        ops = _make_ops()
        with tempfile.TemporaryDirectory() as d:
            # Two files with same name and size but different paths
            sub1 = Path(d) / "dir1"
            sub2 = Path(d) / "dir2"
            sub1.mkdir()
            sub2.mkdir()
            content = b"x" * 2048  # small file; min_size_bytes set low below
            (sub1 / "movie.mkv").write_bytes(content)
            (sub2 / "movie.mkv").write_bytes(content)
            # Age them so they are eligible
            old_time = time.time() - 200_000
            for p in [sub1 / "movie.mkv", sub2 / "movie.mkv"]:
                os.utime(p, (old_time, old_time))
            result = run_filesystem_hygiene(ops, {
                "filesystem": {
                    "enabled": True,
                    "roots": [d],
                    "remove_zero_byte_files": False,
                    "remove_empty_dirs": False,
                    "dedupe": {
                        "enabled": True,
                        "dry_run": True,
                        "min_size_bytes": 1024,
                    },
                },
            })
            # dry_run: both files should still exist, removed_dupes stays 0
            self.assertTrue((sub1 / "movie.mkv").exists())
            self.assertTrue((sub2 / "movie.mkv").exists())
            self.assertEqual(result["removed_dupes"], 0)


# ---------------------------------------------------------------------------
# duplicate_prune.py tests
# ---------------------------------------------------------------------------


class TestDuplicatePrune(unittest.TestCase):
    def test_disabled_returns_summary(self):
        ops = _make_ops()
        result = run_qbit_duplicate_prune(
            ops,
            {"qbit_duplicate_prune": {"enabled": False}},
            {},
            "user",
            "pass",
        )
        self.assertFalse(result["enabled"])
        self.assertEqual(result["deleted"], 0)

    def test_missing_credentials_raises(self):
        ops = _make_ops()
        with self.assertRaises(RuntimeError):
            run_qbit_duplicate_prune(
                ops,
                {"qbit_duplicate_prune": {"enabled": True}},
                {},
                "",
                "",
            )

    def test_no_duplicates_returns_zero(self):
        ops = _make_ops()
        now = int(time.time())
        ops.qbit_list_completed_torrents = MagicMock(return_value=[
            {"hash": "aaa", "name": "Torrent A", "size": 1000,
             "category": "tv", "completion_on": now - 200_000, "added_on": now - 200_000},
        ])
        result = run_qbit_duplicate_prune(
            ops,
            {"qbit_duplicate_prune": {"enabled": True}},
            {"url": "http://qbit:8080"},
            "user",
            "pass",
        )
        self.assertTrue(result["enabled"])
        self.assertEqual(result["groups"], 0)
        self.assertEqual(result["deleted"], 0)

    def test_dry_run_does_not_delete(self):
        ops = _make_ops()
        now = int(time.time())
        torrents = [
            {"hash": "aaa", "name": "Same Name", "size": 5000,
             "category": "tv", "completion_on": now - 200_000, "added_on": now - 200_000},
            {"hash": "bbb", "name": "Same Name", "size": 5000,
             "category": "tv", "completion_on": now - 100_000, "added_on": now - 100_000},
        ]
        ops.qbit_list_completed_torrents = MagicMock(return_value=torrents)
        result = run_qbit_duplicate_prune(
            ops,
            {"qbit_duplicate_prune": {"enabled": True, "dry_run": True,
                                       "match_on_name_size": True, "match_on_hash": False}},
            {"url": "http://qbit:8080"},
            "user",
            "pass",
        )
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["candidates"], 1)
        ops.qbit_delete_torrents.assert_not_called()

    def test_actual_delete_calls_qbit(self):
        ops = _make_ops()
        now = int(time.time())
        torrents = [
            {"hash": "aaa", "name": "Same Name", "size": 5000,
             "category": "tv", "completion_on": now - 200_000, "added_on": now - 200_000},
            {"hash": "bbb", "name": "Same Name", "size": 5000,
             "category": "tv", "completion_on": now - 100_000, "added_on": now - 100_000},
        ]
        ops.qbit_list_completed_torrents = MagicMock(return_value=torrents)
        result = run_qbit_duplicate_prune(
            ops,
            {"qbit_duplicate_prune": {"enabled": True, "dry_run": False,
                                       "match_on_name_size": True, "match_on_hash": False}},
            {"url": "http://qbit:8080"},
            "user",
            "pass",
        )
        self.assertEqual(result["deleted"], 1)
        ops.qbit_delete_torrents.assert_called_once()

    def test_max_delete_per_run_limits_deletions(self):
        ops = _make_ops()
        now = int(time.time())
        # Create 5 duplicates of the same torrent by name+size
        torrents = [
            {"hash": f"h{i}", "name": "Dup", "size": 999,
             "category": "tv", "completion_on": now - (200_000 + i * 1000),
             "added_on": now - (200_000 + i * 1000)}
            for i in range(5)
        ]
        ops.qbit_list_completed_torrents = MagicMock(return_value=torrents)
        result = run_qbit_duplicate_prune(
            ops,
            {"qbit_duplicate_prune": {
                "enabled": True, "dry_run": False,
                "max_delete_per_run": 2,
                "match_on_name_size": True, "match_on_hash": False,
            }},
            {"url": "http://qbit:8080"},
            "user",
            "pass",
        )
        # At most 2 should be deleted
        self.assertLessEqual(result["deleted"], 2)

    def test_keep_newest_strategy(self):
        ops = _make_ops()
        now = int(time.time())
        deleted_hashes = []

        def capture_delete(_opener, _url, hashes, delete_files=False):
            deleted_hashes.extend(hashes)

        ops.qbit_delete_torrents = capture_delete
        torrents = [
            {"hash": "old", "name": "Movie", "size": 1000,
             "category": "movies", "completion_on": now - 300_000, "added_on": now - 300_000},
            {"hash": "new", "name": "Movie", "size": 1000,
             "category": "movies", "completion_on": now - 100_000, "added_on": now - 100_000},
        ]
        ops.qbit_list_completed_torrents = MagicMock(return_value=torrents)
        run_qbit_duplicate_prune(
            ops,
            {"qbit_duplicate_prune": {
                "enabled": True, "dry_run": False, "keep": "newest",
                "match_on_name_size": True, "match_on_hash": False,
            }},
            {"url": "http://qbit:8080"},
            "user",
            "pass",
        )
        # "keep newest" should delete the older one
        self.assertIn("old", deleted_hashes)
        self.assertNotIn("new", deleted_hashes)


# ---------------------------------------------------------------------------
# ipfilter.py tests
# ---------------------------------------------------------------------------


class TestIpfilterRefresh(unittest.TestCase):
    def test_disabled_returns_summary(self):
        ops = _make_ops()
        result = run_qbit_ipfilter_refresh(
            ops,
            {"qbit_ipfilter": {"enabled": False}},
            {},
            "user",
            "pass",
        )
        self.assertFalse(result["enabled"])
        self.assertFalse(result["downloaded"])

    def test_missing_credentials_raises(self):
        ops = _make_ops()
        with self.assertRaises(RuntimeError):
            run_qbit_ipfilter_refresh(
                ops,
                {"qbit_ipfilter": {"enabled": True}},
                {},
                "",
                "",
            )

    @patch("media_stack.services.media_hygiene_ops.ipfilter.request.urlopen")
    def test_successful_download_writes_file(self, mock_urlopen):
        ops = _make_ops()
        fake_data = b"x" * 2048
        mock_resp = MagicMock()
        mock_resp.read.return_value = fake_data
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        with tempfile.TemporaryDirectory() as d:
            target = os.path.join(d, "ipfilter.dat")
            state = os.path.join(d, ".ipfilter-state.json")
            result = run_qbit_ipfilter_refresh(
                ops,
                {"qbit_ipfilter": {
                    "enabled": True,
                    "target_path": target,
                    "state_path": state,
                    "min_refresh_interval_hours": 0,
                }},
                {"url": "http://qbit:8080"},
                "user",
                "pass",
            )
            self.assertTrue(result["downloaded"])
            self.assertTrue(result["applied"])
            self.assertEqual(result["bytes"], 2048)
            self.assertTrue(Path(target).exists())
            ops.qbit_set_preferences.assert_called_once()

    @patch("media_stack.services.media_hygiene_ops.ipfilter.request.urlopen")
    def test_skips_when_refresh_interval_not_elapsed(self, mock_urlopen):
        ops = _make_ops()
        with tempfile.TemporaryDirectory() as d:
            target = os.path.join(d, "ipfilter.dat")
            state_path = os.path.join(d, ".state.json")
            # Write state indicating recent success
            state_data = {"last_success_epoch": int(time.time()) - 60, "source_url": "http://x"}
            Path(state_path).write_text(json.dumps(state_data))
            result = run_qbit_ipfilter_refresh(
                ops,
                {"qbit_ipfilter": {
                    "enabled": True,
                    "target_path": target,
                    "state_path": state_path,
                    "min_refresh_interval_hours": 24.0,
                }},
                {"url": "http://qbit:8080"},
                "user",
                "pass",
            )
            self.assertFalse(result["downloaded"])
            self.assertEqual(result["skipped_reason"], "min_refresh_interval")
            mock_urlopen.assert_not_called()

    @patch("media_stack.services.media_hygiene_ops.ipfilter.request.urlopen")
    def test_download_failure_uses_cached_if_present(self, mock_urlopen):
        ops = _make_ops()
        mock_urlopen.side_effect = Exception("network error")

        with tempfile.TemporaryDirectory() as d:
            target = os.path.join(d, "ipfilter.dat")
            state_path = os.path.join(d, ".state.json")
            # Pre-create a cached filter file
            Path(target).write_bytes(b"cached-filter-data")
            result = run_qbit_ipfilter_refresh(
                ops,
                {"qbit_ipfilter": {
                    "enabled": True,
                    "target_path": target,
                    "state_path": state_path,
                    "min_refresh_interval_hours": 0,
                    "apply_existing_on_download_failure": True,
                }},
                {"url": "http://qbit:8080"},
                "user",
                "pass",
            )
            self.assertFalse(result["downloaded"])
            self.assertEqual(result["skipped_reason"], "source_unavailable_using_cached_filter")
            # Should still apply the preference to qbit
            self.assertTrue(result["applied"])

    @patch("media_stack.services.media_hygiene_ops.ipfilter.request.urlopen")
    def test_download_failure_required_raises(self, mock_urlopen):
        ops = _make_ops()
        mock_urlopen.side_effect = Exception("network error")

        with tempfile.TemporaryDirectory() as d:
            target = os.path.join(d, "ipfilter.dat")
            state_path = os.path.join(d, ".state.json")
            with self.assertRaises(RuntimeError):
                run_qbit_ipfilter_refresh(
                    ops,
                    {"qbit_ipfilter": {
                        "enabled": True,
                        "required": True,
                        "target_path": target,
                        "state_path": state_path,
                        "min_refresh_interval_hours": 0,
                    }},
                    {"url": "http://qbit:8080"},
                    "user",
                    "pass",
                )


# ---------------------------------------------------------------------------
# queue_guardrails.py tests
# ---------------------------------------------------------------------------


class TestQueueGuardrails(unittest.TestCase):
    def test_disabled_returns_summary(self):
        ops = _make_ops()
        result = run_qbit_queue_guardrails(
            ops,
            {"queue_guardrails": {"enabled": False}},
            "user",
            "pass",
        )
        self.assertFalse(result["enabled"])
        self.assertEqual(result["over_limit_deleted"], 0)

    def test_missing_credentials_raises(self):
        ops = _make_ops()
        with self.assertRaises(RuntimeError):
            run_qbit_queue_guardrails(
                ops,
                {"queue_guardrails": {"enabled": True}},
                "",
                "",
            )

    def test_over_limit_prune_deletes_excess(self):
        ops = _make_ops()
        now = int(time.time())
        torrents = [
            {"hash": f"h{i}", "name": f"Torrent {i}", "size": 1000,
             "category": "tv", "state": "stalledDL", "progress": 0.0,
             "added_on": now - 10000 - i * 100, "completion_on": 0,
             "last_activity": now - 5000, "dlspeed": 0, "eta": -1}
            for i in range(10)
        ]
        ops.qbit_list_torrents = MagicMock(return_value=torrents)
        result = run_qbit_queue_guardrails(
            ops,
            {
                "queue_guardrails": {
                    "enabled": True,
                    "dry_run": False,
                    "prune_when_over_limit": True,
                    "max_queued_by_category": {"tv": 5},
                },
                "url": "http://qbit:8080",
            },
            "user",
            "pass",
        )
        self.assertGreater(result["over_limit_candidates"], 0)
        ops.qbit_delete_torrents.assert_called()

    def test_dry_run_does_not_delete(self):
        ops = _make_ops()
        now = int(time.time())
        torrents = [
            {"hash": f"h{i}", "name": f"Torrent {i}", "size": 1000,
             "category": "tv", "state": "stalledDL", "progress": 0.0,
             "added_on": now - 10000 - i * 100, "completion_on": 0,
             "last_activity": now - 5000, "dlspeed": 0, "eta": -1}
            for i in range(10)
        ]
        ops.qbit_list_torrents = MagicMock(return_value=torrents)
        result = run_qbit_queue_guardrails(
            ops,
            {
                "queue_guardrails": {
                    "enabled": True,
                    "dry_run": True,
                    "prune_when_over_limit": True,
                    "max_queued_by_category": {"tv": 5},
                },
                "url": "http://qbit:8080",
            },
            "user",
            "pass",
        )
        self.assertTrue(result["dry_run"])
        ops.qbit_delete_torrents.assert_not_called()

    def test_stale_prune_detects_old_stalled_torrents(self):
        ops = _make_ops()
        now = int(time.time())
        # One torrent that is very old and stalled with no progress
        torrents = [
            {"hash": "stale1", "name": "Old Stalled", "size": 5000,
             "category": "movies", "state": "stalledDL", "progress": 0.01,
             "added_on": now - 800_000, "completion_on": 0,
             "last_activity": now - 200_000, "dlspeed": 0, "eta": -1},
        ]
        ops.qbit_list_torrents = MagicMock(return_value=torrents)
        result = run_qbit_queue_guardrails(
            ops,
            {
                "queue_guardrails": {
                    "enabled": True,
                    "dry_run": False,
                    "stale_prune": {
                        "enabled": True,
                        "max_age_hours": 168.0,
                        "max_stalled_hours": 24.0,
                    },
                },
                "url": "http://qbit:8080",
            },
            "user",
            "pass",
        )
        self.assertGreater(result["stale_candidates"], 0)

    def test_over_budget_prune_by_size(self):
        ops = _make_ops()
        now = int(time.time())
        gib = 1024 ** 3
        # Create torrents in "movies" category totaling ~10 GiB
        torrents = [
            {"hash": f"b{i}", "name": f"Big Movie {i}", "size": 2 * gib,
             "category": "movies", "state": "stalledDL", "progress": 0.5,
             "added_on": now - 50000 - i * 1000, "completion_on": 0,
             "last_activity": now - 1000, "dlspeed": 100, "eta": 100}
            for i in range(5)
        ]
        ops.qbit_list_torrents = MagicMock(return_value=torrents)
        result = run_qbit_queue_guardrails(
            ops,
            {
                "queue_guardrails": {
                    "enabled": True,
                    "dry_run": False,
                    "max_total_size_gib_by_category": {"movies": 4},
                    "stale_prune": {"enabled": False},
                },
                "url": "http://qbit:8080",
            },
            "user",
            "pass",
        )
        self.assertGreater(result["over_budget_candidates"], 0)

    def test_no_torrents_returns_clean_summary(self):
        ops = _make_ops()
        ops.qbit_list_torrents = MagicMock(return_value=[])
        result = run_qbit_queue_guardrails(
            ops,
            {
                "queue_guardrails": {
                    "enabled": True,
                    "dry_run": False,
                    "stale_prune": {"enabled": False},
                },
                "url": "http://qbit:8080",
            },
            "user",
            "pass",
        )
        self.assertEqual(result["total"], 0)
        self.assertEqual(result["over_limit_deleted"], 0)
        self.assertEqual(result["stale_deleted"], 0)


if __name__ == "__main__":
    unittest.main()
