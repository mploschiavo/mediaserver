"""Jellyseerr UserProvider — auto-provisioning via OIDC first-login.

Jellyseerr creates its own user record the first time someone logs in
via OIDC. The controller never calls ``/Users/New`` here; create is a
no-op (``deferred_oidc_first_login``). What the controller DOES do:

- After first login, a reconcile sweep picks up the new Jellyseerr user
  as an orphan and the admin imports it (linking it to the existing
  controller row by email / username).
- Once linked, role changes / password resets propagate via
  ``update_user`` — patching ``/api/v1/user/{id}`` with the role's
  permission mask + request quotas.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

from media_stack.core.auth.users.provider import (
    ExternalUser,
    ProviderCapabilities,
    ProviderHealth,
)
from media_stack.core.http import HttpClient

_OK_STATUSES = (HTTPStatus.OK, HTTPStatus.NO_CONTENT)
_ERR_DETAIL_LEN = 99


class JellyseerrProviderError(RuntimeError):
    pass


class JellyseerrApiProvider:

    name = "jellyseerr"
    capabilities = ProviderCapabilities(
        source_of_truth=False,
        supports_groups=False,
        supports_password=False,  # Jellyseerr passwords come from OIDC
        supports_policy=True,
        auto_provisions_on_login=True,
    )

    def __init__(self, base_url: str, api_key: str,
                 http_client: HttpClient | None = None) -> None:
        self._base_url = str(base_url).rstrip("/")
        self._api_key = api_key
        self._http = http_client or HttpClient()

    def health_check(self) -> ProviderHealth:
        if not self._api_key:
            return ProviderHealth(ok=False, detail="API key not set")
        try:
            status, _, _ = self._http.request(
                self._base_url, "/api/v1/status", api_key=self._api_key,
            )
        except Exception as exc:  # noqa: BLE001
            return ProviderHealth(ok=False, detail=str(exc)[:_ERR_DETAIL_LEN])
        return ProviderHealth(
            ok=HTTPStatus.OK <= status < HTTPStatus.MULTIPLE_CHOICES,
            detail=f"status={status}",
        )

    def list_users(self) -> list[ExternalUser]:
        if not self._api_key:
            return []
        try:
            status, body, _ = self._http.request(
                self._base_url, "/api/v1/user", api_key=self._api_key,
            )
        except Exception:  # noqa: BLE001
            return []
        if status != HTTPStatus.OK:
            return []
        items = []
        if isinstance(body, dict):
            items = body.get("results") or []
        elif isinstance(body, list):
            items = body
        return [self._to_external_user(u) for u in items if isinstance(u, dict)]

    def _to_external_user(self, u: dict) -> ExternalUser:
        return ExternalUser(
            external_id=str(u.get("id", "")),
            username=str(u.get("jellyfinUsername") or u.get("username")
                          or u.get("plexUsername") or ""),
            email=str(u.get("email", "")),
            groups=[],
            extra={"userType": u.get("userType")},
        )

    def create_user(self, *, username: str, email: str, display_name: str,
                    password: str, groups: list[str],
                    policy: dict[str, Any] | None = None) -> ExternalUser:
        """No-op: Jellyseerr auto-provisions on first OIDC login.

        Returning a placeholder here keeps UserService's orchestration flow
        intact. The actual row is filled in on reconcile after the user
        logs in for the first time.
        """
        del email, groups, password, policy
        return ExternalUser(
            external_id="",  # no ID yet — reconcile will link later
            username=username, email="", groups=[],
            extra={"display_name": display_name,
                   "deferred": "oidc_first_login"},
        )

    def update_user(self, external_id: str, *, display_name: str = "",
                    email: str = "",
                    groups: list[str] | None = None,
                    policy: dict[str, Any] | None = None) -> ExternalUser:
        del email, groups, display_name
        if not external_id:
            # User hasn't been provisioned yet (still awaiting OIDC first
            # login). Skip silently; reconcile will re-run this once the
            # external_id is known.
            return ExternalUser(external_id="", username="", email="",
                                 groups=[], extra={})
        if policy:
            self._apply_permissions(external_id, policy)
        return ExternalUser(external_id=external_id, username="", email="",
                            groups=[], extra={})

    def delete_user(self, external_id: str) -> None:
        if not external_id or not self._api_key:
            return
        try:
            self._http.request(
                self._base_url, f"/api/v1/user/{external_id}",
                api_key=self._api_key, method="DELETE",
            )
        except Exception as exc:  # noqa: BLE001
            raise JellyseerrProviderError(
                f"delete failed: {str(exc)[:_ERR_DETAIL_LEN]}",
            ) from exc

    def set_password(self, external_id: str, password: str) -> None:
        # Jellyseerr logins are federated via OIDC — no password to set.
        del external_id, password

    def revoke_sessions(self, external_id: str) -> None:
        """Jellyseerr doesn't expose an explicit session revocation API.

        Deleting the user (via ``delete_user``) invalidates their cookie
        on the next request because the user record is gone. Leave this
        as a no-op.
        """
        del external_id

    def _apply_permissions(self, external_id: str, payload: dict[str, Any]) -> None:
        body = self._build_permissions_body(payload)
        if not body:
            return
        try:
            status, _, text = self._http.request(
                self._base_url, f"/api/v1/user/{external_id}",
                api_key=self._api_key, method="PUT", payload=body,
            )
        except Exception as exc:  # noqa: BLE001
            raise JellyseerrProviderError(
                f"permission patch failed: {str(exc)[:_ERR_DETAIL_LEN]}",
            ) from exc
        if status not in _OK_STATUSES:
            raise JellyseerrProviderError(
                f"permission patch rejected: status={status} "
                f"body={text[:_ERR_DETAIL_LEN]}",
            )

    def _build_permissions_body(self, payload: dict[str, Any]) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if "permissions" in payload:
            body["permissions"] = int(payload["permissions"])
        quota = payload.get("request_quota") or {}
        movies = int(quota.get("movies", 0))
        tv = int(quota.get("tv", 0))
        if movies or tv:
            body["movieQuotaLimit"] = movies
            body["movieQuotaDays"] = 7
            body["tvQuotaLimit"] = tv
            body["tvQuotaDays"] = 7
        return body
