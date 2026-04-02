import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from cli.deploy_profile_defaults_service import (  # noqa: E402
    DeployProfileDefaultsService,
)


class DeployProfileDefaultsServiceTests(unittest.TestCase):
    def test_full_profile_sets_expected_defaults(self):
        svc = DeployProfileDefaultsService()
        resolved = svc.apply(
            profile="full",
            include_optional="",
            enable_components="",
            run_bootstrap="",
        )
        self.assertEqual(resolved.include_optional, "1")
        self.assertEqual(resolved.enable_components, "1")
        self.assertEqual(resolved.run_bootstrap, "1")

    def test_public_demo_defaults(self):
        svc = DeployProfileDefaultsService()
        resolved = svc.apply(
            profile="public-demo",
            include_optional="",
            enable_components="",
            run_bootstrap="",
        )
        self.assertEqual(resolved.include_optional, "1")
        self.assertEqual(resolved.enable_components, "0")
        self.assertEqual(resolved.run_bootstrap, "0")


if __name__ == "__main__":
    unittest.main()
