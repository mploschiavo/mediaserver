import sys
import unittest
from pathlib import Path
from urllib import parse

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from bootstrap_services.apps.sabnzbd.service import SabnzbdService  # noqa: E402


class SabnzbdServiceTests(unittest.TestCase):
    def _service(self, http_request, logs: list[str]) -> SabnzbdService:
        return SabnzbdService(
            http_request=http_request,
            normalize_url=lambda value: str(value or "").strip(),
            normalize_mapping_path=lambda value: str(value or "").strip(),
            choose_category=lambda _app, _cfg: "tv",
            coerce_list=lambda value: list(value or []),
            resolve_path=lambda root, rel: Path(root) / str(rel),
            log=logs.append,
        )

    def test_ensure_defaults_seeds_placeholder_server_when_empty(self):
        calls: list[tuple[str, str, dict[str, list[str]]]] = []
        logs: list[str] = []

        def http_request(base_url, path, timeout=20):
            del base_url, timeout
            parsed = parse.urlparse(path)
            qs = parse.parse_qs(parsed.query, keep_blank_values=True)
            mode = str((qs.get("mode") or [""])[0]).strip()
            section = str((qs.get("section") or [""])[0]).strip()
            calls.append((mode, section, qs))
            if mode == "get_config" and section == "misc":
                return (
                    200,
                    {
                        "config": {
                            "misc": {
                                "download_dir": "/data/usenet/incomplete",
                                "complete_dir": "/data/usenet/completed",
                                "auto_browser": "0",
                            }
                        }
                    },
                    "",
                )
            if mode == "get_config" and section == "servers":
                return 200, {"config": {}}, ""
            if mode == "set_config" and section == "servers":
                return 200, {"config": {"servers": [{"name": "bootstrap-placeholder"}]}}, ""
            raise AssertionError(f"Unexpected request: mode={mode} section={section} path={path}")

        service = self._service(http_request, logs)
        service.ensure_defaults(
            {
                "url": "http://sabnzbd:8080",
                "incomplete_dir": "/data/usenet/incomplete",
                "complete_dir": "/data/usenet/completed",
                "auto_browser": False,
                "seed_placeholder_server_if_empty": True,
                "placeholder_server": {
                    "name": "bootstrap-placeholder",
                    "displayname": "Bootstrap Placeholder",
                    "host": "news.invalid",
                    "port": 119,
                    "connections": 1,
                    "enable": False,
                    "required": False,
                    "optional": True,
                    "ssl": False,
                },
            },
            "sab-api-key",
        )

        server_seed_calls = [
            item for item in calls if item[0] == "set_config" and item[1] == "servers"
        ]
        self.assertEqual(len(server_seed_calls), 1)
        qs = server_seed_calls[0][2]
        self.assertEqual((qs.get("name") or [""])[0], "bootstrap-placeholder")
        self.assertEqual((qs.get("host") or [""])[0], "news.invalid")
        self.assertEqual((qs.get("enable") or [""])[0], "0")
        self.assertEqual((qs.get("optional") or [""])[0], "1")
        self.assertTrue(any("seeded placeholder server" in line for line in logs))

    def test_ensure_defaults_skips_placeholder_when_servers_exist(self):
        calls: list[tuple[str, str, dict[str, list[str]]]] = []
        logs: list[str] = []

        def http_request(base_url, path, timeout=20):
            del base_url, timeout
            parsed = parse.urlparse(path)
            qs = parse.parse_qs(parsed.query, keep_blank_values=True)
            mode = str((qs.get("mode") or [""])[0]).strip()
            section = str((qs.get("section") or [""])[0]).strip()
            calls.append((mode, section, qs))
            if mode == "get_config" and section == "misc":
                return (
                    200,
                    {
                        "config": {
                            "misc": {
                                "download_dir": "/data/usenet/incomplete",
                                "complete_dir": "/data/usenet/completed",
                                "auto_browser": "0",
                            }
                        }
                    },
                    "",
                )
            if mode == "get_config" and section == "servers":
                return 200, {"config": {"servers": [{"name": "real-server"}]}}, ""
            raise AssertionError(f"Unexpected request: mode={mode} section={section} path={path}")

        service = self._service(http_request, logs)
        service.ensure_defaults(
            {
                "url": "http://sabnzbd:8080",
                "incomplete_dir": "/data/usenet/incomplete",
                "complete_dir": "/data/usenet/completed",
                "auto_browser": False,
                "seed_placeholder_server_if_empty": True,
            },
            "sab-api-key",
        )

        server_seed_calls = [
            item for item in calls if item[0] == "set_config" and item[1] == "servers"
        ]
        self.assertEqual(server_seed_calls, [])
        self.assertTrue(any("server list already configured" in line for line in logs))

    def test_ensure_defaults_warns_when_empty_and_placeholder_seed_disabled(self):
        calls: list[tuple[str, str, dict[str, list[str]]]] = []
        logs: list[str] = []

        def http_request(base_url, path, timeout=20):
            del base_url, timeout
            parsed = parse.urlparse(path)
            qs = parse.parse_qs(parsed.query, keep_blank_values=True)
            mode = str((qs.get("mode") or [""])[0]).strip()
            section = str((qs.get("section") or [""])[0]).strip()
            calls.append((mode, section, qs))
            if mode == "get_config" and section == "misc":
                return (
                    200,
                    {
                        "config": {
                            "misc": {
                                "download_dir": "/data/usenet/incomplete",
                                "complete_dir": "/data/usenet/completed",
                                "auto_browser": "0",
                            }
                        }
                    },
                    "",
                )
            if mode == "get_config" and section == "servers":
                return 200, {"config": {}}, ""
            raise AssertionError(f"Unexpected request: mode={mode} section={section} path={path}")

        service = self._service(http_request, logs)
        service.ensure_defaults(
            {
                "url": "http://sabnzbd:8080",
                "incomplete_dir": "/data/usenet/incomplete",
                "complete_dir": "/data/usenet/completed",
                "auto_browser": False,
                "seed_placeholder_server_if_empty": False,
            },
            "sab-api-key",
        )

        server_seed_calls = [
            item for item in calls if item[0] == "set_config" and item[1] == "servers"
        ]
        self.assertEqual(server_seed_calls, [])
        self.assertTrue(any("/wizard/" in line for line in logs))


if __name__ == "__main__":
    unittest.main()
