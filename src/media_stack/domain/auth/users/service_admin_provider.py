"""ServiceAdminProvider protocol — single-login services.

Some services in the stack (qBittorrent, Sonarr, Radarr, Bazarr,
Prowlarr, SABnzbd) have no user directory — they're managed through a
single admin credential that the controller uses to call their APIs.
This protocol is the counterpart to UserProvider for those services.

When a user's role has ``propagate_to_service_admins = true``, password
changes on that user are pushed to every ServiceAdminProvider by
UserService.reset_password.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class ServiceAdminHealth:
    ok: bool
    detail: str = ""


class ServiceAdminProvider(Protocol):
    """Minimum surface for a single-admin service backend."""

    name: str

    def health_check(self) -> ServiceAdminHealth: ...
    def set_admin_password(self, password: str) -> None: ...
