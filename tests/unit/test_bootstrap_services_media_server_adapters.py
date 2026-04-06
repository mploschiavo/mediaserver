import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.services.media_server_adapters import (  # noqa: E402
    EmbyMediaServerAdapter,
    JellyfinMediaServerAdapter,
    MediaServerAdapterBase,
    MediaServerAdapterContext,
    MediaServerAdapterFactory,
    MythTvMediaServerAdapter,
    PlexMediaServerAdapter,
)

ENSURE_JELLYFIN_PREWARM = "ensure_jellyfin_prewarm"
ENSURE_JELLYFIN_LIVETV = "ensure_jellyfin_livetv"
ENSURE_JELLYFIN_LIBRARIES = "ensure_jellyfin_libraries"
ENSURE_JELLYFIN_PLUGINS = "ensure_jellyfin_plugins"
ENSURE_JELLYFIN_PLAYBACK = "ensure_jellyfin_playback_defaults"
ENSURE_JELLYFIN_HOME_RAILS = "ensure_jellyfin_home_rails"
ENSURE_JELLYFIN_AUTO_COLLECTIONS = "ensure_jellyfin_auto_collections_config"


class MediaServerAdaptersTests(unittest.TestCase):
    def _runtime(self):
        return SimpleNamespace(
            cfg={},
            config_root="/srv-config",
            wait_timeout=30,
            adapter_hooks_cfg={
                "media_server_operation_plans": {
                    "jellyfin": {
                        "prewarm_mode": {
                            "steps": [
                                {
                                    "operation": "ensure_jellyfin_prewarm",
                                    "args": ["cfg", "config_root", "wait_timeout"],
                                }
                            ]
                        },
                        "home_rails_mode": {
                            "steps": [
                                {
                                    "operation": "ensure_jellyfin_home_rails",
                                    "args": ["cfg", "config_root", "wait_timeout"],
                                }
                            ]
                        },
                        "post_servarr_pre_hygiene_steps": {
                            "steps": [
                                {
                                    "operation": "ensure_jellyfin_livetv",
                                    "args": ["cfg", "config_root", "wait_timeout"],
                                    "enabled_attr": "configure_jellyfin_livetv",
                                    "required_attr": "jellyfin_livetv_required",
                                },
                                {
                                    "operation": "ensure_jellyfin_libraries",
                                    "args": ["cfg", "config_root", "wait_timeout"],
                                    "enabled_attr": "configure_jellyfin_libraries",
                                    "required_attr": "jellyfin_libraries_required",
                                },
                                {
                                    "operation": "ensure_jellyfin_plugins",
                                    "args": ["cfg", "config_root", "wait_timeout"],
                                    "enabled_attr": "configure_jellyfin_plugins",
                                    "required_attr": "jellyfin_plugins_required",
                                },
                                {
                                    "operation": "ensure_jellyfin_playback_defaults",
                                    "args": ["cfg", "config_root", "wait_timeout"],
                                    "enabled_attr": "configure_jellyfin_playback",
                                    "required_attr": "jellyfin_playback_required",
                                },
                                {
                                    "operation": "ensure_jellyfin_home_rails",
                                    "args": ["cfg", "config_root", "wait_timeout"],
                                    "enabled_attr": "configure_jellyfin_home_rails",
                                    "required_attr": "jellyfin_home_rails_required",
                                },
                                {
                                    "operation": "ensure_jellyfin_auto_collections_config",
                                    "args": ["cfg", "config_root", "wait_timeout"],
                                    "enabled_attr": "configure_auto_collections",
                                    "required_attr": "auto_collections_required",
                                },
                            ]
                        },
                        "post_servarr_post_hygiene_steps": {
                            "steps": [
                                {
                                    "operation": "ensure_jellyfin_prewarm",
                                    "args": ["cfg", "config_root", "wait_timeout"],
                                    "enabled_attr": "configure_jellyfin_prewarm",
                                    "required_attr": "jellyfin_prewarm_required",
                                }
                            ]
                        },
                    }
                }
            },
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

    def test_factory_maps_alt_media_server_adapters_from_manifests(self):
        ctx, _calls = self._context()
        self.assertIsInstance(
            MediaServerAdapterFactory().create("emby", ctx), EmbyMediaServerAdapter
        )
        self.assertIsInstance(
            MediaServerAdapterFactory().create("plex", ctx), PlexMediaServerAdapter
        )
        self.assertIsInstance(
            MediaServerAdapterFactory().create("mythtv", ctx),
            MythTvMediaServerAdapter,
        )

    def test_factory_rejects_unregistered_backend(self):
        ctx, _calls = self._context()
        with self.assertRaises(ValueError):
            MediaServerAdapterFactory(adapter_class_specs={"jellyfin": ""}).create("jellyfin", ctx)

    def test_factory_supports_custom_backend_key_via_reflection_mapping(self):
        ctx, _calls = self._context()
        adapter = MediaServerAdapterFactory(
            adapter_class_specs={
                "my-media": "media_stack.services.media_server_adapters.jellyfin:JellyfinMediaServerAdapter"
            }
        ).create("my-media", ctx)
        self.assertIsInstance(adapter, JellyfinMediaServerAdapter)

    def test_factory_requires_explicit_mapping_for_custom_backend(self):
        module_name = "media_stack.services.media_server_adapters.my_media"
        fake_module = types.ModuleType(module_name)

        class MyMediaServerAdapter(MediaServerAdapterBase):
            pass

        fake_module.MyMediaServerAdapter = MyMediaServerAdapter
        ctx, _calls = self._context()

        with mock.patch.dict(sys.modules, {module_name: fake_module}):
            adapter = MediaServerAdapterFactory(
                adapter_class_specs={
                    "my-media": (
                        "media_stack.services.media_server_adapters.my_media:MyMediaServerAdapter"
                    )
                }
            ).create("my-media", ctx)

        self.assertIsInstance(adapter, MyMediaServerAdapter)

    def test_jellyfin_adapter_invokes_prewarm_operation(self):
        ctx, calls = self._context()
        adapter = JellyfinMediaServerAdapter(context=ctx)
        adapter.run_prewarm_mode()
        invoke_ops = [entry[1] for entry in calls if entry[0] == "invoke"]
        self.assertIn(ENSURE_JELLYFIN_PREWARM, invoke_ops)

    def test_jellyfin_adapter_runs_post_steps(self):
        ctx, calls = self._context()
        adapter = JellyfinMediaServerAdapter(context=ctx)
        adapter.run_post_servarr_pre_hygiene_steps()
        adapter.run_post_servarr_post_hygiene_steps()
        invoke_ops = [entry[1] for entry in calls if entry[0] == "invoke"]
        self.assertIn(ENSURE_JELLYFIN_LIVETV, invoke_ops)
        self.assertIn(ENSURE_JELLYFIN_LIBRARIES, invoke_ops)
        self.assertIn(ENSURE_JELLYFIN_PLUGINS, invoke_ops)
        self.assertIn(ENSURE_JELLYFIN_PLAYBACK, invoke_ops)
        self.assertIn(ENSURE_JELLYFIN_HOME_RAILS, invoke_ops)
        self.assertIn(ENSURE_JELLYFIN_AUTO_COLLECTIONS, invoke_ops)
        self.assertIn(ENSURE_JELLYFIN_PREWARM, invoke_ops)

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
