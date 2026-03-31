import sys
import types
import unittest

from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from bootstrap_services.adapter_reflection import (  # noqa: E402
    class_prefix_from_key,
    discover_adapter_class,
    module_token_from_key,
)
from bootstrap_services.download_client_adapters.base import (  # noqa: E402
    DownloadClientAdapterBase,
)


class AdapterReflectionTests(unittest.TestCase):
    def test_module_token_normalizes_key(self):
        self.assertEqual(module_token_from_key("My-Torrent.Client"), "my_torrent_client")

    def test_class_prefix_normalizes_key(self):
        self.assertEqual(class_prefix_from_key("my-torrent client"), "MyTorrentClient")

    def test_discover_adapter_class_returns_none_for_missing_module(self):
        discovered = discover_adapter_class(
            module_prefix="bootstrap_services.download_client_adapters",
            key="module-that-does-not-exist",
            base_class=DownloadClientAdapterBase,
            class_suffix="DownloadClientAdapter",
        )
        self.assertIsNone(discovered)

    def test_discover_adapter_class_uses_single_subclass_fallback(self):
        module_name = "bootstrap_services.download_client_adapters.discovery_fallback"
        fake_module = types.ModuleType(module_name)

        class StrangeName(DownloadClientAdapterBase):
            pass

        fake_module.StrangeName = StrangeName
        with mock.patch.dict(sys.modules, {module_name: fake_module}):
            discovered = discover_adapter_class(
                module_prefix="bootstrap_services.download_client_adapters",
                key="discovery-fallback",
                base_class=DownloadClientAdapterBase,
                class_suffix="DownloadClientAdapter",
            )
        self.assertIs(discovered, StrangeName)


if __name__ == "__main__":
    unittest.main()
