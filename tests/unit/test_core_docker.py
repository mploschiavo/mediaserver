import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from core.platforms.compose.docker_client import DockerClient  # noqa: E402
from core.exceptions import DockerError  # noqa: E402


class _NotFound(Exception):
    status_code = 404


class CoreDockerTests(unittest.TestCase):
    def test_get_container_returns_none_for_not_found(self):
        client = mock.Mock()
        client.containers.get.side_effect = _NotFound("missing")
        docker = DockerClient(client=client)
        self.assertIsNone(docker.get_container("missing"))

    def test_remove_container_returns_false_when_absent(self):
        docker = DockerClient(client=mock.Mock())
        with mock.patch.object(docker, "get_container", return_value=None):
            self.assertFalse(docker.remove_container("missing"))

    def test_pull_image_raises_docker_error_on_failure(self):
        client = mock.Mock()
        client.images.pull.side_effect = RuntimeError("pull failed")
        docker = DockerClient(client=client)
        with self.assertRaises(DockerError):
            docker.pull_image("ghcr.io/example/app:latest")


if __name__ == "__main__":
    unittest.main()
