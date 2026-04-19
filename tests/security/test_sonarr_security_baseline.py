"""Live security audit against Sonarr."""

from __future__ import annotations

import unittest

from tests.security.servarr_baseline import ServarrSecurityBaselineMixin


class SonarrSecurityBaseline(ServarrSecurityBaselineMixin, unittest.TestCase):
    SERVICE_NAME = "Sonarr"
    DEFAULT_URL = "http://localhost:8989"
    ENV_VAR = "SONARR_URL"

    @classmethod
    def setUpClass(cls) -> None:
        cls._set_up_suite()


if __name__ == "__main__":
    unittest.main()
