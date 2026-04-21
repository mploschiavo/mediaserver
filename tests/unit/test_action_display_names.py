"""Every action must have a meaningful display name.

After the action→job migration, the rule is: the dashboard's
``actionLabel(name)`` function returns either a small override
(for IDs whose humanised slug is too terse, like ``bootstrap`` →
"Setup All Services") OR a title-cased humanisation of the slug
itself (e.g., ``configure-libraries`` → "Configure Libraries").

This test pins three properties:

1. Every action visible in the dashboard's headline buttons + Job
   tree + Recent Activity has a non-empty label.
2. The override table is short — adding cute renames per-action
   would diverge the Recent Activity log from the Job tree (the
   pre-migration bug).
3. The dashboard renders via ``actionLabel(name)``, never raw
   ``a.name``."""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DASHBOARD = ROOT / "src" / "media_stack" / "api" / "dashboard.html"


def _extract_label_overrides() -> dict[str, str]:
    """Parse the small ``ACTION_LABEL_OVERRIDES`` table from
    dashboard.html. The previous, much larger ``ACTION_LABELS``
    table was deleted in the action→job unification."""
    text = DASHBOARD.read_text(encoding="utf-8")
    match = re.search(
        r"const ACTION_LABEL_OVERRIDES\s*=\s*\{([^}]+)\}",
        text, re.DOTALL,
    )
    if not match:
        return {}
    block = match.group(1)
    return {
        m.group(1): m.group(2)
        for m in re.finditer(r"'([^']+)'\s*:\s*'([^']+)'", block)
    }


def _humanise(action_id: str) -> str:
    """Mirror of dashboard.html's ``actionLabel`` fallback —
    title-case the slug with hyphens as spaces."""
    return action_id.replace("-", " ").title()


def _action_label(action_id: str, overrides: dict[str, str]) -> str:
    return overrides.get(action_id) or _humanise(action_id)


def _get_known_actions() -> set[str]:
    try:
        from media_stack.api.server import ACTION_PRIORITY
        return set(ACTION_PRIORITY.keys())
    except Exception:
        return set()


def _get_contract_job_names() -> set[str]:
    try:
        from media_stack.cli.commands.job_framework import (
            discover_jobs_from_contracts,
        )
        return {j["name"] for j in discover_jobs_from_contracts()}
    except Exception:
        return set()


class TestActionDisplayNames(unittest.TestCase):

    def test_every_known_action_resolves_to_a_label(self) -> None:
        """The override table is intentionally tiny; the
        humanised-slug fallback must give every action a label."""
        overrides = _extract_label_overrides()
        actions = _get_known_actions() | _get_contract_job_names()
        for action_id in sorted(actions):
            label = _action_label(action_id, overrides)
            self.assertTrue(
                label and len(label) >= 4,
                f"actionLabel({action_id!r}) returned {label!r}; "
                "every action must resolve to a non-empty, "
                ">=4-char string.",
            )

    def test_overrides_are_shorter_than_pre_migration(self) -> None:
        """The pre-migration ``ACTION_LABELS`` table had ~22 cute
        renames that diverged from the job tree (the user-visible
        bug we just fixed). The replacement override table should
        stay tiny — only IDs whose humanised slug is too terse to
        be useful at the top of the dashboard."""
        overrides = _extract_label_overrides()
        self.assertLessEqual(
            len(overrides), 5,
            f"ACTION_LABEL_OVERRIDES has {len(overrides)} entries; "
            "keep it small. Add a contract job and let "
            "actionLabel() humanise the slug instead of paving "
            "over it with a custom name.",
        )

    def test_dashboard_uses_action_label_function(self) -> None:
        """Pin: the dashboard must call ``actionLabel(name)``
        rather than rendering the raw slug. Otherwise the override
        + humanise pipeline doesn't get a chance to run."""
        text = DASHBOARD.read_text(encoding="utf-8")
        raw_renders = re.findall(
            r">\s*'\s*\+\s*a\.name\s*\+\s*'", text,
        )
        self.assertFalse(
            raw_renders,
            f"Dashboard renders raw a.name {len(raw_renders)} "
            "time(s) instead of actionLabel(a.name)",
        )


if __name__ == "__main__":
    unittest.main()
