"""Tests for media_stack.api.services.health — probes, key discovery, history."""

import json
import os
import sqlite3
import sys
import tempfile
import textwrap
import time
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import media_stack.api.services.health as health_mod  # noqa: E402


# ---------------------------------------------------------------------------
# discover_api_keys
# ---------------------------------------------------------------------------


class TestDiscoverApiKeysEnvVars(unittest.TestCase):
    """discover_api_keys should prefer env vars over config files."""

    @patch.object(health_mod, "SERVICES", [
        MagicMock(id="sonarr", api_key_env="SONARR_API_KEY"),
        MagicMock(id="radarr", api_key_env="RADARR_API_KEY"),
    ])
    @patch.dict(os.environ, {
        "SONARR_API_KEY": "env-sonarr-key",
        "RADARR_API_KEY": "env-radarr-key",
        "CONFIG_ROOT": "/nonexistent-path",
    })
    def test_env_vars_take_precedence(self):
        keys = health_mod.discover_api_keys()
        self.assertEqual(keys["sonarr"], "env-sonarr-key")
        self.assertEqual(keys["radarr"], "env-radarr-key")

    @patch.object(health_mod, "SERVICES", [
        MagicMock(id="sonarr", api_key_env="SONARR_API_KEY"),
    ])
    @patch.dict(os.environ, {
        "SONARR_API_KEY": "  ",
        "CONFIG_ROOT": "/nonexistent-path",
    })
    def test_blank_env_var_ignored(self):
        keys = health_mod.discover_api_keys()
        self.assertNotIn("sonarr", keys)


class TestDiscoverApiKeysXml(unittest.TestCase):
    """discover_api_keys should parse *arr XML config files."""

    @patch.object(health_mod, "SERVICES", [])
    def test_xml_config_sonarr(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sonarr_dir = Path(tmpdir) / "sonarr"
            sonarr_dir.mkdir()
            (sonarr_dir / "config.xml").write_text(
                "<Config><ApiKey>test123</ApiKey></Config>"
            )
            with patch.dict(os.environ, {"CONFIG_ROOT": tmpdir}):
                keys = health_mod.discover_api_keys()
            self.assertEqual(keys.get("sonarr"), "test123")

    @patch.object(health_mod, "SERVICES", [])
    def test_xml_config_multiple_arr(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            for app, key in [("radarr", "radarr-key"), ("prowlarr", "prowl-key")]:
                d = Path(tmpdir) / app
                d.mkdir()
                (d / "config.xml").write_text(
                    f"<Config><ApiKey>{key}</ApiKey></Config>"
                )
            with patch.dict(os.environ, {"CONFIG_ROOT": tmpdir}):
                keys = health_mod.discover_api_keys()
            self.assertEqual(keys["radarr"], "radarr-key")
            self.assertEqual(keys["prowlarr"], "prowl-key")

    @patch.object(health_mod, "SERVICES", [])
    def test_xml_missing_file_skipped(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"CONFIG_ROOT": tmpdir}):
                keys = health_mod.discover_api_keys()
            # Should not raise; sonarr simply absent
            self.assertNotIn("sonarr", keys)


class TestDiscoverApiKeysIni(unittest.TestCase):
    """discover_api_keys should parse INI config files (sabnzbd, tautulli)."""

    @patch.object(health_mod, "SERVICES", [])
    def test_sabnzbd_ini(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sab_dir = Path(tmpdir) / "sabnzbd"
            sab_dir.mkdir()
            (sab_dir / "sabnzbd.ini").write_text(
                textwrap.dedent("""\
                [misc]
                api_key = abc456
                """)
            )
            with patch.dict(os.environ, {"CONFIG_ROOT": tmpdir}):
                keys = health_mod.discover_api_keys()
            self.assertEqual(keys.get("sabnzbd"), "abc456")

    @patch.object(health_mod, "SERVICES", [])
    def test_tautulli_ini(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tau_dir = Path(tmpdir) / "tautulli"
            tau_dir.mkdir()
            (tau_dir / "config.ini").write_text(
                textwrap.dedent("""\
                [General]
                api_key = tau-secret-999
                """)
            )
            with patch.dict(os.environ, {"CONFIG_ROOT": tmpdir}):
                keys = health_mod.discover_api_keys()
            self.assertEqual(keys.get("tautulli"), "tau-secret-999")


class TestDiscoverApiKeysBazarr(unittest.TestCase):
    """discover_api_keys should read Bazarr's YAML config via helper."""

    @patch.object(health_mod, "SERVICES", [])
    @patch("media_stack.api.preflight.api_keys._read_bazarr_api_key", return_value="baz-key-789")
    def test_bazarr_yaml(self, mock_reader):
        with tempfile.TemporaryDirectory() as tmpdir:
            bazarr_cfg = Path(tmpdir) / "bazarr" / "config"
            bazarr_cfg.mkdir(parents=True)
            (bazarr_cfg / "config.yaml").write_text("auth:\n  apikey: baz-key-789\n")
            with patch.dict(os.environ, {"CONFIG_ROOT": tmpdir}):
                keys = health_mod.discover_api_keys()
            self.assertEqual(keys.get("bazarr"), "baz-key-789")
            mock_reader.assert_called_once()


class TestDiscoverApiKeysJellyseerr(unittest.TestCase):
    """discover_api_keys should read Jellyseerr's settings.json."""

    @patch.object(health_mod, "SERVICES", [])
    def test_jellyseerr_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            js_dir = Path(tmpdir) / "jellyseerr"
            js_dir.mkdir()
            (js_dir / "settings.json").write_text(
                json.dumps({"main": {"apiKey": "jseerr-key-42"}})
            )
            with patch.dict(os.environ, {"CONFIG_ROOT": tmpdir}):
                keys = health_mod.discover_api_keys()
            self.assertEqual(keys.get("jellyseerr"), "jseerr-key-42")

    @patch.object(health_mod, "SERVICES", [])
    def test_jellyseerr_malformed_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            js_dir = Path(tmpdir) / "jellyseerr"
            js_dir.mkdir()
            (js_dir / "settings.json").write_text("NOT JSON AT ALL")
            with patch.dict(os.environ, {"CONFIG_ROOT": tmpdir}):
                keys = health_mod.discover_api_keys()
            self.assertNotIn("jellyseerr", keys)


class TestDiscoverApiKeysJellyfin(unittest.TestCase):
    """discover_api_keys should read Jellyfin's SQLite DB."""

    @patch.object(health_mod, "SERVICES", [])
    def test_jellyfin_sqlite(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            jf_dir = Path(tmpdir) / "jellyfin" / "data"
            jf_dir.mkdir(parents=True)
            db_path = jf_dir / "jellyfin.db"
            conn = sqlite3.connect(str(db_path))
            conn.execute(
                "CREATE TABLE ApiKeys (Id INTEGER PRIMARY KEY, AccessToken TEXT)"
            )
            conn.execute("INSERT INTO ApiKeys (AccessToken) VALUES ('jf-token-abc')")
            conn.commit()
            conn.close()
            with patch.dict(os.environ, {"CONFIG_ROOT": tmpdir}):
                keys = health_mod.discover_api_keys()
            self.assertEqual(keys.get("jellyfin"), "jf-token-abc")


# ---------------------------------------------------------------------------
# _get_running_containers
# ---------------------------------------------------------------------------


class TestGetRunningContainersDocker(unittest.TestCase):
    """_get_running_containers should list Docker container names."""

    @patch.dict(os.environ, {"K8S_NAMESPACE": ""})
    @patch("docker.from_env")
    def test_docker_containers(self, mock_from_env):
        c1 = MagicMock()
        c1.name = "sonarr"
        c2 = MagicMock()
        c2.name = "radarr"
        mock_client = MagicMock()
        mock_client.containers.list.return_value = [c1, c2]
        mock_from_env.return_value = mock_client

        names = health_mod._get_running_containers()
        self.assertEqual(names, {"sonarr", "radarr"})

    @patch.dict(os.environ, {"K8S_NAMESPACE": ""})
    @patch("docker.from_env", side_effect=Exception("Docker not available"))
    def test_docker_failure_returns_empty(self, mock_from_env):
        names = health_mod._get_running_containers()
        self.assertEqual(names, set())


class TestGetRunningContainersK8s(unittest.TestCase):
    """_get_running_containers should list K8s pod names via the API."""

    @patch.dict(os.environ, {"K8S_NAMESPACE": "media"})
    @patch("kubernetes.config.load_incluster_config")
    @patch("kubernetes.client.CoreV1Api")
    def test_k8s_pods(self, mock_core_v1, mock_load_config):
        pod1 = MagicMock()
        pod1.status.phase = "Running"
        pod1.metadata.labels = {"app": "sonarr"}
        pod1.metadata.name = "sonarr-abc123"

        pod2 = MagicMock()
        pod2.status.phase = "Pending"
        pod2.metadata.labels = {"app": "radarr"}
        pod2.metadata.name = "radarr-def456"

        mock_v1 = MagicMock()
        mock_v1.list_namespaced_pod.return_value = MagicMock(items=[pod1, pod2])
        mock_core_v1.return_value = mock_v1

        names = health_mod._get_running_containers()
        # Only pod1 is Running
        self.assertIn("sonarr", names)
        self.assertNotIn("radarr", names)

    @patch.dict(os.environ, {"K8S_NAMESPACE": "media"})
    @patch("kubernetes.config.load_incluster_config", side_effect=Exception("no cluster"))
    @patch("kubernetes.config.load_kube_config", side_effect=Exception("no kubeconfig"))
    def test_k8s_failure_returns_empty(self, mock_kube, mock_cluster):
        names = health_mod._get_running_containers()
        self.assertEqual(names, set())


# ---------------------------------------------------------------------------
# probe_services
# ---------------------------------------------------------------------------


class TestProbeServices(unittest.TestCase):
    """probe_services should return structured health + auth results."""

    def _make_cache(self, cached_value=None):
        cache = MagicMock()
        cache.get.return_value = cached_value
        return cache

    def test_returns_cached_result(self):
        cached = {"services": {}, "healthy": 0, "total": 0}
        cache = self._make_cache(cached)
        result = health_mod.probe_services(cache)
        self.assertIs(result, cached)

    @patch.object(health_mod, "SERVICE_PROBES", {
        "sonarr": ("sonarr", 8989, "/api/v3/health"),
    })
    @patch.object(health_mod, "AUTH_PROBES", {})
    @patch.object(health_mod, "discover_api_keys", return_value={})
    @patch.object(health_mod, "_get_running_containers", return_value=set())
    @patch("urllib.request.urlopen")
    def test_probe_ok(self, mock_urlopen, mock_containers, mock_keys):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        cache = self._make_cache(None)
        result = health_mod.probe_services(cache)
        self.assertIn("services", result)
        self.assertIn("healthy", result)
        self.assertIn("total", result)
        svc = result["services"].get("sonarr", {})
        self.assertEqual(svc.get("status"), "ok")
        self.assertEqual(result["healthy"], 1)
        self.assertEqual(result["total"], 1)

    @patch.object(health_mod, "SERVICE_PROBES", {
        "sonarr": ("sonarr", 8989, "/api/v3/health"),
    })
    @patch.object(health_mod, "AUTH_PROBES", {})
    @patch.object(health_mod, "discover_api_keys", return_value={})
    @patch.object(health_mod, "_get_running_containers", return_value=set())
    @patch("urllib.request.urlopen", side_effect=Exception("connection refused"))
    def test_probe_error(self, mock_urlopen, mock_containers, mock_keys):
        cache = self._make_cache(None)
        result = health_mod.probe_services(cache)
        svc = result["services"].get("sonarr", {})
        self.assertEqual(svc.get("status"), "error")
        self.assertIn("error", svc)

    @patch.object(health_mod, "SERVICE_PROBES", {
        "sonarr": ("sonarr", 8989, "/api/v3/health"),
    })
    @patch.object(health_mod, "AUTH_PROBES", {
        "sonarr": ("sonarr", 8989, "/api/v3/system/status", "X-Api-Key"),
    })
    @patch.object(health_mod, "discover_api_keys", return_value={"sonarr": "my-key"})
    @patch.object(health_mod, "_get_running_containers", return_value=set())
    @patch("urllib.request.urlopen")
    def test_probe_with_auth_header(self, mock_urlopen, mock_containers, mock_keys):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        cache = self._make_cache(None)
        result = health_mod.probe_services(cache)
        svc = result["services"].get("sonarr", {})
        self.assertEqual(svc.get("auth"), "ok")

    @patch.object(health_mod, "SERVICE_PROBES", {
        "sonarr": ("sonarr", 8989, "/api/v3/health"),
    })
    @patch.object(health_mod, "AUTH_PROBES", {
        "sonarr": ("sonarr", 8989, "/api/v3/system/status", "X-Api-Key"),
    })
    @patch.object(health_mod, "discover_api_keys", return_value={})
    @patch.object(health_mod, "_get_running_containers", return_value=set())
    @patch("urllib.request.urlopen")
    def test_probe_no_key_auth(self, mock_urlopen, mock_containers, mock_keys):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        cache = self._make_cache(None)
        result = health_mod.probe_services(cache)
        svc = result["services"].get("sonarr", {})
        self.assertEqual(svc.get("auth"), "no_key")

    @patch.object(health_mod, "SERVICE_PROBES", {
        "sonarr": ("sonarr", 8989, "/api/v3/health"),
    })
    @patch.object(health_mod, "AUTH_PROBES", {})
    @patch.object(health_mod, "discover_api_keys", return_value={})
    @patch.object(health_mod, "_get_running_containers", return_value={"radarr"})
    @patch("urllib.request.urlopen")
    def test_probe_disabled_service(self, mock_urlopen, mock_containers, mock_keys):
        """Service not in running set should be reported as disabled."""
        cache = self._make_cache(None)
        result = health_mod.probe_services(cache)
        svc = result["services"].get("sonarr", {})
        self.assertEqual(svc.get("status"), "disabled")
        self.assertEqual(svc.get("auth"), "n/a")


# ---------------------------------------------------------------------------
# append_health_history / get_health_history
# ---------------------------------------------------------------------------


class TestHealthHistory(unittest.TestCase):
    """append_health_history and get_health_history round-trip."""

    def setUp(self):
        self._tmpfile = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self._tmpfile.close()
        self._orig_path = health_mod._HEALTH_HISTORY_PATH
        health_mod._HEALTH_HISTORY_PATH = Path(self._tmpfile.name)
        # Start with no file
        os.unlink(self._tmpfile.name)

    def tearDown(self):
        health_mod._HEALTH_HISTORY_PATH = self._orig_path
        try:
            os.unlink(self._tmpfile.name)
        except FileNotFoundError:
            pass

    def test_append_creates_file(self):
        services = {"sonarr": {"status": "ok", "ms": 42}}
        health_mod.append_health_history(services)
        self.assertTrue(health_mod._HEALTH_HISTORY_PATH.exists())
        data = json.loads(health_mod._HEALTH_HISTORY_PATH.read_text())
        self.assertEqual(len(data), 1)
        self.assertIn("ts", data[0])
        self.assertEqual(data[0]["services"]["sonarr"]["status"], "ok")

    def test_append_then_read(self):
        services = {"sonarr": {"status": "ok", "ms": 10}}
        health_mod.append_health_history(services)
        health_mod.append_health_history(services)
        result = health_mod.get_health_history()
        self.assertEqual(result["entries"], 2)
        self.assertIn("sla", result)
        self.assertEqual(result["sla"]["sonarr"]["total"], 2)
        self.assertEqual(result["sla"]["sonarr"]["ok"], 2)
        self.assertEqual(result["sla"]["sonarr"]["uptime_pct"], 100.0)

    def test_sla_calculation_partial_uptime(self):
        now = time.time()
        history = [
            {"ts": now - 3600, "services": {"app1": {"status": "ok"}}},
            {"ts": now - 1800, "services": {"app1": {"status": "error"}}},
            {"ts": now, "services": {"app1": {"status": "ok"}}},
        ]
        health_mod._HEALTH_HISTORY_PATH.write_text(json.dumps(history))
        result = health_mod.get_health_history()
        self.assertEqual(result["sla"]["app1"]["total"], 3)
        self.assertEqual(result["sla"]["app1"]["ok"], 2)
        self.assertAlmostEqual(result["sla"]["app1"]["uptime_pct"], 66.67, places=2)

    def test_history_capped_at_1440(self):
        entries = [
            {"ts": time.time() - i, "services": {"s": {"status": "ok"}}}
            for i in range(1500)
        ]
        health_mod._HEALTH_HISTORY_PATH.write_text(json.dumps(entries))
        health_mod.append_health_history({"s": {"status": "ok"}})
        data = json.loads(health_mod._HEALTH_HISTORY_PATH.read_text())
        self.assertLessEqual(len(data), 1440)

    def test_get_history_missing_file(self):
        """Missing file should return empty history."""
        result = health_mod.get_health_history()
        self.assertEqual(result["history"], [])
        self.assertEqual(result["period_hours"], 0)

    def test_get_history_empty_file(self):
        """Empty JSON array should return empty history."""
        health_mod._HEALTH_HISTORY_PATH.write_text("[]")
        result = health_mod.get_health_history()
        self.assertEqual(result["history"], [])
        self.assertEqual(result["period_hours"], 0)

    def test_get_history_corrupt_file(self):
        """Corrupt JSON should return empty history gracefully."""
        health_mod._HEALTH_HISTORY_PATH.write_text("{{{NOT JSON")
        result = health_mod.get_health_history()
        self.assertEqual(result["history"], [])
        self.assertEqual(result["period_hours"], 0)

    def test_period_hours_calculation(self):
        now = time.time()
        history = [
            {"ts": now - 7200, "services": {"x": {"status": "ok"}}},
            {"ts": now, "services": {"x": {"status": "ok"}}},
        ]
        health_mod._HEALTH_HISTORY_PATH.write_text(json.dumps(history))
        result = health_mod.get_health_history()
        # First entry is 2 hours ago
        self.assertGreaterEqual(result["period_hours"], 1.9)
        self.assertLessEqual(result["period_hours"], 2.1)


if __name__ == "__main__":
    unittest.main()
