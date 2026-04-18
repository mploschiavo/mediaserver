"""Translate Role → provider-specific payload. Stateless, pure."""

from __future__ import annotations

from typing import Any

from media_stack.core.auth.users.models import Role


class RolePolicyMapper:
    """Reads role.sso_groups + role.provider_payloads[provider_id].

    The mapper knows nothing about specific backends — it returns the raw
    payload the role catalog configured, keyed by provider id.
    """

    def sso_groups(self, role: Role) -> list[str]:
        return list(role.sso_groups)

    def payload_for(self, role: Role, provider_id: str) -> dict[str, Any]:
        return dict(role.provider_payloads.get(provider_id, {}))
