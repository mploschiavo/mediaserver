"""Tests for dashboard HTML features and handlers_get routing."""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

DASHBOARD_PATH = ROOT / "src" / "media_stack" / "api" / "dashboard.html"
DASHBOARD_HTML = DASHBOARD_PATH.read_text(encoding="utf-8") if DASHBOARD_PATH.exists() else ""


class TestDashboardHTMLContent(unittest.TestCase):
    """Validate the dashboard HTML has expected elements and no dead code."""

    def test_has_logs_tab(self):
        self.assertIn("tab-logs", DASHBOARD_HTML)

    def test_has_content_tab(self):
        self.assertIn("tab-content", DASHBOARD_HTML)

    def test_has_routing_tab(self):
        self.assertIn("tab-routing", DASHBOARD_HTML)

    def test_has_ops_tab(self):
        self.assertIn("tab-ops", DASHBOARD_HTML)

    def test_has_config_tab(self):
        self.assertIn("tab-profile", DASHBOARD_HTML)

    def test_has_alerts_tab(self):
        self.assertIn("tab-webhooks", DASHBOARD_HTML)

    def test_has_mobile_bar(self):
        self.assertIn("mobile-bar", DASHBOARD_HTML)

    def test_has_xss_protection_function(self):
        self.assertIn("_escHtml", DASHBOARD_HTML)

    def test_no_dead_timedFetch(self):
        self.assertNotIn("timedFetch", DASHBOARD_HTML)

    def test_no_dead_apiTimings(self):
        self.assertNotIn("apiTimings", DASHBOARD_HTML)

    def test_no_api_footer_div(self):
        self.assertNotIn('id="api-footer"', DASHBOARD_HTML)

    def test_has_interval_tracking(self):
        self.assertIn("_intervals", DASHBOARD_HTML)

    def test_has_beforeunload_cleanup(self):
        self.assertIn("beforeunload", DASHBOARD_HTML)

    def test_mobile_touch_targets(self):
        self.assertIn("min-height:44px", DASHBOARD_HTML)

    def test_no_unmatched_quotes_in_js_strings(self):
        """JS strings must not contain unescaped quotes that break syntax.

        Catches the specific bug pattern: 'Run 'Configure All' first'
        where inner single quotes break the outer string.
        """
        import re
        in_script = False
        for lineno, line in enumerate(DASHBOARD_HTML.splitlines(), 1):
            if "<script" in line:
                in_script = True
            if "</script>" in line:
                in_script = False
            if not in_script:
                continue
            stripped = line.strip()
            if stripped.startswith("//"):
                continue
            # Pattern: a string like 'text 'Word text' — space before inner quote
            # This catches 'Run 'Configure All' first' but not 'ok','val'
            if re.search(r"'[^']*\s'[A-Z]", line):
                self.fail(
                    f"Line {lineno}: unmatched single quote in JS string "
                    f"(space before inner quote): {stripped[:100]}"
                )

    def test_confirm_action_uses_addEventListener(self):
        self.assertIn("confirm-run-btn", DASHBOARD_HTML)
        self.assertIn("addEventListener", DASHBOARD_HTML)

    def test_has_container_totals_reference(self):
        self.assertIn("totals", DASHBOARD_HTML)

    def test_has_aggregate_display_pills(self):
        self.assertIn("cpu_display", DASHBOARD_HTML)
        self.assertIn("memory_display", DASHBOARD_HTML)


class TestHandlersGetRouting(unittest.TestCase):
    """Test GET route dispatch."""

    def _make_handler(self, path):
        h = MagicMock()
        h.path = path
        h.state = MagicMock()
        h.state.initial_bootstrap_done = True
        h.state.phase = "complete"
        h.state.to_dict.return_value = {"phase": "complete"}
        h.state.app_status = {}
        h.state.runtime_config = {}
        h.state.webhook_urls = []
        h.state.get_failed_services.return_value = {}
        h.state.get_logs_since.return_value = []
        return h

    def test_healthz(self):
        h = self._make_handler("/healthz")
        from media_stack.api.handlers_get import handle
        handle(h)
        h._json_response.assert_called_once_with(200, {"status": "ok"})

    def test_readyz(self):
        h = self._make_handler("/readyz")
        from media_stack.api.handlers_get import handle
        handle(h)
        args = h._json_response.call_args[0]
        self.assertEqual(args[0], 200)
        self.assertEqual(args[1]["status"], "ready")

    def test_not_found(self):
        h = self._make_handler("/api/nonexistent")
        from media_stack.api.handlers_get import handle
        handle(h)
        args = h._json_response.call_args[0]
        self.assertEqual(args[0], 404)


if __name__ == "__main__":
    unittest.main()
