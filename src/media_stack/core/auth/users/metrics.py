"""Prometheus text-format metrics for user management.

Rendered on demand from the live controller state — no background
collector needed. Exposed at /metrics by the API layer.
"""

from __future__ import annotations

from typing import Any


class UserMgmtMetricsRenderer:
    """Builds Prometheus text from a snapshot of user-mgmt state."""

    def render(
        self,
        *,
        users: list[dict[str, Any]],
        roles: list[dict[str, Any]],
        provider_health: list[dict[str, Any]],
        audit_recent: list[dict[str, Any]],
        drift_summary: dict[str, dict[str, int]] | None = None,
        security_counts: dict[str, int] | None = None,
    ) -> str:
        lines: list[str] = []
        self._render_users(users, lines)
        self._render_roles(roles, lines)
        self._render_providers(provider_health, lines)
        if drift_summary:
            self._render_drift(drift_summary, lines)
        self._render_audit(audit_recent, lines)
        if security_counts:
            self._render_security_counters(security_counts, lines)
        return "\n".join(lines) + "\n"

    def _render_users(self, users, lines):
        lines.extend([
            "# HELP media_stack_users_total Number of users by state",
            "# TYPE media_stack_users_total gauge",
        ])
        states: dict[str, int] = {}
        for u in users:
            state = u.get("state", "unknown")
            states[state] = states.get(state, 0) + 1
        for state, count in sorted(states.items()):
            lines.append(
                f'media_stack_users_total{{state="{self._escape(state)}"}} {count}'
            )

    def _render_roles(self, roles, lines):
        lines.extend([
            "# HELP media_stack_roles_total Number of defined roles",
            "# TYPE media_stack_roles_total gauge",
            f"media_stack_roles_total {len(roles)}",
        ])

    def _render_providers(self, provider_health, lines):
        lines.extend([
            "# HELP media_stack_user_provider_up Provider health (1=ok, 0=fail)",
            "# TYPE media_stack_user_provider_up gauge",
        ])
        for p in provider_health:
            name = self._escape(p.get("name", ""))
            ok = 1 if p.get("ok") else 0
            lines.append(
                f'media_stack_user_provider_up{{provider="{name}"}} {ok}'
            )

    def _render_drift(self, drift_summary, lines):
        lines.extend([
            "# HELP media_stack_user_drift Orphans/ghosts per provider",
            "# TYPE media_stack_user_drift gauge",
        ])
        for provider, stats in drift_summary.items():
            for metric, count in stats.items():
                lines.append(
                    f'media_stack_user_drift{{provider="{self._escape(provider)}",'
                    f'kind="{self._escape(metric)}"}} {count}'
                )

    def _render_audit(self, audit_recent, lines):
        lines.extend([
            "# HELP media_stack_audit_actions_total Audit log entries by action",
            "# TYPE media_stack_audit_actions_total counter",
        ])
        action_counts: dict[str, int] = {}
        for e in audit_recent:
            action = e.get("action", "unknown")
            action_counts[action] = action_counts.get(action, 0) + 1
        for action, count in sorted(action_counts.items()):
            lines.append(
                f'media_stack_audit_actions_total{{action="{self._escape(action)}"}} {count}'
            )

    def _render_security_counters(self, counts, lines):
        lines.extend([
            "# HELP media_stack_security_event_total "
            "Controller-side security rejections by event type",
            "# TYPE media_stack_security_event_total counter",
        ])
        for event, n in sorted(counts.items()):
            lines.append(
                f'media_stack_security_event_total{{event="{self._escape(event)}"}} {n}'
            )

    def _escape(self, s: str) -> str:
        return str(s).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


_renderer = UserMgmtMetricsRenderer()
render_metrics = _renderer.render
