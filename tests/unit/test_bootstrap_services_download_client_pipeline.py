import sys
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from bootstrap_services.download_client_pipeline_service import (  # noqa: E402
    DownloadClientPipelineInputs,
    DownloadClientPipelineService,
)
from bootstrap_services.download_client_adapters.base import (  # noqa: E402
    DownloadClientAdapterBase,
)
from bootstrap_services.enums import RunnerOperation  # noqa: E402


class DownloadClientPipelineServiceTests(unittest.TestCase):
    def _invoke(self):
        handlers = {
            RunnerOperation.QBIT_LOGIN.value: mock.Mock(),
            RunnerOperation.SETUP_QBIT_CATEGORIES.value: mock.Mock(),
            RunnerOperation.READ_SABNZBD_API_KEY.value: mock.Mock(return_value="sab-key"),
            RunnerOperation.ENSURE_SABNZBD_DEFAULTS.value: mock.Mock(),
            RunnerOperation.ENSURE_SABNZBD_CATEGORIES.value: mock.Mock(),
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
                        "bootstrap_services.download_client_adapters.qbittorrent:"
                        "QbittorrentDownloadClientAdapter"
                    ),
                    "sabnzbd": (
                        "bootstrap_services.download_client_adapters.sabnzbd:"
                        "SabnzbdDownloadClientAdapter"
                    ),
                    "transmission": (
                        "bootstrap_services.download_client_adapters.transmission:"
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
        invoke.handlers[RunnerOperation.QBIT_LOGIN.value].assert_called_once()
        invoke.handlers[RunnerOperation.SETUP_QBIT_CATEGORIES.value].assert_called_once()
        invoke.handlers[RunnerOperation.READ_SABNZBD_API_KEY.value].assert_called_once()
        invoke.handlers[RunnerOperation.ENSURE_SABNZBD_DEFAULTS.value].assert_called_once()
        invoke.handlers[RunnerOperation.ENSURE_SABNZBD_CATEGORIES.value].assert_called_once()

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
                                "bootstrap_services.download_client_adapters.sabnzbd:"
                                "SabnzbdDownloadClientAdapter"
                            ),
                        }
                    }
                )
            )

    def test_pipeline_rejects_invalid_download_client_adapter_shape(self):
        invoke = self._invoke()
        service = self._service(invoke)
        with self.assertRaises(ValueError):
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
                            "bootstrap_services.download_client_adapters.transmission:"
                            "TransmissionDownloadClientAdapter"
                        ),
                        "sabnzbd": (
                            "bootstrap_services.download_client_adapters.sabnzbd:"
                            "SabnzbdDownloadClientAdapter"
                        ),
                    }
                }
            )
        )
        self.assertFalse(result.qbit_login_ok)
        invoke.handlers[RunnerOperation.QBIT_LOGIN.value].assert_not_called()
        invoke.handlers[RunnerOperation.SETUP_QBIT_CATEGORIES.value].assert_not_called()

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
        invoke.handlers[RunnerOperation.QBIT_LOGIN.value].assert_not_called()
        invoke.handlers[RunnerOperation.SETUP_QBIT_CATEGORIES.value].assert_not_called()

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
                            "bootstrap_services.download_client_adapters.transmission:"
                            "TransmissionDownloadClientAdapter"
                        ),
                        "sabnzbd": (
                            "bootstrap_services.download_client_adapters.sabnzbd:"
                            "SabnzbdDownloadClientAdapter"
                        ),
                    }
                },
            )
        )
        self.assertFalse(result.qbit_login_ok)
        invoke.handlers[RunnerOperation.QBIT_LOGIN.value].assert_not_called()
        invoke.handlers[RunnerOperation.SETUP_QBIT_CATEGORIES.value].assert_not_called()

    def test_pipeline_requires_explicit_mapping_for_custom_client_module(self):
        invoke = self._invoke()
        service = self._service(invoke)

        module_name = "bootstrap_services.download_client_adapters.my_torrent"
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
                                "bootstrap_services.download_client_adapters.my_torrent:"
                                "MyTorrentDownloadClientAdapter"
                            ),
                            "sabnzbd": (
                                "bootstrap_services.download_client_adapters.sabnzbd:"
                                "SabnzbdDownloadClientAdapter"
                            ),
                        }
                    },
                )
            )

        self.assertTrue(result.qbit_login_ok)
        invoke.handlers[RunnerOperation.QBIT_LOGIN.value].assert_not_called()
        invoke.handlers[RunnerOperation.SETUP_QBIT_CATEGORIES.value].assert_not_called()

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
                            "bootstrap_services.download_client_adapters.qbittorrent:"
                            "QbittorrentDownloadClientAdapter"
                        ),
                        "nzbget": (
                            "bootstrap_services.download_client_adapters.nzbget:"
                            "NzbgetDownloadClientAdapter"
                        ),
                    }
                },
            )
        )
        self.assertTrue(result.qbit_login_ok)
        self.assertEqual(result.sab_api_key, "")
        invoke.handlers[RunnerOperation.QBIT_LOGIN.value].assert_called_once()
        invoke.handlers[RunnerOperation.SETUP_QBIT_CATEGORIES.value].assert_called_once()
        invoke.handlers[RunnerOperation.READ_SABNZBD_API_KEY.value].assert_not_called()


if __name__ == "__main__":
    unittest.main()
