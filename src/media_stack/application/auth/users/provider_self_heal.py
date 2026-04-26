"""Self-heal a provider row that went missing behind the controller.

Call site: ``UserWriteService._forall_providers_set_password``. When
the provider raises ``user not found`` during a password-rotation the
controller's store-side record still thinks the row is there (the
provider_ref is set), but the external record was wiped — usually
because the provider's DB was rebuilt, a restored backup dropped it,
or someone hand-edited users_database.yml and saved the file without
the entry.

Rather than returning a silent ``error: user not found`` that leaves
the operator to figure out the mismatch, the healer recreates the
row with the current password + role-derived groups. Idempotent from
the controller's point of view — set_password flow continues as if
nothing was wrong, with a ``healed`` status in the result map so the
audit log makes the self-repair visible.

Moved out of user_write_service.py in v1.0.169 so that file stays
under the 400-line ratchet without losing test coverage — tests pick
this up via the module-level ``_provider_self_healer`` instance.
"""

from __future__ import annotations


class ProviderSelfHealer:

    def heal(self, provider, user, password, original_exc, role) -> bool:
        if "not found" not in str(original_exc).lower():
            return False
        if not hasattr(provider, "create_user"):
            return False
        groups = self._groups_from_role(role, provider.name)
        external_id = user.provider_refs.get(provider.name) or user.username
        try:
            provider.create_user(
                username=external_id,
                email=user.email,
                display_name=user.display_name or user.username,
                password=password,
                groups=groups,
            )
            return True
        except Exception:  # noqa: BLE001
            return False

    def _groups_from_role(self, role, provider_name: str) -> list[str]:
        if role is None:
            return []
        payload = (getattr(role, "provider_payloads", {}) or {}).get(
            provider_name, {})
        raw_groups = payload.get("groups") if isinstance(payload, dict) else None
        if isinstance(raw_groups, list):
            return [str(g) for g in raw_groups if g]
        return []


_provider_self_healer = ProviderSelfHealer()
# Kept as a module-level callable so the existing import path in
# user_write_service and its tests stays stable.
heal_missing_provider_user = _provider_self_healer.heal
