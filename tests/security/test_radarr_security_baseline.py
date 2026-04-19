"""Live security audit against Radarr."""

from __future__ import annotations

import unittest

from tests.security.servarr_baseline import ServarrSecurityBaselineMixin


class RadarrSecurityBaseline(ServarrSecurityBaselineMixin, unittest.TestCase):
    SERVICE_NAME = "Radarr"
    DEFAULT_URL = "http://localhost:7878"
    ENV_VAR = "RADARR_URL"

    @classmethod
    def setUpClass(cls) -> None:
        cls._set_up_suite()


if __name__ == "__main__":
    unittest.main()
