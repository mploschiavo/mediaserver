import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from bootstrap_services.runtime_core import (  # noqa: E402
    resolve_app_service_class,
    set_runtime_context_cfg,
)
from bootstrap_services.jellyseerr_service import JellyseerrService  # noqa: E402


class AppServiceResolutionTests(unittest.TestCase):
    def tearDown(self):
        set_runtime_context_cfg({})

    def test_resolve_app_service_class_uses_manifest_binding_by_default(self):
        cls = resolve_app_service_class("jellyseerr_service", JellyseerrService)
        self.assertIs(cls, JellyseerrService)

    def test_resolve_app_service_class_uses_runtime_context_hooks(self):
        set_runtime_context_cfg(
            {
                "app_service_classes": {
                    "jellyseerr_service": "bootstrap_services.jellyseerr_service:JellyseerrService"
                }
            },
        )
        cls = resolve_app_service_class("jellyseerr_service", JellyseerrService)
        self.assertIs(cls, JellyseerrService)

    def test_resolve_app_service_class_prefers_technology_binding(self):
        set_runtime_context_cfg(
            {
                "technology_aliases": {"openseer": "openseerr"},
                "app_service_classes": {
                    "request_manager_service": "bootstrap_services.jellyseerr_service:JellyseerrService"
                },
                "app_service_classes_by_technology": {
                    "jellyseerr": {
                        "request_manager_service": (
                            "bootstrap_services.jellyseerr_service:JellyseerrService"
                        )
                    },
                    "openseerr": {
                        "request_manager_service": (
                            "bootstrap_services.apps.openseerr.service:OpenSeerrService"
                        )
                    },
                },
            },
        )
        cls = resolve_app_service_class(
            "request_manager_service",
            JellyseerrService,
            technology="openseer",
        )
        self.assertEqual(cls.__name__, "OpenSeerrService")

    def test_resolve_app_service_class_rejects_invalid_spec(self):
        set_runtime_context_cfg(
            {
                "app_service_classes": {
                    "jellyseerr_service": "invalid-spec",
                }
            }
        )
        with self.assertRaises(RuntimeError):
            resolve_app_service_class("jellyseerr_service", JellyseerrService)

    def test_resolve_app_service_class_requires_explicit_binding_when_hooks_loaded(self):
        set_runtime_context_cfg(
            {
                "app_service_classes": {
                    "other_service": "bootstrap_services.jellyseerr_service:JellyseerrService"
                }
            }
        )
        with self.assertRaises(RuntimeError):
            resolve_app_service_class("jellyseerr_service", JellyseerrService)


if __name__ == "__main__":
    unittest.main()
