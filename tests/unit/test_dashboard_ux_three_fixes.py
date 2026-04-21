"""Ratchets for three UX fixes (2026-04-21):

1. Services table stacks into cards on phone widths (<=600px).
2. Login screen has a clickable "Forgot your password?" that
   explains the break-glass reset path (``bin/reset-admin.sh``)
   instead of the old dead-end "Contact your administrator".
3. Job tree surfaces elapsed time on the currently-running job so
   long jobs (``discover-indexers``, ~14 min) don't look stalled.

Each fix is small and obvious in isolation, so each is also
cheap to regress. These tests pin the user-facing shape — if a
future refactor drops the ``data-label`` attrs, removes the
forgot-password link, or breaks the running-badge signature,
the test tells you which one.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

_DASHBOARD = ROOT / "src" / "media_stack" / "api" / "dashboard.html"
_LOGIN_PAGE = ROOT / "src" / "media_stack" / "api" / "login_page.py"


class MobileServicesTableCardLayout(unittest.TestCase):
    """Task 4. Services table must stack as cards under 600px."""

    def setUp(self) -> None:
        self.html = _DASHBOARD.read_text(encoding="utf-8")

    def test_phone_breakpoint_rules_target_svc_table(self) -> None:
        """A @media(max-width:600px) block must contain #svc-table
        rules that turn rows into stacked cards."""
        # Pull the @media(max-width:600px) block.
        m = re.search(
            r"@media\s*\(\s*max-width\s*:\s*600px\s*\)\s*\{",
            self.html,
        )
        self.assertIsNotNone(
            m, "No @media(max-width:600px) block — the card-layout "
               "breakpoint was dropped.",
        )
        # Find the matching close brace.
        start = m.end()
        depth = 1
        i = start
        while i < len(self.html) and depth > 0:
            c = self.html[i]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
            i += 1
        block = self.html[start:i]
        self.assertIn(
            "#svc-table", block,
            "Phone breakpoint no longer styles #svc-table — mobile "
            "regressed to horizontal-scroll table.",
        )
        # The essential rule: rows become blocks instead of table-rows.
        self.assertRegex(
            block,
            r"#svc-table[^{]*tr[^{]*\{[^}]*display\s*:\s*block",
            "Phone breakpoint must set `#svc-table tr { display:block }` "
            "or similar — otherwise the card layout doesn't render.",
        )

    def test_render_services_emits_data_label_attrs(self) -> None:
        """Each visible <td> in renderServices must carry a data-label
        attribute — that's what the CSS pseudo-element reads."""
        m = re.search(
            r"function renderServices\([^)]*\)\s*\{",
            self.html,
        )
        self.assertIsNotNone(m, "renderServices() not found in dashboard.html")
        body_start = m.end()
        # Grab ~6k chars of body (renderServices is ~100 lines).
        body = self.html[body_start:body_start + 6000]
        required_labels = {
            "Service", "Trend", "Status", "Auth", "Login",
            "Config", "Latency", "Version", "Age", "Category",
        }
        missing = {
            label for label in required_labels
            if f'data-label="{label}"' not in body
        }
        self.assertFalse(
            missing,
            "renderServices() is missing data-label attrs for: "
            f"{sorted(missing)} — mobile card layout shows blank "
            "labels above each value when these are dropped.",
        )


class LoginForgotPasswordLink(unittest.TestCase):
    """Task 5. Forgot-password link must exist, be clickable, and
    point to the break-glass reset path."""

    def setUp(self) -> None:
        self.src = _LOGIN_PAGE.read_text(encoding="utf-8")

    def test_login_has_clickable_forgot_link_with_reset_instructions(self) -> None:
        self.assertIn(
            'id="forgot"', self.src,
            "Login page lost the forgot-password link (id=\"forgot\")",
        )
        self.assertIn(
            "bin/reset-admin.sh", self.src,
            "Forgot-password instructions must name bin/reset-admin.sh "
            "— that's the only supported reset path after the "
            "admin-bootstrap redesign (no env/web backdoor).",
        )
        # The old dead-end copy must be gone — it was actively
        # unhelpful when the reader *is* the administrator.
        self.assertNotIn(
            "Contact your administrator", self.src,
            "The 'Contact your administrator' dead-end copy is back. "
            "For single-admin home deployments the user IS the admin; "
            "show them the reset-admin.sh command instead.",
        )

    def test_forgot_toggle_wired_up_in_js(self) -> None:
        """The reset-instructions div (#resetHelp) must be toggled
        by a click handler on #forgot — otherwise the link 404s
        to itself and the user sees nothing happen."""
        self.assertRegex(
            self.src,
            r'getElementById\(["\']forgot["\']\)',
            "No JS handler attached to #forgot — clicking the link "
            "will navigate to '#' and do nothing.",
        )
        self.assertIn(
            'id="resetHelp"', self.src,
            "Reset-instructions panel (#resetHelp) is missing — "
            "toggle target gone.",
        )


class JobTreeRunningBadge(unittest.TestCase):
    """Task 6. Running job must show an elapsed-time badge in the
    tree so ~14-minute jobs don't look stalled."""

    def setUp(self) -> None:
        self.html = _DASHBOARD.read_text(encoding="utf-8")

    def test_render_job_node_accepts_running_param(self) -> None:
        """renderJobNode must take a running-state argument and
        compare node.name against it. If the signature regresses
        to ``(node, depth)`` the tree goes silent again."""
        self.assertRegex(
            self.html,
            r"function renderJobNode\(\s*node\s*,\s*depth\s*,\s*running",
            "renderJobNode no longer accepts a 'running' argument — "
            "tree won't know which node is currently executing.",
        )
        # Call site must pass the current-action through.
        self.assertRegex(
            self.html,
            r"renderJobNode\(\s*d\.tree\s*,\s*0\s*,\s*cur\s*\)",
            "loadJobTree must pass 'cur' (current_action) into "
            "renderJobNode — otherwise the badge never renders.",
        )

    def test_running_badge_css_present(self) -> None:
        self.assertIn(
            ".job-running", self.html,
            "CSS rule for .job-running dropped — running badge "
            "renders as unstyled text.",
        )
        self.assertIn(
            "@keyframes jobPulse", self.html,
            "jobPulse keyframes dropped — badge no longer signals "
            "liveness, users may still think the job is stalled.",
        )

    def test_auto_refresh_while_running_is_under_three_seconds(self) -> None:
        """The elapsed-time badge is useful only if it updates
        visibly. Originally set to 5s; tightened to 2s so the
        'running 1m 23s' text actually ticks."""
        m = re.search(
            r"loadJobTree\(\)[^}]*?\}\s*,\s*(\d+)\s*\)\s*;?\s*\}",
            self.html,
        )
        self.assertIsNotNone(
            m, "Could not locate the loadJobTree auto-refresh setTimeout",
        )
        self.assertLessEqual(
            int(m.group(1)), 3000,
            f"Job-tree auto-refresh is {m.group(1)}ms — too slow "
            "for the elapsed-time badge to feel live. Keep it <=3000ms.",
        )


if __name__ == "__main__":
    unittest.main()
