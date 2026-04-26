"""Unit tests for media_stack.api.services.ops — namespace/container info, snapshots, mounts."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

# Install a mock docker module into sys.modules BEFORE importing ops,
# so that `import docker` inside the functions resolves to our mock.
_mock_docker = mock.MagicMock()
sys.modules.setdefault("docker", _mock_docker)

from media_stack.api.services.ops import (  # noqa: E402
    get_namespaces,
    check_image_updates,
    get_mount_info,
    take_snapshot,
    get_config_snapshots,
    get_snapshot_detail,
    diff_snapshots,
    get_service_logs,
)


def _reset_docker_mock():
    """Reset the shared docker mock between tests."""
    _mock_docker.reset_mock()


# ---------------------------------------------------------------------------
# get_namespaces() — Compose path (no K8S_NAMESPACE)
# ---------------------------------------------------------------------------

class TestGetNamespacesCompose(unittest.TestCase):
    def setUp(self):
        _reset_docker_mock()
        os.environ.pop("K8S_NAMESPACE", None)

    def test_returns_container_info(self):
        mock_client = mock.MagicMock()
        _mock_docker.from_env.return_value = mock_client

        mock_container = mock.MagicMock()
        mock_container.name = "sonarr"
        mock_container.status = "running"
        mock_container.image.tags = ["linuxserver/sonarr:latest"]
        mock_container.stats.return_value = {
            "cpu_stats": {"cpu_usage": {"total_usage": 200}, "system_cpu_usage": 10000},
            "precpu_stats": {"cpu_usage": {"total_usage": 100}, "system_cpu_usage": 9000},
            "memory_stats": {"usage": 104857600},  # 100 MiB
        }
        mock_client.containers.list.return_value = [mock_container]

        result = get_namespaces()

        self.assertIn("namespaces", result)
        self.assertEqual(result["namespaces"][0]["namespace"], "compose")
        self.assertEqual(result["namespaces"][0]["pods"], 1)
        self.assertEqual(result["namespaces"][0]["running"], 1)
        self.assertEqual(len(result["services"]), 1)
        self.assertEqual(result["services"][0]["name"], "sonarr")

    def test_returns_error_when_docker_unavailable(self):
        _mock_docker.from_env.side_effect = Exception("Cannot connect to Docker")

        result = get_namespaces()

        self.assertIn("error", result)
        self.assertIn("Cannot connect", result["error"])
        _mock_docker.from_env.side_effect = None


# ---------------------------------------------------------------------------
# check_image_updates() — Compose path
# ---------------------------------------------------------------------------

class TestCheckImageUpdatesCompose(unittest.TestCase):
    def setUp(self):
        _reset_docker_mock()
        os.environ.pop("K8S_NAMESPACE", None)

    def test_returns_image_list(self):
        mock_client = mock.MagicMock()
        _mock_docker.from_env.return_value = mock_client

        mock_container = mock.MagicMock()
        mock_container.name = "radarr"
        mock_container.image.tags = ["linuxserver/radarr:5.3.0"]
        mock_container.image.attrs = {
            "Created": "2025-01-15T10:00:00.000Z",
            "RepoDigests": ["linuxserver/radarr@sha256:abcdef1234567890"],
        }
        mock_container.attrs = {"State": {"StartedAt": "2025-03-01T08:00:00.000Z"}}
        mock_client.containers.list.return_value = [mock_container]

        result = check_image_updates()

        self.assertEqual(result["total"], 1)
        self.assertEqual(result["images"][0]["name"], "radarr")
        self.assertEqual(result["images"][0]["tag"], "5.3.0")
        self.assertEqual(result["pinned"], 1)  # "5.3.0" is not "latest"

    def test_detects_latest_tag(self):
        mock_client = mock.MagicMock()
        _mock_docker.from_env.return_value = mock_client

        mock_container = mock.MagicMock()
        mock_container.name = "jellyfin"
        mock_container.image.tags = ["jellyfin/jellyfin:latest"]
        mock_container.image.attrs = {"Created": "", "RepoDigests": []}
        mock_container.attrs = {"State": {"StartedAt": ""}}
        mock_client.containers.list.return_value = [mock_container]

        result = check_image_updates()

        self.assertEqual(result["pinned"], 0)  # "latest" is not pinned

    def test_error_returns_error_dict(self):
        _mock_docker.from_env.side_effect = Exception("Docker socket missing")

        result = check_image_updates()

        self.assertIn("error", result)
        _mock_docker.from_env.side_effect = None


# ---------------------------------------------------------------------------
# get_mount_info() — pure subprocess mock
# ---------------------------------------------------------------------------

class TestGetMountInfo(unittest.TestCase):
    @mock.patch("media_stack.api.services.ops.subprocess")
    def test_detects_nfs_mount(self, mock_subprocess):
        mock_result = mock.MagicMock()
        mock_result.stdout = (
            "nas:/volume1/media on /media type nfs4 (rw,relatime)\n"
            "/dev/sda1 on / type ext4 (rw,relatime)\n"
        )
        mock_subprocess.run.return_value = mock_result

        result = get_mount_info()

        self.assertTrue(result["nfs_available"])
        self.assertFalse(result["cifs_available"])
        nfs_mounts = [m for m in result["mounts"] if m["fstype"].startswith("nfs")]
        self.assertGreaterEqual(len(nfs_mounts), 1)

    @mock.patch("media_stack.api.services.ops.subprocess")
    def test_detects_cifs_mount(self, mock_subprocess):
        mock_result = mock.MagicMock()
        mock_result.stdout = "//server/share on /mnt/nas type cifs (rw)\n"
        mock_subprocess.run.return_value = mock_result

        result = get_mount_info()
        self.assertTrue(result["cifs_available"])
        self.assertEqual(len(result["mounts"]), 1)

    @mock.patch("media_stack.api.services.ops.subprocess")
    def test_returns_empty_when_no_relevant_mounts(self, mock_subprocess):
        mock_result = mock.MagicMock()
        mock_result.stdout = "/dev/sda1 on / type ext4 (rw,relatime)\ntmpfs on /tmp type tmpfs (rw)\n"
        mock_subprocess.run.return_value = mock_result

        result = get_mount_info()
        self.assertEqual(result["mounts"], [])
        self.assertFalse(result["nfs_available"])
        self.assertFalse(result["cifs_available"])


# ---------------------------------------------------------------------------
# Snapshot operations — file-based, no mocks needed
# ---------------------------------------------------------------------------

class TestTakeSnapshot(unittest.TestCase):
    def test_creates_snapshot_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sonarr_dir = Path(tmpdir) / "sonarr"
            sonarr_dir.mkdir()
            (sonarr_dir / "config.xml").write_text(
                "<Config><ApiKey>secret_key_123</ApiKey><Port>8989</Port></Config>"
            )
            os.environ["CONFIG_ROOT"] = tmpdir
            try:
                result = take_snapshot()
            finally:
                os.environ.pop("CONFIG_ROOT", None)

            self.assertEqual(result["status"], "created")
            self.assertGreaterEqual(result["configs"], 1)
            snapshot_dir = Path(tmpdir) / ".snapshots"
            snapshots = list(snapshot_dir.glob("snapshot-*.json"))
            self.assertEqual(len(snapshots), 1)
            # Verify API keys are redacted
            data = json.loads(snapshots[0].read_text())
            if "sonarr/config.xml" in data:
                self.assertIn("***", data["sonarr/config.xml"])
                self.assertNotIn("secret_key_123", data["sonarr/config.xml"])


class TestGetConfigSnapshots(unittest.TestCase):
    def test_lists_snapshots(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            snapshot_dir = Path(tmpdir) / ".snapshots"
            snapshot_dir.mkdir()
            (snapshot_dir / "snapshot-20260407T120000.json").write_text('{"test": "data"}')
            os.environ["CONFIG_ROOT"] = tmpdir
            try:
                result = get_config_snapshots()
            finally:
                os.environ.pop("CONFIG_ROOT", None)

            self.assertEqual(len(result["snapshots"]), 1)
            self.assertEqual(result["snapshots"][0]["file"], "snapshot-20260407T120000.json")

    def test_returns_empty_when_no_snapshots_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            os.environ["CONFIG_ROOT"] = tmpdir
            try:
                result = get_config_snapshots()
            finally:
                os.environ.pop("CONFIG_ROOT", None)
            self.assertEqual(result["snapshots"], [])


class TestGetSnapshotDetail(unittest.TestCase):
    def test_reads_snapshot_content(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            snapshot_dir = Path(tmpdir) / ".snapshots"
            snapshot_dir.mkdir()
            (snapshot_dir / "snapshot-20260407T120000.json").write_text(
                json.dumps({"sonarr/config.xml": "<Config>***</Config>"})
            )
            os.environ["CONFIG_ROOT"] = tmpdir
            try:
                result = get_snapshot_detail("snapshot-20260407T120000.json")
            finally:
                os.environ.pop("CONFIG_ROOT", None)
            self.assertIn("snapshot", result)
            self.assertIn("sonarr/config.xml", result["snapshot"])

    def test_returns_error_for_missing_snapshot(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            os.environ["CONFIG_ROOT"] = tmpdir
            try:
                result = get_snapshot_detail("snapshot-nonexistent.json")
            finally:
                os.environ.pop("CONFIG_ROOT", None)
            self.assertIn("error", result)

    def test_rejects_non_snapshot_filename(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            snapshot_dir = Path(tmpdir) / ".snapshots"
            snapshot_dir.mkdir()
            (snapshot_dir / "evil.json").write_text("{}")
            os.environ["CONFIG_ROOT"] = tmpdir
            try:
                result = get_snapshot_detail("evil.json")
            finally:
                os.environ.pop("CONFIG_ROOT", None)
            self.assertIn("error", result)


class TestDiffSnapshots(unittest.TestCase):
    def test_detects_changes_between_snapshots(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            snapshot_dir = Path(tmpdir) / ".snapshots"
            snapshot_dir.mkdir()
            (snapshot_dir / "snapshot-a.json").write_text(
                json.dumps({"sonarr/config.xml": "version1", "radarr/config.xml": "same"})
            )
            (snapshot_dir / "snapshot-b.json").write_text(
                json.dumps({"sonarr/config.xml": "version2", "radarr/config.xml": "same", "lidarr/config.xml": "new"})
            )
            os.environ["CONFIG_ROOT"] = tmpdir
            try:
                result = diff_snapshots("snapshot-a.json", "snapshot-b.json")
            finally:
                os.environ.pop("CONFIG_ROOT", None)

            self.assertEqual(result["total_changes"], 2)
            statuses = {d["file"]: d["status"] for d in result["diffs"]}
            self.assertEqual(statuses["sonarr/config.xml"], "changed")
            self.assertEqual(statuses["lidarr/config.xml"], "added")


# ---------------------------------------------------------------------------
# get_service_logs() — Compose path
# ---------------------------------------------------------------------------

class TestGetServiceLogsCompose(unittest.TestCase):
    def setUp(self):
        _reset_docker_mock()
        os.environ.pop("K8S_NAMESPACE", None)

    def test_returns_log_lines(self):
        mock_client = mock.MagicMock()
        _mock_docker.from_env.return_value = mock_client

        mock_container = mock.MagicMock()
        mock_container.logs.return_value = b"line1\nline2\nline3\n"
        mock_client.containers.get.return_value = mock_container

        result = get_service_logs("sonarr", lines=50)

        self.assertEqual(result["lines"], ["line1", "line2", "line3"])
        mock_container.logs.assert_called_once_with(tail=50)

    def test_returns_error_for_missing_container(self):
        mock_client = mock.MagicMock()
        _mock_docker.from_env.return_value = mock_client
        mock_client.containers.get.side_effect = Exception("Container not found")

        result = get_service_logs("nonexistent")

        self.assertEqual(result["lines"], [])
        self.assertIn("error", result)
        self.assertIn("not found", result["error"])


if __name__ == "__main__":
    unittest.main()
