"""UX regression tests for the dashboard.

Catches interaction bugs that cause:
1. Dropdowns/inputs resetting when the user changes a selection
2. Page jumping to the top on tab click
3. Form state being wiped by async reloads
4. Scroll position changes from hash navigation

These are static analysis tests — they scan the JS source for known
anti-patterns. They don't execute JS but catch the code patterns that
reliably produce UX bugs.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DASHBOARD = ROOT / "src" / "media_stack" / "api" / "dashboard.html"


def _load() -> str:
    return DASHBOARD.read_text(encoding="utf-8")


def _script_blocks(html: str) -> str:
    """Extract all <script> content."""
    blocks = re.findall(r"<script[^>]*>(.*?)</script>", html, re.DOTALL)
    return "\n".join(blocks)


class TestNoScrollJumpOnTabClick(unittest.TestCase):
    """Tab clicks must not cause the browser to jump/scroll unexpectedly."""

    def test_no_location_hash_assignment(self):
        """Setting location.hash causes browser scroll-to-anchor.

        Use history.replaceState(null,null,'#hash') instead.
        """
        js = _script_blocks(_load())
        # Match: location.hash = ... or location.hash= ...
        # Exclude: location.hash.replace (reading hash, not setting it)
        assignments = re.findall(r"location\.hash\s*=(?!=)", js)
        self.assertEqual(
            len(assignments), 0,
            f"Found {len(assignments)} location.hash assignment(s). "
            "This causes scroll-jump. Use history.replaceState() instead."
        )

    def test_no_scroll_to_top_zero(self):
        """scrollTo({top:0}) inside showTab pushes tab content off-screen.

        The tab bar is below the header/quick actions, so scrolling to
        page top (0) hides the tab content. If scroll reset is needed,
        scroll to the tab bar element instead.
        """
        js = _script_blocks(_load())
        # Find showTab function body
        show_tab = re.search(r"function showTab\(.*?\{(.*?)\n\}", js, re.DOTALL)
        if not show_tab:
            return  # showTab not found — different test covers that
        body = show_tab.group(1)
        self.assertNotIn(
            "scrollTo({top:0",
            body,
            "showTab() scrolls to page top, pushing tabs off-screen. "
            "Remove or scroll to tab-bar element instead."
        )

    def test_tabs_use_replace_state(self):
        """Tab switching should use history.replaceState for URL hash."""
        js = _script_blocks(_load())
        show_tab = re.search(r"function showTab\(.*?\{(.*?)\n\}", js, re.DOTALL)
        if not show_tab:
            self.fail("showTab function not found")
        body = show_tab.group(1)
        self.assertIn(
            "replaceState",
            body,
            "showTab() must use history.replaceState() for URL hash, "
            "not location.hash which causes scroll-jump."
        )


class TestOnchangeHandlersDontReload(unittest.TestCase):
    """Form onchange handlers must not trigger full-page data reloads.

    The pattern: user picks a dropdown value → onchange calls loadXxx()
    → loadXxx() fetches from server → re-renders entire section →
    dropdown resets to server state → user's selection is lost.

    This is the #1 dashboard UX bug pattern.
    """

    def _get_onchange_functions(self) -> list[tuple[int, str, str]]:
        """Return (line, element_context, function_name) for onchange handlers."""
        html = _load()
        results = []
        for lineno, line in enumerate(html.splitlines(), 1):
            # Find onchange="functionName()" patterns
            for m in re.finditer(r'onchange="([^"]+)"', line):
                handler = m.group(1)
                results.append((lineno, line.strip()[:100], handler))
        return results

    # Legitimate onchange→fetch patterns that are NOT forms with unsaved state.
    # These are action triggers where the user expects immediate server action.
    _ALLOWED_ONCHANGE_FETCH = {
        # Log source picker — selecting a service should immediately fetch its logs
        "loadSvcLogFromPicker",
    }

    def test_no_onchange_calls_load_function_directly(self):
        """onchange must not directly call a load* function that fetches from server.

        load* functions fetch data and re-render, wiping unsaved form state.
        onchange should call a local state update function instead.

        Exception: action-trigger selects (like log source picker) where the
        user expects immediate server action are in the allowlist.
        """
        html = _load()
        js = _script_blocks(html)

        # Find all load* functions that call apiFetch (i.e., hit the server)
        server_load_fns = set()
        for m in re.finditer(r"(?:async\s+)?function\s+(\w+)\s*\([^)]*\)\s*\{", js):
            fn_name = m.group(1)
            if not fn_name.startswith("load"):
                continue
            # Find the function body (heuristic: next 2000 chars)
            start = m.end()
            chunk = js[start:start + 2000]
            if "apiFetch(" in chunk or "fetch(" in chunk:
                server_load_fns.add(fn_name)

        # Now check: any onchange that directly calls a server-load function?
        violations = []
        for lineno, context, handler in self._get_onchange_functions():
            for fn in server_load_fns:
                if fn in self._ALLOWED_ONCHANGE_FETCH:
                    continue
                # Direct call: onchange="loadAuthConfig()"
                if fn + "()" in handler or fn + "(" in handler:
                    violations.append(
                        f"  Line {lineno}: onchange calls {fn}() which fetches from server\n"
                        f"  {context}"
                    )

        self.assertFalse(
            violations,
            f"\nonchange handlers must not call server-fetch functions directly.\n"
            f"This wipes unsaved form state when the user changes a dropdown.\n"
            f"Fix: call a local state-update function instead.\n\n"
            f"Violations:\n" + "\n".join(violations)
        )

    def test_select_onchange_does_not_re_render_own_container(self):
        """A <select> onchange must not call a function that sets innerHTML
        on the select's own parent subtab/section container.

        This is the exact pattern that causes dropdown reset:
        select.onchange → render() → container.innerHTML = new HTML → select destroyed.

        Only flags when the innerHTML target matches the select's parent container.
        Safe patterns (like setBwInterval setting a timer that updates a different
        subtab) are not flagged.
        """
        html = _load()
        js = _script_blocks(html)

        # Find select elements with their parent container IDs and onchange handlers
        # Pattern: <div id="cfg-auth" ...> ... <select onchange="handler()"> ...
        violations = []
        for m in re.finditer(
            r'<select[^>]*id="([^"]*)"[^>]*onchange="(\w+)\([^"]*\)"', html
        ):
            select_id = m.group(1)
            handler_name = m.group(2)

            # Find function body
            fn_match = re.search(
                rf"function\s+{re.escape(handler_name)}\s*\([^)]*\)\s*\{{(.*?)(?:\n\}}|\n  \}})",
                js, re.DOTALL,
            )
            if not fn_match:
                continue
            body = fn_match.group(1)

            # Direct pattern: function fetches and sets innerHTML on a container
            # that holds this select
            if "apiFetch(" in body and f"getElementById('{select_id}')" in body and ".innerHTML=" in body:
                violations.append(
                    f"select #{select_id} onchange '{handler_name}' fetches from server "
                    f"and sets innerHTML on its own container — this destroys the select."
                )

        self.assertFalse(
            violations,
            "Select onchange handlers must not re-render their own container:\n"
            + "\n".join(f"  {v}" for v in violations)
        )


class TestBodyScrollbarStability(unittest.TestCase):
    """Body must have stable scrollbar to prevent layout shift."""

    def test_body_has_overflow_y_scroll(self):
        """Without overflow-y:scroll, the scrollbar appears/disappears
        when tab content height changes, shifting the page ~15px.
        """
        html = _load()
        # Check CSS
        self.assertRegex(
            html,
            r"body\s*\{[^}]*overflow-y\s*:\s*scroll",
            "body CSS must include overflow-y:scroll to prevent scrollbar layout shift"
        )


class TestTabContentMinHeight(unittest.TestCase):
    """Tab content panels must have a min-height to prevent collapse on switch."""

    def test_tab_content_has_min_height(self):
        """Without min-height, switching from a tall tab to a short one
        causes the page to visually collapse then expand when data loads.
        """
        html = _load()
        # Check the .tab-content CSS rule
        match = re.search(r"\.tab-content\s*\{([^}]+)\}", html)
        self.assertIsNotNone(match, ".tab-content CSS rule not found")
        rule = match.group(1)
        self.assertIn(
            "min-height",
            rule,
            ".tab-content must have min-height to prevent collapse on tab switch"
        )


class TestFormStatePreservation(unittest.TestCase):
    """Config forms that have multiple related dropdowns must preserve
    state locally — not refetch from server on every interaction.
    """

    def test_auth_form_has_local_state(self):
        """Auth config has mode + OIDC provider + OIDC fields.
        Changing one dropdown must not wipe the others.
        """
        js = _script_blocks(_load())
        # The auth form should have a local state variable
        self.assertRegex(
            js,
            r"_authFormState|_authCfg|let\s+_auth\w*\s*=",
            "Auth config form must maintain local state to prevent "
            "dropdown resets when changing selections."
        )

    def test_oidc_provider_change_is_local(self):
        """onOidcProviderChange must NOT call loadAuthConfig or apiFetch."""
        js = _script_blocks(_load())
        fn = re.search(
            r"function\s+onOidcProviderChange\s*\(\)\s*\{(.*?)\n\}",
            js, re.DOTALL,
        )
        if not fn:
            self.skipTest("onOidcProviderChange not found")
        body = fn.group(1)
        self.assertNotIn(
            "loadAuthConfig",
            body,
            "onOidcProviderChange must not call loadAuthConfig — "
            "this reloads from server and wipes the dropdown selection."
        )
        self.assertNotIn(
            "apiFetch",
            body,
            "onOidcProviderChange must not call apiFetch — "
            "server reload wipes unsaved form state."
        )


if __name__ == "__main__":
    unittest.main()
