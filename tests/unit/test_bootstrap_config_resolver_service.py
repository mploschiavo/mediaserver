import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from cli.bootstrap_config_resolver_service import (  # noqa: E402
    BootstrapConfigResolverConfig,
    BootstrapConfigResolverService,
)
from core.exceptions import ConfigError


class _Result:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _Kube:
    cmd_prefix = ["kubectl"]

    def __init__(self, hosts_stdout: str) -> None:
        self.hosts_stdout = hosts_stdout
        self.calls: list[list[str]] = []

    def run(self, args, **_kwargs):
        cmd = list(args)
        self.calls.append(cmd)
        if cmd[:5] == ["-n", "media-stack", "get", "ingress", "media-stack-ingress"]:
            return _Result(0, self.hosts_stdout)
        return _Result(1, "", "unexpected command")


class BootstrapConfigResolverServiceTests(unittest.TestCase):
    def test_resolve_injects_hosts_into_homepage(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            config_path = base / "bootstrap.json"
            job_config_path = base / "job-config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "config_version": 2,
                        "prowlarr_url": "http://prowlarr:9696",
                        "adapter_hooks": {
                            "bootstrap_job": {
                                "config_resolver": {
                                    "operations": ["inject_homepage_ingress_hosts"],
                                    "ingress_host_targets": [
                                        {
                                            "name": "homepage",
                                            "hosts_path": "homepage.hosts",
                                            "enable_path": "homepage.enabled",
                                            "enable_value": True,
                                        }
                                    ],
                                }
                            }
                        },
                        "arr_apps": [],
                        "download_clients": {
                            "qbittorrent": {
                                "url": "http://qbittorrent:8080",
                                "host": "qbittorrent",
                                "port": 8080,
                                "name": "qBittorrent",
                                "implementation": "qBittorrent",
                            },
                            "sabnzbd": {
                                "url": "http://sabnzbd:8080",
                                "host": "sabnzbd",
                                "port": 8080,
                                "name": "SABnzbd",
                                "implementation": "SABnzbd",
                            },
                        },
                        "technology_bindings": {
                            "torrent_client": "qbittorrent",
                            "usenet_client": "sabnzbd",
                            "media_server": "jellyfin",
                        },
                        "homepage": {"enabled": False},
                    }
                ),
                encoding="utf-8",
            )
            kube = _Kube("jellyfin.local\nsonarr.local\njellyfin.local\n")
            svc = BootstrapConfigResolverService(
                cfg=BootstrapConfigResolverConfig(
                    namespace="media-stack",
                    ingress_name="media-stack-ingress",
                    config_file=config_path,
                    job_config_file=job_config_path,
                ),
                kube=kube,
                info=mock.Mock(),
            )

            svc.resolve_bootstrap_config()
            written = json.loads(job_config_path.read_text(encoding="utf-8"))
            self.assertEqual(
                written["homepage"]["hosts"],
                ["jellyfin.local", "sonarr.local"],
            )
            self.assertTrue(written["homepage"]["enabled"])

    def test_resolve_without_targets_skips_ingress_query_and_keeps_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            config_path = base / "bootstrap.json"
            job_config_path = base / "job-config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "config_version": 2,
                        "prowlarr_url": "http://prowlarr:9696",
                        "arr_apps": [],
                        "download_clients": {
                            "qbittorrent": {
                                "url": "http://qbittorrent:8080",
                                "host": "qbittorrent",
                                "port": 8080,
                                "name": "qBittorrent",
                                "implementation": "qBittorrent",
                            },
                            "sabnzbd": {
                                "url": "http://sabnzbd:8080",
                                "host": "sabnzbd",
                                "port": 8080,
                                "name": "SABnzbd",
                                "implementation": "SABnzbd",
                            },
                        },
                        "technology_bindings": {
                            "torrent_client": "qbittorrent",
                            "usenet_client": "sabnzbd",
                            "media_server": "jellyfin",
                        },
                        "homepage": {"enabled": False},
                    }
                ),
                encoding="utf-8",
            )
            kube = _Kube("jellyfin.local\nsonarr.local\n")
            svc = BootstrapConfigResolverService(
                cfg=BootstrapConfigResolverConfig(
                    namespace="media-stack",
                    ingress_name="media-stack-ingress",
                    config_file=config_path,
                    job_config_file=job_config_path,
                ),
                kube=kube,
                info=mock.Mock(),
            )

            svc.resolve_bootstrap_config()
            written = json.loads(job_config_path.read_text(encoding="utf-8"))
            self.assertEqual(written["homepage"], {"enabled": False})
            self.assertEqual(kube.calls, [])

    def test_unknown_config_resolver_operation_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            config_path = base / "bootstrap.json"
            job_config_path = base / "job-config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "config_version": 2,
                        "prowlarr_url": "http://prowlarr:9696",
                        "adapter_hooks": {
                            "bootstrap_job": {
                                "config_resolver": {
                                    "operations": ["missing_operation"],
                                }
                            }
                        },
                        "arr_apps": [],
                        "download_clients": {
                            "qbittorrent": {
                                "url": "http://qbittorrent:8080",
                                "host": "qbittorrent",
                                "port": 8080,
                                "name": "qBittorrent",
                                "implementation": "qBittorrent",
                            },
                            "sabnzbd": {
                                "url": "http://sabnzbd:8080",
                                "host": "sabnzbd",
                                "port": 8080,
                                "name": "SABnzbd",
                                "implementation": "SABnzbd",
                            },
                        },
                        "technology_bindings": {
                            "torrent_client": "qbittorrent",
                            "usenet_client": "sabnzbd",
                            "media_server": "jellyfin",
                        },
                        "homepage": {"enabled": False},
                    }
                ),
                encoding="utf-8",
            )
            kube = _Kube("jellyfin.local\nsonarr.local\n")
            svc = BootstrapConfigResolverService(
                cfg=BootstrapConfigResolverConfig(
                    namespace="media-stack",
                    ingress_name="media-stack-ingress",
                    config_file=config_path,
                    job_config_file=job_config_path,
                ),
                kube=kube,
                info=mock.Mock(),
            )

            with self.assertRaises(ConfigError):
                svc.resolve_bootstrap_config()


if __name__ == "__main__":
    unittest.main()
