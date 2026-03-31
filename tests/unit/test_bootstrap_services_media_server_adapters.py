import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from bootstrap_services.enums import RunnerOperation  # noqa: E402
from bootstrap_services.media_server_adapters import (  # noqa: E402
    GenericMediaServerAdapter,
    JellyfinMediaServerAdapter,
    MediaServerAdapterBase,
    MediaServerAdapterContext,
    MediaServerAdapterFactory,
)


class MediaServerAdaptersTests(unittest.TestCase):
    def _runtime(self):
        return SimpleNamespace(
            cfg={},
            config_root="/srv-config",
            wait_timeout=30,
            adapter_hooks_cfg={},
            configure_jellyfin_livetv=True,
            jellyfin_livetv_required=False,
            configure_jellyfin_libraries=True,
            jellyfin_libraries_required=False,
            configure_jellyfin_plugins=True,
            jellyfin_plugins_required=False,
            configure_jellyfin_playback=True,
            jellyfin_playback_required=False,
            configure_jellyfin_home_rails=True,
            jellyfin_home_rails_required=False,
            configure_auto_collections=True,
            auto_collections_required=False,
            configure_jellyfin_prewarm=True,
            jellyfin_prewarm_required=False,
        )

    def _context(self, runtime=None):
        calls = []

        def invoke(op, *args, **kwargs):
            key = op.value if hasattr(op, "value") else str(op)
            calls.append(("invoke", key, args, kwargs))
            return None

        def run_optional(*, enabled, required, action, warning_message):
            calls.append(("optional", enabled, required, warning_message))
            if enabled:
                action()

        return (
            MediaServerAdapterContext(
                backend="jellyfin",
                runtime=runtime or self._runtime(),
                invoke_operation=invoke,
                run_optional_step=run_optional,
                log=mock.Mock(),
            ),
            calls,
        )

    def test_factory_maps_jellyfin_adapter(self):
        ctx, _calls = self._context()
        adapter = MediaServerAdapterFactory().create("jellyfin", ctx)
        self.assertIsInstance(adapter, JellyfinMediaServerAdapter)

    def test_factory_can_disable_mapping(self):
        ctx, _calls = self._context()
        adapter = MediaServerAdapterFactory(adapter_class_specs={"jellyfin": ""}).create(
            "jellyfin", ctx
        )
        self.assertIsInstance(adapter, GenericMediaServerAdapter)

    def test_factory_supports_custom_backend_key_via_reflection_mapping(self):
        ctx, _calls = self._context()
        adapter = MediaServerAdapterFactory(
            adapter_class_specs={
                "my-media": "bootstrap_services.media_server_adapters.jellyfin:JellyfinMediaServerAdapter"
            }
        ).create("my-media", ctx)
        self.assertIsInstance(adapter, JellyfinMediaServerAdapter)

    def test_factory_supports_convention_discovery_for_custom_backend_module(self):
        module_name = "bootstrap_services.media_server_adapters.my_media"
        fake_module = types.ModuleType(module_name)

        class MyMediaServerAdapter(MediaServerAdapterBase):
            pass

        fake_module.MyMediaServerAdapter = MyMediaServerAdapter
        ctx, _calls = self._context()

        with mock.patch.dict(sys.modules, {module_name: fake_module}):
            adapter = MediaServerAdapterFactory().create("my-media", ctx)

        self.assertIsInstance(adapter, MyMediaServerAdapter)

    def test_jellyfin_adapter_invokes_prewarm_operation(self):
        ctx, calls = self._context()
        adapter = JellyfinMediaServerAdapter(context=ctx)
        adapter.run_prewarm_mode()
        invoke_ops = [entry[1] for entry in calls if entry[0] == "invoke"]
        self.assertIn(RunnerOperation.ENSURE_JELLYFIN_PREWARM.value, invoke_ops)

    def test_jellyfin_adapter_runs_post_steps(self):
        ctx, calls = self._context()
        adapter = JellyfinMediaServerAdapter(context=ctx)
        adapter.run_post_servarr_pre_hygiene_steps()
        adapter.run_post_servarr_post_hygiene_steps()
        invoke_ops = [entry[1] for entry in calls if entry[0] == "invoke"]
        self.assertIn(RunnerOperation.ENSURE_JELLYFIN_LIVETV.value, invoke_ops)
        self.assertIn(RunnerOperation.ENSURE_JELLYFIN_LIBRARIES.value, invoke_ops)
        self.assertIn(RunnerOperation.ENSURE_JELLYFIN_PLUGINS.value, invoke_ops)
        self.assertIn(RunnerOperation.ENSURE_JELLYFIN_PLAYBACK.value, invoke_ops)
        self.assertIn(RunnerOperation.ENSURE_JELLYFIN_HOME_RAILS.value, invoke_ops)
        self.assertIn(RunnerOperation.ENSURE_JELLYFIN_AUTO_COLLECTIONS.value, invoke_ops)
        self.assertIn(RunnerOperation.ENSURE_JELLYFIN_PREWARM.value, invoke_ops)

    def test_jellyfin_adapter_supports_config_driven_operation_plan(self):
        runtime = self._runtime()
        runtime.adapter_hooks_cfg = {
            "media_server_operation_plans": {
                "jellyfin": {
                    "prewarm_mode": {
                        "steps": [
                            {
                                "operation": "custom_media_prewarm",
                                "args": ["cfg", "config_root", "wait_timeout"],
                            }
                        ]
                    }
                }
            }
        }
        ctx, calls = self._context(runtime=runtime)
        adapter = JellyfinMediaServerAdapter(context=ctx)
        adapter.run_prewarm_mode()
        invoke_ops = [entry[1] for entry in calls if entry[0] == "invoke"]
        self.assertIn("custom_media_prewarm", invoke_ops)


if __name__ == "__main__":
    unittest.main()
