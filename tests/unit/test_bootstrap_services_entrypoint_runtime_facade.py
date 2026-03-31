import importlib
import unittest


class EntrypointRuntimeFacadeTests(unittest.TestCase):
    def test_facade_exports_legacy_private_helpers(self):
        module = importlib.import_module("bootstrap_services.entrypoint_runtime")
        self.assertTrue(hasattr(module, "_disk_usage_percent"))
        self.assertTrue(hasattr(module, "_fmt_bytes"))
        self.assertTrue(hasattr(module, "_to_float"))
        self.assertTrue(hasattr(module, "_servarr_pipeline_service"))

    def test_facade_exports_common_runtime_api(self):
        module = importlib.import_module("bootstrap_services.entrypoint_runtime")
        required = [
            "read_api_key",
            "resolve_jellyfin_api_key",
            "ensure_arr_download_client",
            "ensure_jellyfin_livetv",
            "run_media_hygiene",
            "configure_jellyseerr",
        ]
        for name in required:
            self.assertTrue(hasattr(module, name), msg=f"missing facade symbol: {name}")

