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
        set_runtime_context_cfg({}, {})

    def test_resolve_app_service_class_uses_default_when_unset(self):
        cls = resolve_app_service_class({}, "jellyseerr_service", JellyseerrService)
        self.assertIs(cls, JellyseerrService)

    def test_resolve_app_service_class_uses_cfg_override(self):
        cfg = {
            "adapter_hooks": {
                "app_service_classes": {
                    "jellyseerr_service": "bootstrap_services.jellyseerr_service:JellyseerrService"
                }
            }
        }
        cls = resolve_app_service_class(cfg, "jellyseerr_service", JellyseerrService)
        self.assertIs(cls, JellyseerrService)

    def test_resolve_app_service_class_uses_runtime_context_hooks(self):
        set_runtime_context_cfg(
            {},
            {
                "app_service_classes": {
                    "jellyseerr_service": "bootstrap_services.jellyseerr_service:JellyseerrService"
                }
            },
        )
        cls = resolve_app_service_class(None, "jellyseerr_service", JellyseerrService)
        self.assertIs(cls, JellyseerrService)

    def test_resolve_app_service_class_rejects_invalid_spec(self):
        cfg = {
            "adapter_hooks": {
                "app_service_classes": {
                    "jellyseerr_service": "invalid-spec",
                }
            }
        }
        with self.assertRaises(RuntimeError):
            resolve_app_service_class(cfg, "jellyseerr_service", JellyseerrService)


if __name__ == "__main__":
    unittest.main()
