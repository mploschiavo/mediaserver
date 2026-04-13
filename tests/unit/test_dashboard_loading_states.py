"""Verify dashboard doesn't show stale 'Loading...' to users.

Catches:
1. Subtabs that say "Loading..." but have no data loader registered
2. Default active subtabs that don't auto-trigger their loader
3. Tab/subtab IDs referenced in JS but missing from HTML
4. API endpoints referenced in loaders that don't exist in handlers
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DASHBOARD = ROOT / "src" / "media_stack" / "api" / "dashboard.html"


def _load_dashboard() -> str:
    return DASHBOARD.read_text(encoding="utf-8")


class TestNoStaleLoadingText(unittest.TestCase):
    """Active/visible subtabs must not show static 'Loading...' text."""

    def test_no_bare_loading_in_active_subtabs(self):
        """Active subtabs (class='active') should not contain bare 'Loading...' text."""
        text = _load_dashboard()
        # Find active subtab divs
        active_tabs = re.findall(r'<div[^>]*class="subtab active"[^>]*>(.*?)</div>', text, re.DOTALL)
        bare_loading = [t for t in active_tabs if t.strip() == "Loading..." or t.strip() == '<span style="color:var(--fg3);font-size:.85em">Loading...</span>']
        self.assertFalse(
            bare_loading,
            f"Active subtabs with stale 'Loading...' (no auto-loader): {bare_loading[:3]}"
        )


class TestSubtabLoadersExist(unittest.TestCase):
    """Every subtab ID in showSubTab() must have a matching loader function."""

    def test_all_subtab_ids_have_loaders(self):
        text = _load_dashboard()
        # Extract subtab IDs from showSubTab dispatch
        dispatched = set(re.findall(r"id==='([^']+)'", text))
        # Extract subtab IDs from HTML
        html_ids = set(re.findall(r'id="((?:lib|cfg|ops)-[^"]+)"', text))
        # Subtabs in HTML that are never loaded
        # (exclude static content subtabs like ops-actions)
        # Subtabs loaded via onTabShow (not showSubTab) or are inner containers
        static_subtabs = {"ops-actions", "cfg-profile", "lib-list"}
        missing_loader = html_ids - dispatched - static_subtabs
        # Filter to only subtabs that have "Loading..." content
        actually_missing = []
        for sid in missing_loader:
            pattern = f'id="{sid}"[^>]*>.*?Loading'
            if re.search(pattern, text, re.DOTALL):
                actually_missing.append(sid)
        self.assertFalse(
            actually_missing,
            f"Subtabs with 'Loading...' but no loader in showSubTab: {actually_missing}"
        )


class TestDefaultSubtabAutoLoads(unittest.TestCase):
    """Each tab's default (active) subtab must be loaded in onTabShow()."""

    def test_content_tab_loads_default(self):
        text = _load_dashboard()
        # onTabShow function must call loadLibraries for tab-content
        on_tab_show = re.search(r"function onTabShow.*?\n\}", text, re.DOTALL)
        self.assertIsNotNone(on_tab_show, "onTabShow function not found")
        body = on_tab_show.group()
        self.assertIn("tab-content", body, "onTabShow must handle tab-content")
        self.assertIn("loadLibraries", body,
            "onTabShow must call loadLibraries when showing Content tab")

    def test_config_tab_loads_default(self):
        text = _load_dashboard()
        on_tab_show = re.search(r"function onTabShow.*?\n\}", text, re.DOTALL)
        self.assertIsNotNone(on_tab_show)
        body = on_tab_show.group()
        self.assertIn("tab-profile", body, "onTabShow must handle tab-profile (Config)")
        self.assertIn("loadDisplayConfig", body,
            "onTabShow must call loadDisplayConfig when showing Config tab")


class TestApiEndpointsExist(unittest.TestCase):
    """API endpoints referenced in dashboard fetch() calls must exist in handlers."""

    def test_fetched_endpoints_have_handlers(self):
        dashboard = _load_dashboard()
        handlers_get = (ROOT / "src" / "media_stack" / "api" / "handlers_get.py").read_text()
        handlers_post = (ROOT / "src" / "media_stack" / "api" / "handlers_post.py").read_text()
        all_handlers = handlers_get + handlers_post

        # Extract fetch URLs from dashboard
        fetch_urls = set(re.findall(r"fetch\(['\"](/api/[^'\"?]+)", dashboard))
        # Filter out dynamic paths like /api/quality-profiles/sonarr
        static_urls = {u for u in fetch_urls if "{" not in u and not re.search(r"/[a-z]+-[a-z]+/[a-z]", u)}

        missing = []
        for url in sorted(static_urls):
            if url not in all_handlers:
                missing.append(url)

        self.assertFalse(
            missing,
            f"Dashboard fetches endpoints not in handlers:\n"
            + "\n".join(f"  {u}" for u in missing),
        )


if __name__ == "__main__":
    unittest.main()
