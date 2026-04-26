import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

import media_stack.services.apps.homepage.runtime_ops as MODULE


class HomepageRuntimeOpsTests(unittest.TestCase):
    def test_revalidates_runtime_cache_when_services_config_changes(self):
        service = mock.Mock()
        service.ensure_services_config.return_value = True

        with mock.patch.object(MODULE._instance, "_homepage_service", return_value=service):
            with mock.patch.object(
                MODULE,
                "http_request",
                return_value=(200, {"revalidated": True}, "ok"),
            ) as request_mock:
                with mock.patch.object(MODULE, "log"):
                    changed = MODULE.ensure_homepage_services_config(
                        {"homepage": {"url": "http://homepage:3000"}},
                        "/tmp/config",
                    )

        self.assertTrue(changed)
        request_mock.assert_called_once_with("http://homepage:3000", "/api/revalidate", timeout=15)

    def test_skips_revalidate_when_services_config_is_unchanged(self):
        service = mock.Mock()
        service.ensure_services_config.return_value = False

        with mock.patch.object(MODULE._instance, "_homepage_service", return_value=service):
            with mock.patch.object(MODULE, "http_request") as request_mock:
                changed = MODULE.ensure_homepage_services_config(
                    {"homepage": {"url": "http://homepage:3000"}},
                    "/tmp/config",
                )

        self.assertFalse(changed)
        request_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
