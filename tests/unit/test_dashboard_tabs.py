"""Tests for dashboard tab rendering.

Catches the bug where a subtab is defined in HTML but not wired
into showSubTab's lazy-load chain — resulting in a tab that shows
skeleton bars forever.
"""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DASHBOARD = ROOT / "src" / "media_stack" / "api" / "dashboard.html"


class TestSubTabWiring(unittest.TestCase):
    """Every subtab ID must have a loader in showSubTab."""

    def test_every_subtab_has_loader(self):
        """Every subtab div referenced by a showSubTab button must
        have a matching handler in the showSubTab function body.

        This catches: tab shows skeleton forever because loadXxx
        is never called.
        """
        html = DASHBOARD.read_text()

        # Find all subtab IDs triggered by buttons
        # Pattern: onclick="showSubTab('cfg-jobs',this)"
        button_ids = set(re.findall(r"showSubTab\('([^']+)'", html))

        # Find all subtab IDs handled in the showSubTab function
        # Pattern: if(id==='cfg-jobs')
        handled_ids = set(re.findall(r"id==='([^']+)'", html))

        # cfg-profile is the default active tab — loaded on page init, not via showSubTab
        _DEFAULT_ACTIVE = {"cfg-profile"}
        unhandled = button_ids - handled_ids - _DEFAULT_ACTIVE
        self.assertEqual(unhandled, set(),
                         f"Subtabs with buttons but no loader in showSubTab: {sorted(unhandled)}. "
                         "These tabs will show skeleton bars forever. "
                         "Add them to the if/else chain in showSubTab().")

    def test_every_subtab_div_exists(self):
        """Every subtab ID referenced by a button must have a matching div."""
        html = DASHBOARD.read_text()
        button_ids = set(re.findall(r"showSubTab\('([^']+)'", html))

        for tab_id in button_ids:
            self.assertIn(f'id="{tab_id}"', html,
                          f"Button targets subtab '{tab_id}' but no div with that ID exists")

    def test_no_nested_div_in_subtabs_with_skeleton(self):
        """Subtabs that use skeleton loading must not rely on a nested
        child div ID — the skeleton replaces innerHTML and the child
        disappears.
        """
        html = DASHBOARD.read_text()

        # Find subtab IDs that go through showSubTab (which sets skeleton)
        button_ids = set(re.findall(r"showSubTab\('([^']+)'", html))

        # For each, check if the load function references getElementById
        # with a DIFFERENT id than the subtab itself
        # Pattern: async function loadXxx(){ ... getElementById('child-id') ...
        # This is the bug pattern — skeleton wipes 'child-id'
        #
        # We check: every subtab load function should target the subtab
        # div itself (by its ID), not a child div.
        # This is a heuristic check — not perfect but catches the common case.
        for tab_id in button_ids:
            # Find the load function for this tab
            # The pattern in showSubTab is: if(id==='cfg-jobs')_load(loadJobTree)
            m = re.search(rf"id==='{re.escape(tab_id)}'\)([^;]+)", html)
            if not m:
                continue
            call = m.group(1)
            # Extract function name: _load(loadJobTree) or loadJobTree() or {renderX();}
            fn_match = re.search(r'_load\((\w+)\)|(\w+)\(\)', call)
            if not fn_match:
                continue
            fn_name = fn_match.group(1) or fn_match.group(2)
            # Find the function body
            fn_pattern = rf'function {re.escape(fn_name)}\(\)[^{{]*\{{(.*?)\n\}}'
            fn_body_match = re.search(fn_pattern, html, re.DOTALL)
            if not fn_body_match:
                continue
            fn_body = fn_body_match.group(1)
            # Check: does it reference getElementById with the subtab ID?
            # If it references a DIFFERENT ID, that's the skeleton-wipe bug.
            get_calls = re.findall(r"getElementById\('([^']+)'\)", fn_body)
            for get_id in get_calls:
                if get_id != tab_id:
                    self.fail(
                        f"Load function '{fn_name}' for subtab '{tab_id}' "
                        f"targets getElementById('{get_id}') — but skeleton loader "
                        f"replaces {tab_id}'s innerHTML, wiping '{get_id}'. "
                        f"Fix: target '{tab_id}' directly instead of a child div."
                    )


if __name__ == "__main__":
    unittest.main()
