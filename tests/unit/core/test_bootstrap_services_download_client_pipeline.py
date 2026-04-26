import sys
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.services.download_client_pipeline_service import (  # noqa: E402
    DownloadClientPipelineInputs,
    DownloadClientPipelineService,
)
from media_stack.services.download_client_adapters.base import (  # noqa: E402
    DownloadClientAdapterBase,
)

TORRENT_CLIENT_LOGIN = "torrent_client_login"
QBIT_LOGIN = "qbit_login"
SETUP_TORRENT_CATEGORIES = "setup_torrent_categories"
SETUP_QBIT_CATEGORIES = "setup_qbit_categories"
READ_SABNZBD_API_KEY = "read_sabnzbd_api_key"
ENSURE_SABNZBD_DEFAULTS = "ensure_sabnzbd_defaults"
ENSURE_SABNZBD_CATEGORIES = "ensure_sabnzbd_categories"


class DownloadClientPipelineServiceTests(unittest.TestCase):
    def _invoke(self):
        torrent_login = mock.Mock()
        setup_torrent_categories = mock.Mock()
        handlers = {
            TORRENT_CLIENT_LOGIN: torrent_login,
            QBIT_LOGIN: torrent_login,
            SETUP_TORRENT_CATEGORIES: setup_torrent_categories,
            SETUP_QBIT_CATEGORIES: setup_torrent_categories,
            READ_SABNZBD_API_KEY: mock.Mock(return_value="sab-key"),
            ENSURE_SABNZBD_DEFAULTS: mock.Mock(),
            ENSURE_SABNZBD_CATEGORIES: mock.Mock(),
        }

        def invoke(operation, *args, **kwargs):
            key = operation.value if hasattr(operation, "value") else str(operation)
            handler = handlers.get(key)
            if handler is None:
                raise KeyError(key)
            return handler(*args, **kwargs)

        invoke.handlers = handlers  # type: ignore[attr-defined]
        return invoke

    def _service(self, invoke):
        return DownloadClientPipelineService(
            log=mock.Mock(),
            normalize_url=lambda value: str(value).rstrip("/"),
            wait_for_service=mock.Mock(),
            bool_cfg=lambda cfg, key, default=False: bool((cfg or {}).get(key, default)),
            invoke_operation=invoke,
        )

    def _inputs(self, **overrides):
        base = dict(
            config_root="/srv-config",
            arr_apps_raw=[],
            qbit_cfg={"url": "http://qbittorrent:8080"},
            qbit_username="admin",
            qbit_password="secret",
            qbit_login_required=True,
            configure_qbit_arr_clients=True,
            set_qbit_categories=True,
            sab_cfg={"url": "http://sabnzbd:8080"},
            configure_sab_arr_clients=True,
            fully_preconfigured=True,
            wait_timeout=30,
            adapter_hooks_cfg={
                "download_client_adapter_classes": {
                    "qbittorrent": (
                        "media_stack.services.apps.qbittorrent.download_client_adapter:"
                        "QbittorrentDownloadClientAdapter"
                    ),
                    "sabnzbd": (
                        "media_stack.services.apps.sabnzbd.download_client_adapter:"
                        "SabnzbdDownloadClientAdapter"
                    ),
                    "transmission": (
                        "media_stack.services.download_client_adapters.transmission:"
                        "TransmissionDownloadClientAdapter"
                    ),
                }
            },
            torrent_client_key="qbittorrent",
            usenet_client_key="sabnzbd",
        )
        base.update(overrides)
        return DownloadClientPipelineInputs(**base)

    def test_pipeline_runs_qbit_and_sab_adapters(self):
        invoke = self._invoke()
        service = self._service(invoke)
        result = service.run_prepare(self._inputs())

        self.assertTrue(result.qbit_login_ok)
        self.assertEqual(result.sab_api_key, "sab-key")
        invoke.handlers[TORRENT_CLIENT_LOGIN].assert_called_once()
        invoke.handlers[SETUP_TORRENT_CATEGORIES].assert_called_once()
        invoke.handlers[READ_SABNZBD_API_KEY].assert_called_once()
        invoke.handlers[ENSURE_SABNZBD_DEFAULTS].assert_called_once()
        invoke.handlers[ENSURE_SABNZBD_CATEGORIES].assert_called_once()

    def test_pipeline_rejects_disabled_qbit_adapter_mapping(self):
        invoke = self._invoke()
        service = self._service(invoke)
        with self.assertRaises(ValueError):
            service.run_prepare(
                self._inputs(
                    adapter_hooks_cfg={
                        "download_client_adapter_classes": {
                            "qbittorrent": "",
                            "sabnzbd": (
                                "media_stack.services.apps.sabnzbd.download_client_adapter:"
                                "SabnzbdDownloadClientAdapter"
                            ),
                        }
                    }
                )
            )

    def test_pipeline_rejects_invalid_download_client_adapter_shape(self):
        invoke = self._invoke()
        service = self._service(invoke)
        with self.assertRaises((ValueError, AttributeError)):
            service.run_prepare(
                self._inputs(
                    adapter_hooks_cfg={
                        "download_client_adapter_classes": "invalid",
                    }
                )
            )

    def test_pipeline_can_swap_qbit_to_transmission_adapter(self):
        invoke = self._invoke()
        service = self._service(invoke)
        result = service.run_prepare(
            self._inputs(
                adapter_hooks_cfg={
                    "download_client_adapter_classes": {
                        "qbittorrent": (
                            "media_stack.services.download_client_adapters.transmission:"
                            "TransmissionDownloadClientAdapter"
                        ),
                        "sabnzbd": (
                            "media_stack.services.apps.sabnzbd.download_client_adapter:"
                            "SabnzbdDownloadClientAdapter"
                        ),
                    }
                }
            )
        )
        self.assertFalse(result.qbit_login_ok)
        invoke.handlers[TORRENT_CLIENT_LOGIN].assert_not_called()
        invoke.handlers[SETUP_TORRENT_CATEGORIES].assert_not_called()

    def test_pipeline_uses_active_transmission_key_from_bindings(self):
        invoke = self._invoke()
        service = self._service(invoke)
        result = service.run_prepare(
            self._inputs(
                torrent_client_key="transmission",
                qbit_cfg={
                    "url": "http://transmission:9091",
                    "name": "Transmission",
                    "configure_arr_clients": True,
                },
            )
        )
        self.assertFalse(result.qbit_login_ok)
        invoke.handlers[TORRENT_CLIENT_LOGIN].assert_not_called()
        invoke.handlers[SETUP_TORRENT_CATEGORIES].assert_not_called()

    def test_pipeline_supports_custom_torrent_key_via_reflection_mapping(self):
        invoke = self._invoke()
        service = self._service(invoke)
        result = service.run_prepare(
            self._inputs(
                torrent_client_key="mytorrent",
                qbit_cfg={
                    "url": "http://mytorrent:9091",
                    "name": "MyTorrent",
                    "configure_arr_clients": True,
                },
                adapter_hooks_cfg={
                    "download_client_adapter_classes": {
                        "mytorrent": (
                            "media_stack.services.download_client_adapters.transmission:"
                            "TransmissionDownloadClientAdapter"
                        ),
                        "sabnzbd": (
                            "media_stack.services.apps.sabnzbd.download_client_adapter:"
                            "SabnzbdDownloadClientAdapter"
                        ),
                    }
                },
            )
        )
        self.assertFalse(result.qbit_login_ok)
        invoke.handlers[TORRENT_CLIENT_LOGIN].assert_not_called()
        invoke.handlers[SETUP_TORRENT_CATEGORIES].assert_not_called()

    def test_pipeline_requires_explicit_mapping_for_custom_client_module(self):
        invoke = self._invoke()
        service = self._service(invoke)

        module_name = "media_stack.services.download_client_adapters.my_torrent"
        fake_module = types.ModuleType(module_name)

        class MyTorrentDownloadClientAdapter(DownloadClientAdapterBase):
            def prepare(self) -> None:
                self.context.status["login_ok"] = True

        fake_module.MyTorrentDownloadClientAdapter = MyTorrentDownloadClientAdapter

        with mock.patch.dict(sys.modules, {module_name: fake_module}):
            result = service.run_prepare(
                self._inputs(
                    torrent_client_key="my-torrent",
                    qbit_cfg={
                        "url": "http://my-torrent:9091",
                        "name": "My Torrent",
                        "configure_arr_clients": True,
                    },
                    adapter_hooks_cfg={
                        "download_client_adapter_classes": {
                            "my-torrent": (
                                "media_stack.services.download_client_adapters.my_torrent:"
                                "MyTorrentDownloadClientAdapter"
                            ),
                            "sabnzbd": (
                                "media_stack.services.apps.sabnzbd.download_client_adapter:"
                                "SabnzbdDownloadClientAdapter"
                            ),
                        }
                    },
                )
            )

        self.assertTrue(result.qbit_login_ok)
        invoke.handlers[TORRENT_CLIENT_LOGIN].assert_not_called()
        invoke.handlers[SETUP_TORRENT_CATEGORIES].assert_not_called()

    def test_pipeline_supports_nzbget_as_active_usenet_client(self):
        invoke = self._invoke()
        service = self._service(invoke)
        result = service.run_prepare(
            self._inputs(
                usenet_client_key="nzbget",
                sab_cfg={
                    "url": "http://nzbget:6789",
                    "name": "NZBGet",
                    "configure_arr_clients": True,
                    "implementation": "Nzbget",
                },
                adapter_hooks_cfg={
                    "download_client_adapter_classes": {
                        "qbittorrent": (
                            "media_stack.services.apps.qbittorrent.download_client_adapter:"
                            "QbittorrentDownloadClientAdapter"
                        ),
                        "nzbget": (
                            "media_stack.services.download_client_adapters.nzbget:"
                            "NzbgetDownloadClientAdapter"
                        ),
                    }
                },
            )
        )
        self.assertTrue(result.qbit_login_ok)
        self.assertEqual(result.sab_api_key, "")
        invoke.handlers[TORRENT_CLIENT_LOGIN].assert_called_once()
        invoke.handlers[SETUP_TORRENT_CATEGORIES].assert_called_once()
        invoke.handlers[READ_SABNZBD_API_KEY].assert_not_called()

    def test_pipeline_allows_missing_usenet_binding(self):
        invoke = self._invoke()
        service = self._service(invoke)
        result = service.run_prepare(
            self._inputs(
                usenet_client_key="",
                sab_cfg={},
                configure_sab_arr_clients=False,
            )
        )
        self.assertTrue(result.qbit_login_ok)
        self.assertEqual(result.sab_api_key, "")
        invoke.handlers[TORRENT_CLIENT_LOGIN].assert_called_once()
        invoke.handlers[READ_SABNZBD_API_KEY].assert_not_called()


if __name__ == "__main__":
    unittest.main()
