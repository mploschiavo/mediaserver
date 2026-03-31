import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from bootstrap_services.apps.prowlarr.flaresolverr_service import (  # noqa: E402
    ProwlarrFlareSolverrService,
)


class ProwlarrFlareSolverrServiceTests(unittest.TestCase):
    def _service(self):
        return ProwlarrFlareSolverrService(
            bool_cfg=lambda cfg, key, default=False: bool((cfg or {}).get(key, default)),
            normalize_url=lambda value: str(value).rstrip("/"),
            wait_for_service=mock.Mock(),
            ensure_proxy=mock.Mock(),
        )

    def test_skips_when_disabled(self):
        service = self._service()
        service.ensure_from_config(
            cfg={"flaresolverr": {"enabled": False}},
            prowlarr_url="http://prowlarr:9696",
            prowlarr_key="key",
            wait_timeout=30,
        )
        service.wait_for_service.assert_not_called()
        service.ensure_proxy.assert_not_called()

    def test_waits_and_configures_proxy_when_enabled(self):
        service = self._service()
        service.ensure_from_config(
            cfg={"flaresolverr": {"enabled": True, "url": "http://flaresolverr:8191"}},
            prowlarr_url="http://prowlarr:9696",
            prowlarr_key="key",
            wait_timeout=45,
        )
        service.wait_for_service.assert_called_once_with(
            "FlareSolverr",
            "http://flaresolverr:8191",
            "/",
            45,
        )
        service.ensure_proxy.assert_called_once()
        _, _, payload = service.ensure_proxy.call_args[0]
        self.assertEqual(payload["url"], "http://flaresolverr:8191")


if __name__ == "__main__":
    unittest.main()

