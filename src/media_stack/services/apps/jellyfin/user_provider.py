"""Jellyfin UserProvider — HTTP API backed.

Projects users into Jellyfin via /Users endpoints. Passwords, display
name, per-library access, parental rating ceiling, max active sessions,
etc. are set via this provider's policy payload.
"""

from __future__ import annotations

import logging
from http import HTTPStatus
from typing import Any

from media_stack.core.auth.users.provider import (
    ExternalUser,
    ProviderCapabilities,
    ProviderHealth,
)
from media_stack.core.http import HttpClient

_log = logging.getLogger("media_stack")

_OK_STATUSES = (HTTPStatus.OK, HTTPStatus.NO_CONTENT)
_CREATE_STATUSES = (HTTPStatus.OK, HTTPStatus.CREATED)
_DELETE_STATUSES = (HTTPStatus.OK, HTTPStatus.NO_CONTENT, HTTPStatus.NOT_FOUND)
_ERR_DETAIL_LEN = 99


class JellyfinProviderError(RuntimeError):
    pass


class JellyfinApiProvider:

    name = "jellyfin"
    capabilities = ProviderCapabilities(
        source_of_truth=False,
        supports_groups=False,
        supports_password=True,
        supports_policy=True,
        auto_provisions_on_login=False,
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
                self._base_url, "/System/Info", api_key=self._api_key,
            )
        except Exception as exc:  # noqa: BLE001
            return ProviderHealth(ok=False, detail=str(exc)[:_ERR_DETAIL_LEN])
        return ProviderHealth(ok=HTTPStatus.OK <= status < HTTPStatus.MULTIPLE_CHOICES,
                              detail=f"status={status}")

    def list_users(self) -> list[ExternalUser]:
        if not self._api_key:
            return []
        try:
            status, users, _ = self._http.request(
                self._base_url, "/Users", api_key=self._api_key,
            )
        except Exception:  # noqa: BLE001
            return []
        if status != HTTPStatus.OK or not isinstance(users, list):
            return []
        return [self._to_external_user(u) for u in users if isinstance(u, dict)]

    def _to_external_user(self, u: dict) -> ExternalUser:
        return ExternalUser(
            external_id=str(u.get("Id", "")),
            username=str(u.get("Name", "")),
            email="",
            groups=[],
            extra={"HasPassword": bool(u.get("HasPassword", False))},
        )

    def create_user(self, *, username: str, email: str, display_name: str,
                    password: str, groups: list[str],
                    policy: dict[str, Any] | None = None) -> ExternalUser:
        del email, groups
        self._require_api_key()
        status, body, text = self._http.request(
            self._base_url, "/Users/New",
            api_key=self._api_key, method="POST",
            payload={"Name": username, "Password": password},
        )
        if status not in _CREATE_STATUSES or not isinstance(body, dict):
            raise JellyfinProviderError(
                f"create failed: status={status} body={text[:_ERR_DETAIL_LEN]}"
            )
        user_id = str(body.get("Id", ""))
        if not user_id:
            raise JellyfinProviderError("create returned no Id")
        if policy:
            self._apply_policy(user_id, self._resolve_policy(policy))
        return ExternalUser(
            external_id=user_id, username=username, email="",
            groups=[], extra={"display_name": display_name},
        )

    def update_user(self, external_id: str, *, display_name: str = "",
                    email: str = "",
                    groups: list[str] | None = None,
                    policy: dict[str, Any] | None = None) -> ExternalUser:
        del email, groups, display_name
        if policy:
            self._apply_policy(external_id, self._resolve_policy(policy))
        return ExternalUser(
            external_id=external_id, username="", email="",
            groups=[], extra={},
        )

    def _resolve_policy(self, policy: dict[str, Any]) -> dict[str, Any]:
        """Resolve role-catalog payload into a Jellyfin-ready policy dict.

        The role catalog lets admins declare ``EnabledFolderNames`` (human
        library names like ``["Kids"]``). This method fetches the live
        library list and translates names → IDs, populating
        ``EnabledFolders`` on the outgoing policy. Missing libraries are
        dropped and logged — we prefer a partial restriction over an
        error that blocks the whole role change.
        """
        names = policy.pop("EnabledFolderNames", None)
        if not names:
            return policy
        folder_ids = self._library_ids_by_name(names)
        resolved = dict(policy)
        resolved["EnabledFolders"] = folder_ids
        resolved.setdefault("EnableAllFolders", False)
        return resolved

    def _library_ids_by_name(self, names: list[str]) -> list[str]:
        try:
            status, body, _ = self._http.request(
                self._base_url, "/Library/MediaFolders",
                api_key=self._api_key,
            )
        except Exception:  # noqa: BLE001
            return []
        if status != HTTPStatus.OK or not isinstance(body, dict):
            return []
        items = body.get("Items") or []
        lookup = {
            str(item.get("Name", "")).strip().lower(): str(item.get("Id", ""))
            for item in items
            if isinstance(item, dict)
        }
        ids: list[str] = []
        for n in names:
            key = str(n).strip().lower()
            folder_id = lookup.get(key)
            if folder_id:
                ids.append(folder_id)
        return ids

    def delete_user(self, external_id: str) -> None:
        self._require_api_key()
        status, _, text = self._http.request(
            self._base_url, f"/Users/{external_id}",
            api_key=self._api_key, method="DELETE",
        )
        if status not in _DELETE_STATUSES:
            raise JellyfinProviderError(
                f"delete failed: status={status} body={text[:_ERR_DETAIL_LEN]}"
            )

    def set_password(self, external_id: str, password: str) -> None:
        self._require_api_key()
        status, _, text = self._http.request(
            self._base_url, f"/Users/{external_id}/Password",
            api_key=self._api_key, method="POST",
            payload={"NewPw": password, "ResetPassword": False},
        )
        if status not in _OK_STATUSES:
            raise JellyfinProviderError(
                f"password reset failed: status={status} body={text[:_ERR_DETAIL_LEN]}"
            )

    def _apply_policy(self, external_id: str, policy: dict[str, Any]) -> None:
        status, _, text = self._http.request(
            self._base_url, f"/Users/{external_id}/Policy",
            api_key=self._api_key, method="POST", payload=policy,
        )
        if status not in _OK_STATUSES:
            raise JellyfinProviderError(
                f"policy update failed: status={status} body={text[:_ERR_DETAIL_LEN]}"
            )

    def _require_api_key(self) -> None:
        if not self._api_key:
            raise JellyfinProviderError("API key not set")

    def revoke_sessions(self, external_id: str) -> None:
        """Kill every active session belonging to this user.

        Best-effort — if Jellyfin is unreachable or the user no longer
        exists, we swallow and return. Callers should tolerate that.
        """
        if not external_id or not self._api_key:
            return
        try:
            status, body, _ = self._http.request(
                self._base_url, "/Sessions", api_key=self._api_key,
            )
        except Exception as exc:  # noqa: BLE001
            _log.debug("[DEBUG] list sessions failed: %s", exc)
            return
        if status != HTTPStatus.OK or not isinstance(body, list):
            return
        for session in body:
            if not isinstance(session, dict):
                continue
            if str(session.get("UserId", "")) != external_id:
                continue
            session_id = str(session.get("Id", ""))
            if not session_id:
                continue
            try:
                self._http.request(
                    self._base_url, f"/Sessions/{session_id}",
                    api_key=self._api_key, method="DELETE",
                )
            except Exception as exc:  # noqa: BLE001
                _log.debug("[DEBUG] revoke %s failed: %s", session_id, exc)
                continue
