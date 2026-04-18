"""ServiceAdminProvider for Sonarr/Radarr/Lidarr/Readarr.

All four share the same v3 API shape:
  GET /api/v3/config/host      → current host config
  PUT /api/v3/config/host      → update username/password/auth method

The legacy path (api/services/admin.py) used raw urllib, which raises on
307 Temporary Redirect when the service has ``urlBase`` configured
(e.g. /app/sonarr). This provider uses the core HttpClient which follows
307/308 while preserving the original method + body.
"""

from __future__ import annotations

import logging
from http import HTTPStatus
from typing import Any

from media_stack.core.auth.users.service_admin_provider import ServiceAdminHealth
from media_stack.core.http import HttpClient

_log = logging.getLogger("media_stack")
_ERR_LEN = 99
# Lidarr + Readarr stayed on the v1 API; Sonarr/Radarr moved to v3.
_API_VERSION_OVERRIDES = {"lidarr": "v1", "readarr": "v1"}
_DEFAULT_API_VERSION = "v3"
_OK_STATUSES = (HTTPStatus.OK, HTTPStatus.NO_CONTENT, HTTPStatus.ACCEPTED)


class ArrServiceAdminProviderError(RuntimeError):
    pass


class ArrServiceAdminProvider:
    """Single-login password resetter for any *arr v3 service."""

    def __init__(
        self,
        *,
        service_id: str,
        base_url: str,
        api_key: str,
        admin_username: str = "admin",
        api_version: str = "",
        http_client: HttpClient | None = None,
    ) -> None:
        self.name = service_id
        self._base_url = str(base_url).rstrip("/")
        self._api_key = api_key
        self._admin_username = admin_username
        self._http = http_client or HttpClient()
        resolved = api_version or _API_VERSION_OVERRIDES.get(
            service_id, _DEFAULT_API_VERSION,
        )
        self._host_config_path = f"/api/{resolved}/config/host"
        self._status_path = f"/api/{resolved}/system/status"

    def health_check(self) -> ServiceAdminHealth:
        if not self._api_key:
            return ServiceAdminHealth(ok=False, detail="api key missing")
        try:
            status, _, _ = self._http.request(
                self._base_url, self._status_path, api_key=self._api_key,
            )
        except Exception as exc:  # noqa: BLE001
            return ServiceAdminHealth(ok=False, detail=str(exc)[:_ERR_LEN])
        return ServiceAdminHealth(
            ok=HTTPStatus.OK <= status < HTTPStatus.MULTIPLE_CHOICES,
            detail=f"status={status}",
        )

    def set_admin_password(self, password: str) -> None:
        if not self._api_key:
            raise ArrServiceAdminProviderError(f"{self.name}: api key missing")

        current = self._fetch_host_config()
        updated = self._apply_credentials(current, password)
        self._put_host_config(updated)

    def _fetch_host_config(self) -> dict[str, Any]:
        status, body, text = self._http.request(
            self._base_url, self._host_config_path, api_key=self._api_key,
        )
        if status != HTTPStatus.OK or not isinstance(body, dict):
            raise ArrServiceAdminProviderError(
                f"{self.name}: fetch host config failed "
                f"status={status} body={text[:_ERR_LEN]}"
            )
        return body

    def _apply_credentials(self, current: dict[str, Any],
                           password: str) -> dict[str, Any]:
        updated = dict(current)
        updated["username"] = self._admin_username
        updated["password"] = password
        updated["passwordConfirmation"] = password
        # An Arr with auth disabled ("none" / "") won't accept form-login
        # afterwards unless we also enable forms auth here.
        method = str(updated.get("authenticationMethod", "")).lower()
        if method in ("", "none"):
            updated["authenticationMethod"] = "forms"
        return updated

    def _put_host_config(self, payload: dict[str, Any]) -> None:
        status, _, text = self._http.request(
            self._base_url, self._host_config_path,
            api_key=self._api_key, method="PUT", payload=payload,
        )
        if status not in _OK_STATUSES:
            raise ArrServiceAdminProviderError(
                f"{self.name}: PUT host config failed "
                f"status={status} body={text[:_ERR_LEN]}"
            )
