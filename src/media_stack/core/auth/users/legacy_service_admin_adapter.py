"""Adapter that wraps the existing api/services/admin.py:reset_password
logic as a ServiceAdminProvider.

The legacy admin module already knows how to reset passwords per service
(qBit via setPreferences, Arrs via password_api_path, Bazarr via PATCH
system settings, etc.). This adapter lets each of those capabilities
plug into the new per-user password-propagation flow without duplicating
HTTP code.
"""

from __future__ import annotations

from typing import Callable

from media_stack.core.auth.users.service_admin_provider import ServiceAdminHealth

_ERR_LEN = 99


class LegacyServiceAdminAdapter:
    """One instance per service id; calls the shared reset-password path
    with a single-service filter.
    """

    def __init__(
        self,
        service_id: str,
        *,
        reset_fn: Callable[..., dict],
        probe_fn: Callable[[str], ServiceAdminHealth] | None = None,
    ) -> None:
        self.name = service_id
        self._reset_fn = reset_fn
        self._probe_fn = probe_fn

    def health_check(self) -> ServiceAdminHealth:
        if self._probe_fn is None:
            return ServiceAdminHealth(ok=True, detail="probe not configured")
        try:
            return self._probe_fn(self.name)
        except Exception as exc:  # noqa: BLE001
            return ServiceAdminHealth(ok=False, detail=str(exc)[:_ERR_LEN])

    def set_admin_password(self, password: str) -> None:
        result = self._reset_fn(password, target_services=[self.name])
        errors = result.get("errors") or []
        services = result.get("services") or []
        if self.name not in services and errors:
            raise RuntimeError("; ".join(str(e) for e in errors)[:_ERR_LEN])
