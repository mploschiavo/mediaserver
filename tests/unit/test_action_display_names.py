"""Every action must have a meaningful display name in the dashboard.

Actions show up in the Activity table and Gantt chart. Raw internal names
like 'post-setup' or 'configure-media-server' are not user-friendly.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DASHBOARD = ROOT / "src" / "media_stack" / "api" / "dashboard.html"


def _extract_action_labels() -> dict[str, str]:
    """Parse ACTION_LABELS map from dashboard.html JavaScript."""
    text = DASHBOARD.read_text(encoding="utf-8")
    match = re.search(r"const ACTION_LABELS=\{([^}]+)\}", text, re.DOTALL)
    if not match:
        return {}
    block = match.group(1)
    labels = {}
    for m in re.finditer(r"'([^']+)'\s*:\s*'([^']+)'", block):
        labels[m.group(1)] = m.group(2)
    return labels


def _get_known_actions() -> set[str]:
    """Get all action names from the server ACTION_PRIORITY map."""
    try:
        from media_stack.api.server import ACTION_PRIORITY
        return set(ACTION_PRIORITY.keys())
    except Exception:
        return set()


def _get_contract_job_names() -> set[str]:
    """Get job names discovered from service contracts."""
    try:
        from media_stack.cli.commands.job_framework import discover_jobs_from_contracts
        return {j["name"] for j in discover_jobs_from_contracts()}
    except Exception:
        return set()


class TestActionDisplayNames(unittest.TestCase):
    """Every action visible to users must have a meaningful display label."""

    def test_all_priority_actions_have_labels(self):
        labels = _extract_action_labels()
        actions = _get_known_actions()
        missing = actions - set(labels.keys())
        self.assertFalse(
            missing,
            f"Actions in ACTION_PRIORITY without display labels in dashboard:\n"
            + "\n".join(f"  - {a}" for a in sorted(missing))
            + "\n\nAdd to ACTION_LABELS in dashboard.html",
        )

    def test_all_contract_jobs_have_labels(self):
        labels = _extract_action_labels()
        jobs = _get_contract_job_names()
        missing = jobs - set(labels.keys())
        self.assertFalse(
            missing,
            f"Contract jobs without display labels in dashboard:\n"
            + "\n".join(f"  - {j}" for j in sorted(missing))
            + "\n\nAdd to ACTION_LABELS in dashboard.html",
        )

    def test_labels_are_meaningful(self):
        """Labels should not just be title-cased versions of the action ID."""
        labels = _extract_action_labels()
        bad = []
        for action_id, label in labels.items():
            # Title-cased with hyphens replaced = not meaningful
            trivial = action_id.replace("-", " ").title()
            if label == trivial:
                bad.append(f"{action_id}: '{label}' is just title-cased ID")
            # Too short
            if len(label) < 8:
                bad.append(f"{action_id}: '{label}' is too short to be descriptive")
        self.assertFalse(
            bad,
            f"Labels that are not meaningful:\n"
            + "\n".join(f"  - {b}" for b in bad),
        )

    def test_dashboard_uses_action_label_function(self):
        """The dashboard must use actionLabel() not raw a.name for display."""
        text = DASHBOARD.read_text(encoding="utf-8")
        # Find places where a.name is rendered directly (not via actionLabel)
        # Allow a.name in onclick handlers (filterLogsToAction) but not in display text
        raw_renders = re.findall(r">\s*'\s*\+\s*a\.name\s*\+\s*'", text)
        self.assertFalse(
            raw_renders,
            f"Dashboard renders raw a.name {len(raw_renders)} time(s) instead of actionLabel(a.name)",
        )


if __name__ == "__main__":
    unittest.main()
