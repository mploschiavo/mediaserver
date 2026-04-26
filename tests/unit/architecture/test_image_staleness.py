"""Tests for image staleness detection in ops.check_image_updates()."""

import os
import sys
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import media_stack.api.services.ops as ops_mod  # noqa: E402


def _mock_container(name: str, image_tag: str, created_days_ago: int):
    """Create a mock Docker container with specified age."""
    created = (datetime.now() - timedelta(days=created_days_ago)).strftime("%Y-%m-%dT%H:%M:%S")
    started = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    container = MagicMock()
    container.name = name
    container.image.tags = [image_tag]
    container.image.short_id = "sha256:abc123"
    container.image.attrs = {"Created": created, "RepoDigests": []}
    container.attrs = {"State": {"StartedAt": started}}
    return container


class TestImageStaleness(unittest.TestCase):
    """check_image_updates should calculate days_old and stale fields."""

    @patch.dict(os.environ, {"K8S_NAMESPACE": ""})
    @patch("docker.from_env")
    def test_fresh_image_not_stale(self, mock_docker):
        c = _mock_container("sonarr", "sonarr:latest", created_days_ago=5)
        mock_docker.return_value.containers.list.return_value = [c]
        result = ops_mod.check_image_updates()
        img = result["images"][0]
        self.assertIn(img["days_old"], (4, 5))
        self.assertFalse(img["stale"])

    @patch.dict(os.environ, {"K8S_NAMESPACE": ""})
    @patch("docker.from_env")
    def test_old_image_is_stale(self, mock_docker):
        c = _mock_container("sonarr", "sonarr:v3.0", created_days_ago=45)
        mock_docker.return_value.containers.list.return_value = [c]
        result = ops_mod.check_image_updates()
        img = result["images"][0]
        self.assertIn(img["days_old"], (44, 45))
        self.assertTrue(img["stale"])

    @patch.dict(os.environ, {"K8S_NAMESPACE": ""})
    @patch("docker.from_env")
    def test_stale_count_in_response(self, mock_docker):
        fresh = _mock_container("sonarr", "sonarr:latest", 5)
        old = _mock_container("radarr", "radarr:v1.0", 60)
        mock_docker.return_value.containers.list.return_value = [fresh, old]
        result = ops_mod.check_image_updates()
        self.assertEqual(result["stale"], 1)

    @patch.dict(os.environ, {"K8S_NAMESPACE": ""})
    @patch("docker.from_env")
    def test_exactly_30_days_not_stale(self, mock_docker):
        c = _mock_container("app", "app:v1", created_days_ago=30)
        mock_docker.return_value.containers.list.return_value = [c]
        result = ops_mod.check_image_updates()
        img = result["images"][0]
        self.assertFalse(img["stale"])

    @patch.dict(os.environ, {"K8S_NAMESPACE": ""})
    @patch("docker.from_env")
    def test_31_days_is_stale(self, mock_docker):
        c = _mock_container("app", "app:v1", created_days_ago=31)
        mock_docker.return_value.containers.list.return_value = [c]
        result = ops_mod.check_image_updates()
        self.assertTrue(result["images"][0]["stale"])

    @patch.dict(os.environ, {"K8S_NAMESPACE": ""})
    @patch("docker.from_env")
    def test_missing_created_date(self, mock_docker):
        c = _mock_container("app", "app:v1", 0)
        c.image.attrs = {"Created": "", "RepoDigests": []}
        c.attrs = {"State": {"StartedAt": ""}}
        mock_docker.return_value.containers.list.return_value = [c]
        result = ops_mod.check_image_updates()
        img = result["images"][0]
        self.assertEqual(img["days_old"], -1)
        self.assertFalse(img["stale"])

    @patch.dict(os.environ, {"K8S_NAMESPACE": ""})
    @patch("docker.from_env")
    def test_all_fresh_zero_stale(self, mock_docker):
        containers = [_mock_container(f"svc{i}", f"svc{i}:latest", 2) for i in range(5)]
        mock_docker.return_value.containers.list.return_value = containers
        result = ops_mod.check_image_updates()
        self.assertEqual(result["stale"], 0)
        self.assertEqual(result["total"], 5)

    @patch.dict(os.environ, {"K8S_NAMESPACE": ""})
    @patch("docker.from_env")
    def test_multiple_stale(self, mock_docker):
        containers = [_mock_container(f"svc{i}", f"svc{i}:v1", 90) for i in range(3)]
        mock_docker.return_value.containers.list.return_value = containers
        result = ops_mod.check_image_updates()
        self.assertEqual(result["stale"], 3)

    @patch.dict(os.environ, {"K8S_NAMESPACE": ""})
    @patch("docker.from_env")
    def test_pinned_count_preserved(self, mock_docker):
        c1 = _mock_container("sonarr", "sonarr:v3.1", 10)
        c2 = _mock_container("radarr", "radarr:latest", 10)
        mock_docker.return_value.containers.list.return_value = [c1, c2]
        result = ops_mod.check_image_updates()
        self.assertEqual(result["pinned"], 1)  # v3.1 is pinned, latest is not

    @patch.dict(os.environ, {"K8S_NAMESPACE": ""})
    @patch("docker.from_env", side_effect=Exception("Docker not available"))
    def test_docker_error_returns_error(self, mock_docker):
        result = ops_mod.check_image_updates()
        self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()
