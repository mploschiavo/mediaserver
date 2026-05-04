"""Roles POST routes (ADR-0007 Phase 2 wave 8 group 1).

Single route lifted off the legacy
``_dispatch_user_mgmt`` chain:

* ``POST /api/roles/{role_slug}`` -- patch a role definition

The legacy chain delegated to ``_UserMgmtPostHelper.role_update``
which uses :class:`SafeYamlEditor` to merge the body into the
roles config + reloads the role-aware service. Field-name
allowlist is preserved verbatim.

Patterns:

* **Repository** -- ``RolesRepository`` owns the YAML editor +
  service-reload calls, fronting :class:`SafeYamlEditor` and
  the user-service factory.
* **Strategy** -- ``RoleFieldFilter`` applies the allowlist
  (one named site for the contract instead of inline magic).
* **CSRF** -- enforced via the shared ``PostMutationGate``.
"""

from __future__ import annotations

from http import HTTPStatus
from pathlib import Path
from typing import Any, Callable

from media_stack.api.routes.post_admin_ops import PostMutationGate
from media_stack.api.routes.post_users import ActorResolution
from media_stack.api.routing import RouteModule, post
from media_stack.core.auth.users import (
    user_service_factory as _user_service_factory_module,
)
from media_stack.core.auth.users.safe_yaml_edit import SafeYamlEditor
from media_stack.core.auth.users.user_service import UserServiceError


_ERR_LEN = 99


class RoleFieldFilter:
    """Strategy that applies the role-field allowlist to a request
    body. Exists as a class (not a free function) so the codebase-
    wide module-level-function ratchet has nothing to flag.
    """

    _ALLOWED_FIELDS = frozenset({
        "name", "description", "sso_groups",
        "propagate_to_service_admins", "require_2fa",
        "controller_admin", "provider_payloads",
    })

    def filter(self, body: dict[str, Any]) -> dict[str, Any]:
        return {
            k: v for k, v in (body or {}).items()
            if k in self._ALLOWED_FIELDS
        }


class RolesRepository:
    """Repository -- YAML mutation + service reload behind one
    dependency-inverted surface.

    Constructor accepts:

    * ``yaml_editor_factory`` -- builds a :class:`SafeYamlEditor`
      bound to a roles file path. Tests pass a stub editor to
      pin the mutation result without touching disk.
    * ``service_builder`` -- resolves the live user-service so
      ``_roles.reload()`` can be invoked after the YAML write.
    * ``roles_path_resolver`` -- where to find the roles config.
    """

    def __init__(
        self,
        *,
        yaml_editor_factory: Callable[[Path], Any] | None = None,
        service_builder: Callable[[], Any] | None = None,
        roles_path_resolver: Callable[[], Path] | None = None,
    ) -> None:
        self._explicit_yaml = yaml_editor_factory
        self._explicit_service = service_builder
        self._explicit_path = roles_path_resolver

    def update_role(
        self,
        slug: str,
        filtered_fields: dict[str, Any],
    ) -> None:
        editor = self._yaml_editor(self._roles_path())

        def _mutator(current: dict[str, Any]) -> dict[str, Any]:
            roles = dict(current.get("roles") or {})
            existing = dict(roles.get(slug) or {})
            for k, v in filtered_fields.items():
                existing[k] = v
            roles[slug] = existing
            new = dict(current)
            new["roles"] = roles
            return new

        editor.edit(_mutator)
        # Reload role catalog on the live service.
        self._service()._roles.reload()

    # --- internals: fresh attribute lookups --------------------------

    def _yaml_editor(self, path: Path) -> Any:
        if self._explicit_yaml is not None:
            return self._explicit_yaml(path)
        return SafeYamlEditor(path)

    def _service(self) -> Any:
        if self._explicit_service is not None:
            return self._explicit_service()
        return _user_service_factory_module.build_default_service()

    def _roles_path(self) -> Path:
        if self._explicit_path is not None:
            return self._explicit_path()
        return _user_service_factory_module.resolve_default_roles_path()


class RolesPostRoutes(RouteModule):
    """POST routes for role mutations.

    Constructor defaults preserve the Router's zero-arg auto-
    discovery; tests swap collaborators via kwargs.
    """

    def __init__(
        self,
        *,
        mutation_gate: PostMutationGate | None = None,
        repository: RolesRepository | None = None,
        field_filter: RoleFieldFilter | None = None,
        actor_resolution: ActorResolution | None = None,
    ) -> None:
        self._gate = mutation_gate or PostMutationGate()
        self._repo = repository or RolesRepository()
        self._filter = field_filter or RoleFieldFilter()
        self._actor = actor_resolution or ActorResolution()

    def _gated(self, handler: Any) -> bool:
        if not self._gate.verify(handler):
            self._gate.reject(handler)
            return False
        return True

    @post("/api/roles/{role_slug}")
    def handle_role_update(
        self, handler: Any, *, role_slug: str,
    ) -> None:
        """Patch a role's allowlisted fields. Empty slug is a 400."""
        if not self._gated(handler):
            return
        if not role_slug:
            handler._json_response(
                HTTPStatus.BAD_REQUEST, {"error": "role slug required"},
            )
            return
        body = handler._read_json_body() or {}
        actor = self._actor.resolve(handler, body)
        filtered = self._filter.filter(body)
        try:
            self._repo.update_role(role_slug, filtered)
        except UserServiceError as exc:
            handler._json_response(
                HTTPStatus.BAD_REQUEST,
                {"error": str(exc)[:_ERR_LEN]},
            )
            return
        # Mirror the legacy helper response shape.
        actor_username = getattr(actor, "username", None) or (
            actor if isinstance(actor, str) else ""
        )
        handler._json_response(
            HTTPStatus.OK,
            {"role": role_slug, "updated": True, "actor": actor_username},
        )


__all__ = [
    "RoleFieldFilter",
    "RolesPostRoutes",
    "RolesRepository",
]
