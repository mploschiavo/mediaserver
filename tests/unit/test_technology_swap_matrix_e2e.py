import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from bootstrap_services.enums import BootstrapMode  # noqa: E402
from bootstrap_services.runtime_factory import (  # noqa: E402
    BootstrapCliArgs,
    BootstrapRuntimeFactoryDependencies,
    BootstrapRuntimeFactoryService,
)
from bootstrap_services.download_client_pipeline_service import (  # noqa: E402
    DownloadClientPipelineInputs,
    DownloadClientPipelineService,
)


class TechnologySwapMatrixE2ETests(unittest.TestCase):
    def _factory(self):
        deps = BootstrapRuntimeFactoryDependencies(
            load_bootstrap_default_json=lambda _filename, fallback: fallback,
            deep_merge_objects=lambda base, override: {**dict(base or {}), **dict(override or {})},
            bool_cfg=lambda cfg, key, default=False: bool((cfg or {}).get(key, default)),
            coerce_list=lambda value: (
                value if isinstance(value, list) else ([] if value is None else [value])
            ),
            env_truthy=lambda name, default=False: str(os.environ.get(name, str(default)))
            .strip()
            .lower()
            in {"1", "true", "yes", "on"},
            read_api_key=lambda _config_root, app: f"{app}-key",
            build_sab_remote_path_mappings=lambda _cfg: [],
        )
        return BootstrapRuntimeFactoryService(deps=deps)

    @staticmethod
    def _args() -> BootstrapCliArgs:
        return BootstrapCliArgs(
            mode=BootstrapMode.FULL,
            config_path="bootstrap/media-stack.bootstrap.json",
            config_root="/srv-config",
            wait_timeout=30,
            auto_prowlarr_indexers=False,
        )

    @staticmethod
    def _download_client_entry(key: str) -> dict[str, object]:
        if key == "qbittorrent":
            return {
                "url": "http://qbittorrent:8080",
                "name": "qBittorrent",
                "implementation": "QBittorrent",
                "configure_arr_clients": True,
                "username_env": "STACK_ADMIN_USERNAME",
                "password_env": "STACK_ADMIN_PASSWORD",
                "login_required": True,
            }
        if key == "sabnzbd":
            return {
                "url": "http://sabnzbd:8080",
                "name": "SABnzbd",
                "implementation": "SABnzbd",
                "configure_arr_clients": True,
            }
        if key == "nzbget":
            return {
                "url": "http://nzbget:6789",
                "name": "NZBGet",
                "implementation": "Nzbget",
                "configure_arr_clients": True,
            }
        if key == "jdownloader":
            return {
                "url": "http://jdownloader:5800",
                "name": "JDownloader",
                "implementation": "JDownloader",
                "configure_arr_clients": True,
            }
        if key == "grabit":
            return {
                "url": "http://grabit:9080",
                "name": "Grabit",
                "implementation": "Grabit",
                "configure_arr_clients": True,
            }
        raise AssertionError(f"Unsupported test download client key: {key}")

    def _base_cfg(self, *, usenet_client: str, media_server: str, request_manager: str) -> dict:
        return {
            "config_version": 2,
            "prowlarr_url": "http://prowlarr:9696",
            "arr_apps": [],
            "technology_bindings": {
                "torrent_client": "qbittorrent",
                "usenet_client": usenet_client,
                "media_server": media_server,
                "request_manager": request_manager,
            },
            "download_clients": {
                "qbittorrent": self._download_client_entry("qbittorrent"),
                usenet_client: self._download_client_entry(usenet_client),
            },
        }

    def test_runtime_factory_and_download_pipeline_support_swap_matrix(self):
        factory = self._factory()
        pipeline = DownloadClientPipelineService(
            log=lambda _msg: None,
            normalize_url=lambda value: str(value).rstrip("/"),
            wait_for_service=lambda *_args, **_kwargs: None,
            bool_cfg=lambda cfg, key, default=False: bool((cfg or {}).get(key, default)),
            invoke_operation=lambda operation, *args: (
                True
                if (operation.value if hasattr(operation, "value") else str(operation))
                in {"torrent_client_login", "qbit_login"}
                else (
                    ""
                    if "sab" in (operation.value if hasattr(operation, "value") else str(operation))
                    else None
                )
            ),
        )

        usenet_clients = ["sabnzbd", "nzbget", "jdownloader", "grabit"]
        media_servers = ["jellyfin", "emby", "plex", "mythtv"]

        with mock.patch.dict(
            os.environ,
            {"STACK_ADMIN_USERNAME": "admin", "STACK_ADMIN_PASSWORD": "media-stack-admin"},
            clear=False,
        ):
            for usenet_client in usenet_clients:
                for media_server in media_servers:
                    cfg = self._base_cfg(
                        usenet_client=usenet_client,
                        media_server=media_server,
                        request_manager="openseer",
                    )
                    built = factory.build(self._args(), cfg)
                    rt = built.runtime
                    self.assertEqual(rt.torrent_client_key, "qbittorrent")
                    self.assertEqual(rt.usenet_client_key, usenet_client)
                    self.assertEqual(rt.media_server_backend, media_server)
                    self.assertEqual(rt.request_manager_backend, "openseerr")

                    result = pipeline.run_prepare(
                        DownloadClientPipelineInputs(
                            config_root=rt.config_root,
                            arr_apps_raw=rt.arr_apps_raw,
                            qbit_cfg=rt.qbit_cfg,
                            qbit_username=rt.qb_user,
                            qbit_password=rt.qb_pass,
                            qbit_login_required=rt.qbit_login_required,
                            configure_qbit_arr_clients=rt.configure_qbit_arr_clients,
                            set_qbit_categories=rt.set_qbit_categories,
                            sab_cfg=rt.sab_cfg,
                            configure_sab_arr_clients=rt.configure_sab_arr_clients,
                            fully_preconfigured=rt.fully_preconfigured,
                            wait_timeout=rt.wait_timeout,
                            adapter_hooks_cfg=rt.adapter_hooks_cfg,
                            torrent_client_key=rt.torrent_client_key,
                            usenet_client_key=rt.usenet_client_key,
                        )
                    )
                    self.assertTrue(result.qbit_login_ok)


if __name__ == "__main__":
    unittest.main()
