"""Backfill tests for config.py and ops.py — profiles, routing, snapshots, mounts."""

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

import media_stack.api.services.config as config_mod  # noqa: E402
import media_stack.api.services.ops as ops_mod  # noqa: E402


class TestGetProfile(unittest.TestCase):
    def setUp(self):
        config_mod._invalidate_profile_cache()

    def tearDown(self):
        config_mod._invalidate_profile_cache()

    @patch("media_stack.api.services._resolve.resolve_profile_path", return_value=None)
    def test_not_found(self, _):
        result = config_mod.get_profile()
        self.assertIsNone(result["profile"])
        self.assertIn("error", result)

    def test_valid_profile(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("services:\n  sonarr: {}\n")
            f.flush()
            with patch("media_stack.api.services._resolve.resolve_profile_path", return_value=f.name):
                result = config_mod.get_profile()
        os.unlink(f.name)
        self.assertIsNotNone(result["profile"])
        self.assertIn("services", result["profile"])


class TestSaveProfile(unittest.TestCase):
    def test_save_success(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("old content")
            f.flush()
            with patch("media_stack.api.services._resolve.resolve_profile_path", return_value=f.name):
                result = config_mod.save_profile("new content")
        content = Path(f.name).read_text()
        os.unlink(f.name)
        self.assertEqual(result["status"], "saved")
        self.assertEqual(content, "new content")

    @patch("media_stack.api.services._resolve.resolve_profile_path", return_value=None)
    def test_save_not_found(self, _):
        result = config_mod.save_profile("content")
        self.assertIn("error", result)

    def test_save_with_reload_callback(self):
        callback = MagicMock()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("")
            f.flush()
            with patch("media_stack.api.services._resolve.resolve_profile_path", return_value=f.name):
                config_mod.save_profile("content", reload_config=callback)
        os.unlink(f.name)
        callback.assert_called_once()


class TestGetRouting(unittest.TestCase):
    @patch("media_stack.api.services._resolve.resolve_profile_path", return_value=None)
    def test_defaults_when_no_profile(self, _):
        result = config_mod.get_routing()
        self.assertEqual(result["base_domain"], "local")
        self.assertEqual(result["gateway_port"], 80)

    def test_profile_values(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            import yaml
            yaml.dump({"routing": {"base_domain": "example.com", "gateway_port": 8080}}, f)
            f.flush()
            with patch("media_stack.api.services._resolve.resolve_profile_path", return_value=f.name):
                result = config_mod.get_routing()
        os.unlink(f.name)
        self.assertEqual(result["base_domain"], "example.com")
        self.assertEqual(result["gateway_port"], 8080)


class TestGetEnvvars(unittest.TestCase):
    @patch.dict(os.environ, {"STACK_FOO": "bar", "PATH": "/usr/bin", "BOOTSTRAP_X": "y"}, clear=False)
    def test_filters_by_prefix(self):
        result = config_mod.get_envvars()
        self.assertIn("STACK_FOO", result)
        self.assertIn("BOOTSTRAP_X", result)
        self.assertNotIn("PATH", result)


class TestSetEnvvar(unittest.TestCase):
    def test_sets_and_returns(self):
        result = config_mod.set_envvar("STACK_TEST_KEY", "value123")
        self.assertEqual(result["status"], "set")
        self.assertEqual(os.environ.get("STACK_TEST_KEY"), "value123")
        del os.environ["STACK_TEST_KEY"]


class TestSnapshotDetail(unittest.TestCase):
    def test_path_traversal_dotdot(self):
        result = ops_mod.get_snapshot_detail("../../etc/passwd")
        self.assertIn("Invalid", result["error"])

    def test_path_traversal_slash(self):
        result = ops_mod.get_snapshot_detail("dir/file.json")
        self.assertIn("Invalid", result["error"])

    def test_missing_file(self):
        result = ops_mod.get_snapshot_detail("snapshot-99999999T999999.json")
        self.assertEqual(result["error"], "Snapshot not found")


class TestDiffSnapshots(unittest.TestCase):
    def test_path_traversal_rejected(self):
        result = ops_mod.diff_snapshots("../evil", "snapshot-ok.json")
        self.assertIn("Invalid", result["error"])

    def test_valid_files_compared(self):
        with tempfile.TemporaryDirectory() as td:
            snap_dir = Path(td) / ".snapshots"
            snap_dir.mkdir()
            (snap_dir / "snapshot-a.json").write_text(json.dumps({"f1": "v1"}))
            (snap_dir / "snapshot-b.json").write_text(json.dumps({"f1": "v2", "f2": "new"}))
            with patch.dict(os.environ, {"CONFIG_ROOT": td}):
                result = ops_mod.diff_snapshots("snapshot-a.json", "snapshot-b.json")
            self.assertEqual(result["total_changes"], 2)


class TestGetMountInfo(unittest.TestCase):
    @patch("subprocess.run")
    def test_parses_nfs(self, mock_run):
        # mount output format: device on mountpoint type fstype (options)
        mock_run.return_value = MagicMock(
            stdout="nas:/vol on /media type nfs4 (rw,relatime)\n",
            returncode=0,
        )
        result = ops_mod.get_mount_info()
        self.assertTrue(result["nfs_available"])

    @patch("subprocess.run")
    def test_no_mounts(self, mock_run):
        mock_run.return_value = MagicMock(stdout="", returncode=0)
        result = ops_mod.get_mount_info()
        self.assertEqual(result["mounts"], [])
        self.assertFalse(result["nfs_available"])


class TestTakeSnapshot(unittest.TestCase):
    def test_creates_snapshot_file(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(os.environ, {"CONFIG_ROOT": td}):
                with patch("media_stack.api.services.registry.SERVICES", []):
                    result = ops_mod.take_snapshot()
        self.assertEqual(result["status"], "created")
        self.assertIn("snapshot-", result["file"])


if __name__ == "__main__":
    unittest.main()
