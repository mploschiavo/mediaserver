import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from cli.bootstrap_notification_service import (  # noqa: E402
    BootstrapNotificationConfig,
    BootstrapNotificationService,
)


class BootstrapNotificationServiceTests(unittest.TestCase):
    def test_notify_noop_without_webhook(self):
        svc = BootstrapNotificationService(cfg=BootstrapNotificationConfig(alert_webhook_url=""))
        with mock.patch("urllib.request.urlopen") as mocked:
            svc.notify("info", "message")
        mocked.assert_not_called()

    def test_notify_posts_with_webhook(self):
        svc = BootstrapNotificationService(
            cfg=BootstrapNotificationConfig(alert_webhook_url="http://example.com/hook")
        )
        fake_ctx = mock.MagicMock()
        fake_ctx.__enter__.return_value = object()
        fake_ctx.__exit__.return_value = False
        with mock.patch("urllib.request.urlopen", return_value=fake_ctx) as mocked:
            svc.notify("ok", "done")
        mocked.assert_called_once()


if __name__ == "__main__":
    unittest.main()
