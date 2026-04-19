"""Live security audit against Prowlarr."""

from __future__ import annotations

import unittest

from tests.security.servarr_baseline import ServarrSecurityBaselineMixin


class ProwlarrSecurityBaseline(ServarrSecurityBaselineMixin, unittest.TestCase):
    SERVICE_NAME = "Prowlarr"
    DEFAULT_URL = "http://localhost:9696"
    ENV_VAR = "PROWLARR_URL"
    API_V = "v1"  # Prowlarr still uses API v1 path

    @classmethod
    def setUpClass(cls) -> None:
        cls._set_up_suite()


if __name__ == "__main__":
    unittest.main()
