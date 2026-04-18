"""ServiceAdminProvider for Bazarr.

Bazarr's password reset goes through PATCH /api/system/settings with the
body ``{"auth": {"username": ..., "password": ...}}``. The endpoint
requires the legacy API key (header ``X-API-KEY``) and returns ``302
Found`` on success — which ``urllib`` converts POST→GET by default,
making the reset appear to fail. We use HttpClient and treat a 302 with
a Location header as a successful no-op.
"""

from __future__ import annotations

import logging
from http import HTTPStatus
from typing import Any

from media_stack.core.auth.users.service_admin_provider import ServiceAdminHealth
from media_stack.core.http import HttpClient

_log = logging.getLogger("media_stack")
_ERR_LEN = 99
_SETTINGS_PATH = "/api/system/settings"
_STATUS_PATH = "/api/system/status"
_SUCCESS_STATUSES = (HTTPStatus.OK, HTTPStatus.NO_CONTENT, HTTPStatus.FOUND)


class BazarrServiceAdminProviderError(RuntimeError):
    pass


class BazarrServiceAdminProvider:

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        admin_username: str = "admin",
        http_client: HttpClient | None = None,
    ) -> None:
        self.name = "bazarr"
        self._base_url = str(base_url).rstrip("/")
        self._api_key = api_key
        self._admin_username = admin_username
        self._http = http_client or HttpClient()

    def health_check(self) -> ServiceAdminHealth:
        if not self._api_key:
            return ServiceAdminHealth(ok=False, detail="api key missing")
        try:
            status, _, _ = self._http.request(
                self._base_url, _STATUS_PATH, api_key=self._api_key,
            )
        except Exception as exc:  # noqa: BLE001
            return ServiceAdminHealth(ok=False, detail=str(exc)[:_ERR_LEN])
        return ServiceAdminHealth(
            ok=HTTPStatus.OK <= status < HTTPStatus.MULTIPLE_CHOICES,
            detail=f"status={status}",
        )

    def set_admin_password(self, password: str) -> None:
        if not self._api_key:
            raise BazarrServiceAdminProviderError("bazarr: api key missing")
        payload: dict[str, Any] = {
            "auth": {
                "type": "form",
                "username": self._admin_username,
                "password": password,
            },
        }
        try:
            status, _, text = self._http.request(
                self._base_url, _SETTINGS_PATH,
                api_key=self._api_key, method="POST", payload=payload,
            )
        except Exception as exc:  # noqa: BLE001
            raise BazarrServiceAdminProviderError(
                f"bazarr: request failed: {str(exc)[:_ERR_LEN]}",
            ) from exc
        if status not in _SUCCESS_STATUSES:
            raise BazarrServiceAdminProviderError(
                f"bazarr: settings PATCH failed "
                f"status={status} body={text[:_ERR_LEN]}"
            )
