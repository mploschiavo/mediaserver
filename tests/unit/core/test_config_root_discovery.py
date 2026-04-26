"""Unit tests for config-root auto-discovery preflight."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from media_stack.api.preflight.config_root_discovery import (
    DiscoveryResult,
    _discover_via_docker_env,
    _discover_via_docker_mounts,
    _discover_via_path_scan,
    discover_config_root,
)


# ---------------------------------------------------------------------------
# Helpers to build fake Docker container objects
# ---------------------------------------------------------------------------

def _make_container(
    name: str,
    mounts: list[dict[str, str]] | None = None,
    env: list[str] | None = None,
    labels: dict[str, str] | None = None,
) -> MagicMock:
    """Create a mock Docker container with attrs."""
    c = MagicMock()
    c.name = name
    c.attrs = {
        "Mounts": mounts or [],
        "Config": {
            "Env": env or [],
            "Labels": labels or {},
        },
    }
    return c


# ---------------------------------------------------------------------------
# Method 1: Docker mount inspection
# ---------------------------------------------------------------------------

class TestDockerMountDiscovery(unittest.TestCase):
    """Test _discover_via_docker_mounts."""

    @patch("media_stack.api.preflight.config_root_discovery.SERVICES", [])
    def test_no_services_returns_empty(self):
        with patch("docker.from_env") as mock_docker:
            mock_docker.return_value.containers.list.return_value = []
            result = _discover_via_docker_mounts()
        self.assertIsNone(result.config_root)
        self.assertEqual(result.keys, {})

    def test_docker_not_available_returns_empty(self):
        """When docker SDK is not importable, return empty result gracefully."""
        with patch.dict("sys.modules", {"docker": None}):
            result = _discover_via_docker_mounts()
        self.assertIsNone(result.config_root)

    @patch("media_stack.api.preflight.config_root_discovery.SERVICES")
    def test_discovers_config_root_from_mount(self, mock_services):
        svc = MagicMock()
        svc.id = "sonarr"
        svc.host = "sonarr"
        svc.api_key_config = "sonarr/config.xml"
        svc.api_key_env = "SONARR_API_KEY"
        mock_services.__iter__ = lambda self: iter([svc])

        with tempfile.TemporaryDirectory() as tmpdir:
            # Simulate a mount: host path /tmp/xxx/config/sonarr -> /config
            config_dir = Path(tmpdir) / "config" / "sonarr"
            config_dir.mkdir(parents=True)
            host_source = str(config_dir)

            container = _make_container(
                "sonarr",
                mounts=[{"Destination": "/config", "Source": host_source}],
            )

            with patch("docker.from_env") as mock_docker:
                mock_docker.return_value.containers.list.return_value = [container]
                result = _discover_via_docker_mounts()

        expected_root = str(Path(tmpdir) / "config")
        self.assertEqual(result.config_root, expected_root)
        self.assertEqual(result.source, "docker_mounts")

    @patch("media_stack.api.preflight.config_root_discovery.SERVICES")
    def test_mount_without_service_subdir(self, mock_services):
        """When mount source doesn't end with the service subdir, use it as-is."""
        svc = MagicMock()
        svc.id = "sonarr"
        svc.host = "sonarr"
        svc.api_key_config = "sonarr/config.xml"
        svc.api_key_env = "SONARR_API_KEY"
        mock_services.__iter__ = lambda self: iter([svc])

        with tempfile.TemporaryDirectory() as tmpdir:
            container = _make_container(
                "sonarr",
                mounts=[{"Destination": "/config", "Source": tmpdir}],
            )
            with patch("docker.from_env") as mock_docker:
                mock_docker.return_value.containers.list.return_value = [container]
                result = _discover_via_docker_mounts()

        # When source doesn't end in /sonarr, it's used as the root
        self.assertEqual(result.config_root, tmpdir)

    @patch("media_stack.api.preflight.config_root_discovery.SERVICES")
    def test_votes_pick_majority(self, mock_services):
        """When multiple services have different roots, pick the majority."""
        svc1 = MagicMock()
        svc1.id = "sonarr"
        svc1.host = "sonarr"
        svc1.api_key_config = "sonarr/config.xml"
        svc1.api_key_env = "SONARR_API_KEY"

        svc2 = MagicMock()
        svc2.id = "radarr"
        svc2.host = "radarr"
        svc2.api_key_config = "radarr/config.xml"
        svc2.api_key_env = "RADARR_API_KEY"

        mock_services.__iter__ = lambda self: iter([svc1, svc2])

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "config"
            (root / "sonarr").mkdir(parents=True)
            (root / "radarr").mkdir(parents=True)

            c1 = _make_container(
                "sonarr",
                mounts=[{"Destination": "/config", "Source": str(root / "sonarr")}],
            )
            c2 = _make_container(
                "radarr",
                mounts=[{"Destination": "/config", "Source": str(root / "radarr")}],
            )

            with patch("docker.from_env") as mock_docker:
                mock_docker.return_value.containers.list.return_value = [c1, c2]
                result = _discover_via_docker_mounts()

        self.assertEqual(result.config_root, str(root))

    @patch("media_stack.api.preflight.config_root_discovery.SERVICES")
    def test_compose_prefixed_container_name_dash(self, mock_services):
        """Container names with compose v2 prefix (e.g. media-stack-sonarr-1) are matched."""
        svc = MagicMock()
        svc.id = "sonarr"
        svc.host = "sonarr"
        svc.api_key_config = "sonarr/config.xml"
        svc.api_key_env = "SONARR_API_KEY"
        mock_services.__iter__ = lambda self: iter([svc])

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "config"
            (root / "sonarr").mkdir(parents=True)

            # Compose v2 uses dashes: project-service-N
            container = _make_container(
                "media-stack-sonarr-1",
                mounts=[{"Destination": "/config", "Source": str(root / "sonarr")}],
            )

            with patch("docker.from_env") as mock_docker:
                mock_docker.return_value.containers.list.return_value = [container]
                result = _discover_via_docker_mounts()

        # The name parser strips trailing -1 and extracts "sonarr"
        self.assertEqual(result.config_root, str(root))
        self.assertEqual(result.source, "docker_mounts")

    @patch("media_stack.api.preflight.config_root_discovery.SERVICES")
    def test_compose_label_takes_precedence(self, mock_services):
        """When the compose service label is present, it's used for matching."""
        svc = MagicMock()
        svc.id = "sonarr"
        svc.host = "sonarr"
        svc.api_key_config = "sonarr/config.xml"
        svc.api_key_env = "SONARR_API_KEY"
        mock_services.__iter__ = lambda self: iter([svc])

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "config"
            (root / "sonarr").mkdir(parents=True)

            container = _make_container(
                "some-weird-container-name",
                mounts=[{"Destination": "/config", "Source": str(root / "sonarr")}],
                labels={"com.docker.compose.service": "sonarr"},
            )

            with patch("docker.from_env") as mock_docker:
                mock_docker.return_value.containers.list.return_value = [container]
                result = _discover_via_docker_mounts()

        self.assertEqual(result.config_root, str(root))

    @patch("media_stack.api.preflight.config_root_discovery.SERVICES")
    def test_compose_v1_underscore_name(self, mock_services):
        """Container names with compose v1 prefix (e.g. project_sonarr_1) are matched."""
        svc = MagicMock()
        svc.id = "sonarr"
        svc.host = "sonarr"
        svc.api_key_config = "sonarr/config.xml"
        svc.api_key_env = "SONARR_API_KEY"
        mock_services.__iter__ = lambda self: iter([svc])

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "config"
            (root / "sonarr").mkdir(parents=True)

            container = _make_container(
                "mediastack_sonarr_1",
                mounts=[{"Destination": "/config", "Source": str(root / "sonarr")}],
            )

            with patch("docker.from_env") as mock_docker:
                mock_docker.return_value.containers.list.return_value = [container]
                result = _discover_via_docker_mounts()

        self.assertEqual(result.config_root, str(root))


# ---------------------------------------------------------------------------
# Method 2: Docker environment inspection
# ---------------------------------------------------------------------------

class TestDockerEnvDiscovery(unittest.TestCase):
    """Test _discover_via_docker_env."""

    def test_docker_not_available_returns_empty(self):
        with patch.dict("sys.modules", {"docker": None}):
            result = _discover_via_docker_env()
        self.assertEqual(result.keys, {})

    @patch("media_stack.api.preflight.config_root_discovery.SERVICES")
    def test_discovers_standard_env_key(self, mock_services):
        svc = MagicMock()
        svc.id = "sonarr"
        svc.host = "sonarr"
        svc.api_key_env = "SONARR_API_KEY"
        mock_services.__iter__ = lambda self: iter([svc])

        container = _make_container(
            "sonarr",
            env=["SONARR_API_KEY=abc123", "OTHER_VAR=ignored"],
        )

        with patch("docker.from_env") as mock_docker:
            mock_docker.return_value.containers.list.return_value = [container]
            result = _discover_via_docker_env()

        self.assertEqual(result.keys, {"SONARR_API_KEY": "abc123"})

    @patch("media_stack.api.preflight.config_root_discovery.SERVICES")
    def test_discovers_double_underscore_env(self, mock_services):
        """Arr apps support SONARR__AUTH__APIKEY=xxx format."""
        svc = MagicMock()
        svc.id = "sonarr"
        svc.host = "sonarr"
        svc.api_key_env = "SONARR_API_KEY"
        mock_services.__iter__ = lambda self: iter([svc])

        container = _make_container(
            "sonarr",
            env=["SONARR__AUTH__APIKEY=def456"],
        )

        with patch("docker.from_env") as mock_docker:
            mock_docker.return_value.containers.list.return_value = [container]
            result = _discover_via_docker_env()

        self.assertEqual(result.keys, {"SONARR_API_KEY": "def456"})

    @patch("media_stack.api.preflight.config_root_discovery.SERVICES")
    def test_standard_env_preferred_over_double_underscore(self, mock_services):
        """When both env formats are present, the standard one wins (checked first)."""
        svc = MagicMock()
        svc.id = "sonarr"
        svc.host = "sonarr"
        svc.api_key_env = "SONARR_API_KEY"
        mock_services.__iter__ = lambda self: iter([svc])

        container = _make_container(
            "sonarr",
            env=[
                "SONARR_API_KEY=standard_key",
                "SONARR__AUTH__APIKEY=double_key",
            ],
        )

        with patch("docker.from_env") as mock_docker:
            mock_docker.return_value.containers.list.return_value = [container]
            result = _discover_via_docker_env()

        self.assertEqual(result.keys["SONARR_API_KEY"], "standard_key")

    @patch("media_stack.api.preflight.config_root_discovery.SERVICES")
    def test_no_matching_env_returns_empty(self, mock_services):
        svc = MagicMock()
        svc.id = "sonarr"
        svc.host = "sonarr"
        svc.api_key_env = "SONARR_API_KEY"
        mock_services.__iter__ = lambda self: iter([svc])

        container = _make_container("sonarr", env=["UNRELATED=val"])

        with patch("docker.from_env") as mock_docker:
            mock_docker.return_value.containers.list.return_value = [container]
            result = _discover_via_docker_env()

        self.assertEqual(result.keys, {})

    @patch("media_stack.api.preflight.config_root_discovery.SERVICES")
    def test_multiple_services_discovered(self, mock_services):
        svc1 = MagicMock()
        svc1.id = "sonarr"
        svc1.host = "sonarr"
        svc1.api_key_env = "SONARR_API_KEY"

        svc2 = MagicMock()
        svc2.id = "radarr"
        svc2.host = "radarr"
        svc2.api_key_env = "RADARR_API_KEY"

        mock_services.__iter__ = lambda self: iter([svc1, svc2])

        c1 = _make_container("sonarr", env=["SONARR_API_KEY=key1"])
        c2 = _make_container("radarr", env=["RADARR_API_KEY=key2"])

        with patch("docker.from_env") as mock_docker:
            mock_docker.return_value.containers.list.return_value = [c1, c2]
            result = _discover_via_docker_env()

        self.assertEqual(result.keys["SONARR_API_KEY"], "key1")
        self.assertEqual(result.keys["RADARR_API_KEY"], "key2")


# ---------------------------------------------------------------------------
# Method 4: Path scanning
# ---------------------------------------------------------------------------

class TestPathScanDiscovery(unittest.TestCase):
    """Test _discover_via_path_scan."""

    def test_finds_config_at_current_root(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "sonarr").mkdir()
            (Path(tmpdir) / "sonarr" / "config.xml").write_text(
                "<Config><ApiKey>test</ApiKey></Config>"
            )
            result = _discover_via_path_scan(tmpdir)

        self.assertEqual(result.config_root, tmpdir)

    def test_returns_none_when_no_configs_found(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = _discover_via_path_scan(tmpdir)
        self.assertIsNone(result.config_root)

    def test_nonexistent_path_returns_none(self):
        result = _discover_via_path_scan("/nonexistent/path/that/does/not/exist")
        self.assertIsNone(result.config_root)

    def test_scans_candidate_paths(self):
        """When the current root has no configs, scan falls back to candidates."""
        with tempfile.TemporaryDirectory() as empty_root:
            with tempfile.TemporaryDirectory() as real_root:
                (Path(real_root) / "sonarr").mkdir()
                (Path(real_root) / "sonarr" / "config.xml").write_text(
                    "<Config><ApiKey>test</ApiKey></Config>"
                )
                # Patch _CANDIDATE_ROOTS to include our real_root
                with patch(
                    "media_stack.api.preflight.config_root_discovery._CANDIDATE_ROOTS",
                    (real_root,),
                ):
                    result = _discover_via_path_scan(empty_root)

        self.assertEqual(result.config_root, real_root)

    def test_current_root_preferred_over_candidates(self):
        """Current root is checked first, even if candidates also have configs."""
        with tempfile.TemporaryDirectory() as root1:
            with tempfile.TemporaryDirectory() as root2:
                for root in (root1, root2):
                    (Path(root) / "sonarr").mkdir()
                    (Path(root) / "sonarr" / "config.xml").write_text(
                        "<Config><ApiKey>test</ApiKey></Config>"
                    )
                with patch(
                    "media_stack.api.preflight.config_root_discovery._CANDIDATE_ROOTS",
                    (root2,),
                ):
                    result = _discover_via_path_scan(root1)

        self.assertEqual(result.config_root, root1)


# ---------------------------------------------------------------------------
# Main orchestrator: discover_config_root
# ---------------------------------------------------------------------------

class TestDiscoverConfigRoot(unittest.TestCase):
    """Test the full discovery orchestrator."""

    @patch("media_stack.api.preflight.config_root_discovery._discover_via_docker_mounts")
    @patch("media_stack.api.preflight.config_root_discovery._discover_via_docker_env")
    @patch("media_stack.api.preflight.config_root_discovery._discover_via_path_scan")
    def test_docker_mounts_wins(self, mock_scan, mock_env, mock_mounts):
        """Docker mount discovery takes precedence over path scan."""
        mock_mounts.return_value = DiscoveryResult(
            config_root="/docker/config", source="docker_mounts"
        )
        mock_env.return_value = DiscoveryResult(keys={})
        mock_scan.return_value = DiscoveryResult(config_root="/scan/config")

        result = discover_config_root(current_root="/old/config")

        self.assertEqual(result.config_root, "/docker/config")
        self.assertEqual(result.source, "docker_mounts")
        # Path scan should NOT be called when docker mounts succeeds
        mock_scan.assert_not_called()

    @patch("media_stack.api.preflight.config_root_discovery._discover_via_docker_mounts")
    @patch("media_stack.api.preflight.config_root_discovery._discover_via_docker_env")
    @patch("media_stack.api.preflight.config_root_discovery._discover_via_path_scan")
    def test_falls_back_to_path_scan(self, mock_scan, mock_env, mock_mounts):
        """When docker mount discovery finds nothing, falls back to path scan."""
        mock_mounts.return_value = DiscoveryResult()
        mock_env.return_value = DiscoveryResult(keys={})
        mock_scan.return_value = DiscoveryResult(
            config_root="/scan/config", source="path_scan"
        )

        result = discover_config_root(current_root="/old/config")

        self.assertEqual(result.config_root, "/scan/config")
        self.assertEqual(result.source, "path_scan")

    @patch("media_stack.api.preflight.config_root_discovery._discover_via_docker_mounts")
    @patch("media_stack.api.preflight.config_root_discovery._discover_via_docker_env")
    @patch("media_stack.api.preflight.config_root_discovery._discover_via_path_scan")
    def test_env_keys_merged(self, mock_scan, mock_env, mock_mounts):
        """Keys from docker env are merged into the result."""
        mock_mounts.return_value = DiscoveryResult()
        mock_env.return_value = DiscoveryResult(
            keys={"SONARR_API_KEY": "from_env"}
        )
        mock_scan.return_value = DiscoveryResult()

        result = discover_config_root(current_root="/old/config")

        self.assertEqual(result.keys["SONARR_API_KEY"], "from_env")

    @patch("media_stack.api.preflight.config_root_discovery._discover_via_docker_mounts")
    @patch("media_stack.api.preflight.config_root_discovery._discover_via_docker_env")
    @patch("media_stack.api.preflight.config_root_discovery._discover_via_path_scan")
    def test_all_methods_fail_gracefully(self, mock_scan, mock_env, mock_mounts):
        """When all methods fail, result has no root and no keys."""
        mock_mounts.side_effect = Exception("docker not available")
        mock_env.side_effect = Exception("docker not available")
        mock_scan.side_effect = Exception("permission denied")

        logs: list[str] = []
        result = discover_config_root(current_root="/old/config", log=logs.append)

        self.assertIsNone(result.config_root)
        self.assertEqual(result.keys, {})
        # Should have logged warnings
        self.assertTrue(any("[WARN]" in msg for msg in logs))

    @patch("media_stack.api.preflight.config_root_discovery._discover_via_docker_mounts")
    @patch("media_stack.api.preflight.config_root_discovery._discover_via_docker_env")
    @patch("media_stack.api.preflight.config_root_discovery._discover_via_path_scan")
    def test_path_scan_same_as_current_not_set(self, mock_scan, mock_env, mock_mounts):
        """If path scan returns the same root as current, config_root stays None."""
        mock_mounts.return_value = DiscoveryResult()
        mock_env.return_value = DiscoveryResult(keys={})
        mock_scan.return_value = DiscoveryResult(
            config_root="/old/config", source="path_scan"
        )

        result = discover_config_root(current_root="/old/config")

        # Same as current -> no change needed
        self.assertIsNone(result.config_root)

    @patch("media_stack.api.preflight.config_root_discovery._discover_via_docker_mounts")
    @patch("media_stack.api.preflight.config_root_discovery._discover_via_docker_env")
    @patch("media_stack.api.preflight.config_root_discovery._discover_via_path_scan")
    def test_default_current_root_from_env(self, mock_scan, mock_env, mock_mounts):
        """When current_root is not specified, reads from CONFIG_ROOT env."""
        mock_mounts.return_value = DiscoveryResult()
        mock_env.return_value = DiscoveryResult(keys={})
        mock_scan.return_value = DiscoveryResult()

        with patch.dict(os.environ, {"CONFIG_ROOT": "/env/config"}):
            discover_config_root()

        mock_scan.assert_called_once_with("/env/config", log=None)


# ---------------------------------------------------------------------------
# Integration-level: api_keys.run_preflight with discovery
# ---------------------------------------------------------------------------

class TestApiKeysPreflightWithDiscovery(unittest.TestCase):
    """Test that run_preflight integrates config_root_discovery properly."""

    def test_discovery_changes_config_root(self):
        """If discovery finds a different root, run_preflight uses it."""
        with tempfile.TemporaryDirectory() as wrong_root:
            with tempfile.TemporaryDirectory() as real_root:
                # Put a config file in the real root
                (Path(real_root) / "sonarr").mkdir()
                (Path(real_root) / "sonarr" / "config.xml").write_text(
                    "<Config><ApiKey>discovered_key_0123456789</ApiKey></Config>"
                )

                fake_discovery = DiscoveryResult(
                    config_root=real_root,
                    source="docker_mounts",
                    keys={},
                )

                # Patch at the source module -- the deferred import inside
                # run_preflight resolves from here.
                with patch(
                    "media_stack.api.preflight.config_root_discovery.discover_config_root",
                    return_value=fake_discovery,
                ):
                    from media_stack.api.preflight.api_keys import run_preflight

                    result = run_preflight(config_root=wrong_root)

                # Should have found the key in the real root
                self.assertIn("SONARR_API_KEY", result)
                self.assertEqual(result["SONARR_API_KEY"], "discovered_key_0123456789")

    def test_discovery_keys_not_overwritten_by_file(self):
        """Keys from container env discovery are not overwritten by file readers."""
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / "sonarr").mkdir()
            (Path(root) / "sonarr" / "config.xml").write_text(
                "<Config><ApiKey>file_key</ApiKey></Config>"
            )

            fake_discovery = DiscoveryResult(
                keys={"SONARR_API_KEY": "env_key"},
            )

            with patch(
                "media_stack.api.preflight.config_root_discovery.discover_config_root",
                return_value=fake_discovery,
            ):
                from media_stack.api.preflight.api_keys import run_preflight

                result = run_preflight(config_root=root)

            # The env_key from discovery should win over the file_key
            self.assertEqual(result["SONARR_API_KEY"], "env_key")

    def test_discovery_failure_falls_back_to_file(self):
        """If discovery raises, file-based key reading still works."""
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / "sonarr").mkdir()
            (Path(root) / "sonarr" / "config.xml").write_text(
                "<Config><ApiKey>fallback_key_1234</ApiKey></Config>"
            )

            with patch(
                "media_stack.api.preflight.config_root_discovery.discover_config_root",
                side_effect=Exception("boom"),
            ):
                from media_stack.api.preflight.api_keys import run_preflight

                result = run_preflight(config_root=root)

            self.assertIn("SONARR_API_KEY", result)
            self.assertEqual(result["SONARR_API_KEY"], "fallback_key_1234")


if __name__ == "__main__":
    unittest.main()
