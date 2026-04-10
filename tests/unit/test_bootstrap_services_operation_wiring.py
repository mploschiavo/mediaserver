import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.services.operation_wiring import (  # noqa: E402
    RunnerOperationHandlers,
    build_runner_operation_registry,
)


def _noop(*_args, **_kwargs):
    return None


class OperationWiringTests(unittest.TestCase):
    def _handlers(self):
        return RunnerOperationHandlers(
            ensure_app_auth_settings=_noop,
            torrent_client_login=_noop,
            read_sabnzbd_api_key=_noop,
            ensure_sabnzbd_defaults=_noop,
            ensure_sabnzbd_categories=_noop,
            setup_torrent_categories=_noop,
            run_servarr_pipeline=_noop,
            ensure_bazarr_arr_integration=_noop,
            configure_request_manager=_noop,
            ensure_jellyfin_livetv=_noop,
            ensure_jellyfin_libraries=_noop,
            ensure_jellyfin_plugins=_noop,
            ensure_jellyfin_playback_defaults=_noop,
            ensure_jellyfin_home_rails=_noop,
            ensure_jellyfin_auto_collections_config=_noop,
            enforce_disk_guardrails=_noop,
            run_media_hygiene=_noop,
            ensure_jellyfin_prewarm=_noop,
            ensure_maintainerr_policy=_noop,
            ensure_maintainerr_integrations=_noop,
            ensure_homepage_services_config=_noop,
            ensure_prowlarr_ready=_noop,
            ensure_prowlarr_flaresolverr_proxy=_noop,
            ensure_prowlarr_indexer=_noop,
            auto_add_tested_indexers=_noop,
            trigger_prowlarr_sync=_noop,
            sync_arr_indexers_from_prowlarr=_noop,
            run_prowlarr_indexer_pipeline=_noop,
        )

    def test_build_registry_wires_default_operations(self):
        registry = build_runner_operation_registry(self._handlers())
        self.assertIsNone(registry.invoke("ensure_jellyfin_prewarm"))
        self.assertIsNone(registry.invoke("ensure_maintainerr_integrations"))
        self.assertIsNone(registry.invoke("ensure_prowlarr_flaresolverr_proxy"))
        self.assertIsNone(registry.invoke("run_servarr_pipeline"))

    def test_build_registry_accepts_reflection_overrides(self):
        registry = build_runner_operation_registry(
            self._handlers(),
            operation_handler_specs={"custom_math_ceil": "math:ceil"},
        )
        self.assertEqual(registry.invoke("custom_math_ceil", 1.1), 2)


if __name__ == "__main__":
    unittest.main()
