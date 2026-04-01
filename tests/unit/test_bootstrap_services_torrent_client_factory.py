import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from bootstrap_services.runtime_service_registry import get_runtime_context_cfg, set_runtime_context_cfg
from bootstrap_services.runtime_servarr.factory import _torrent_client_service


class TorrentClientServiceFactoryTests(unittest.TestCase):
    def setUp(self):
        self._prior_ctx = get_runtime_context_cfg()

    def tearDown(self):
        set_runtime_context_cfg(self._prior_ctx)

    @staticmethod
    def _context(torrent_client: str = "") -> dict:
        return {
            "technology_aliases": {
                "qbit": "qbittorrent",
                "qb": "qbittorrent",
            },
            "app_service_classes_by_technology": {
                "qbittorrent": {
                    "torrent_client_service": "bootstrap_services.apps.qbittorrent.service:QBittorrentService",
                },
                "transmission": {
                    "torrent_client_service": "bootstrap_services.apps.transmission.service:TransmissionService",
                },
            },
            "runtime_bindings": {
                "torrent_client": torrent_client,
            },
        }

    def test_factory_resolves_transmission_from_runtime_binding(self):
        set_runtime_context_cfg(self._context("transmission"))
        service = _torrent_client_service()
        self.assertEqual(service.__class__.__name__, "TransmissionService")

    def test_factory_resolves_qbittorrent_from_explicit_technology_when_binding_missing(self):
        set_runtime_context_cfg(self._context(""))
        service = _torrent_client_service({"technology": "qbittorrent"})
        self.assertEqual(service.__class__.__name__, "QBittorrentService")

    def test_factory_requires_bindable_technology_when_no_hints_present(self):
        set_runtime_context_cfg(self._context(""))
        with self.assertRaisesRegex(RuntimeError, "Unable to resolve active torrent client"):
            _torrent_client_service({})


if __name__ == "__main__":
    unittest.main()
