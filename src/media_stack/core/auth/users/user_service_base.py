"""UserServiceBase — shared constructor + accessors for the user service
family. Split out so user_service.py stays under the file-size ratchet.
"""

from __future__ import annotations

from media_stack.core.auth.users.audit_log import AuditLog
from media_stack.core.auth.users.password_policy import PasswordPolicy
from media_stack.core.auth.users.provider import UserProvider
from media_stack.core.auth.users.role_catalog import RoleCatalog
from media_stack.core.auth.users.role_policy_mapper import RolePolicyMapper
from media_stack.core.auth.users.service_admin_provider import (
    ServiceAdminProvider,
)
from media_stack.core.auth.users.user_store import UserStore


class UserServiceError(RuntimeError):
    pass


class UserServiceBase:
    """Shared state + helpers for UserQueryService and UserWriteService."""

    def __init__(
        self,
        *,
        store: UserStore,
        role_catalog: RoleCatalog,
        mapper: RolePolicyMapper,
        providers: list[UserProvider],
        audit: AuditLog,
        service_admins: list[ServiceAdminProvider] | None = None,
        password_policy: PasswordPolicy | None = None,
    ) -> None:
        self._store = store
        self._roles = role_catalog
        self._mapper = mapper
        self._providers = list(providers)
        self._audit = audit
        self._service_admins = list(service_admins or [])
        self._policy = password_policy or PasswordPolicy()

    def _source_of_truth(self) -> UserProvider | None:
        for p in self._providers:
            if getattr(p.capabilities, "source_of_truth", False):
                return p
        return None

    def _secondary_providers(self) -> list[UserProvider]:
        return [p for p in self._providers
                if not getattr(p.capabilities, "source_of_truth", False)]
