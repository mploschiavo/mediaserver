"""Jellyfin ``SessionAdminProvider`` — HTTP-backed live-session view.

Jellyfin's ``GET /Sessions`` is the canonical live-clients endpoint.
Each row carries the user's ``UserName`` natively, so no
``UserId`` -> username translation is required at this layer (the
older ``JellyfinApiProvider.list_sessions`` filters on
``UserId``, which is why the SSO path silently dropped rows — the
controller doesn't store the per-user Jellyfin UserId for federated
accounts created on first OIDC login).

This provider keys on ``UserName`` directly, so it works for both
the locally-provisioned and the OIDC-auto-provisioned cases.

Authentication
--------------
``X-Emby-Token`` / ``X-MediaBrowser-Token`` / ``api_key=`` query —
all three are accepted by Jellyfin 10.8+. The shared ``HttpClient``
sends ``X-Api-Key`` which Jellyfin treats as an alias. We match the
existing ``JellyfinApiProvider`` pattern and use the ``api_key=``
query parameter for revoke (where Jellyfin 10.11+ rejects header
auth on DELETE endpoints).
"""

from __future__ import annotations

import logging
import os
from http import HTTPStatus
from typing import Any

from media_stack.core.auth.users.provider import ExternalSession
from media_stack.core.http import HttpClient

_log = logging.getLogger("media_stack.security.jellyfin_session_provider")

_SESSIONS_PATH = "/Sessions"
_PROBE_TIMEOUT = 5
_OK_DELETE_STATUSES = (
    HTTPStatus.OK, HTTPStatus.NO_CONTENT, HTTPStatus.NOT_FOUND,
)


class JellyfinSessionProvider:
    """SessionAdminProvider backed by Jellyfin's ``/Sessions`` API.

    Construction probes the endpoint once. ``available`` flips to
    True iff the probe returns a JSON list. The provider is still
    safe to call when unavailable (it returns []) — the singleton
    layer can choose to skip the registration entirely if it wants
    a tighter footprint.
    """

    name = "jellyfin"

    def __init__(
        self,
        base_url: str,
        api_key: str,
        http_client: HttpClient | None = None,
        *,
        probe_on_init: bool = True,
    ) -> None:
        self._base_url = str(base_url or "").rstrip("/")
        self._api_key = api_key or ""
        self._http = http_client or HttpClient()
        self.available = False
        if probe_on_init and self._base_url and self._api_key:
            self.available = self._probe()

    # ---- probe ---------------------------------------------------------

    def _probe(self) -> bool:
        try:
            status, body, _ = self._http.request(
                self._base_url, _SESSIONS_PATH,
                api_key=self._api_key, timeout=_PROBE_TIMEOUT,
            )
        except Exception as exc:  # noqa: BLE001
            _log.info(
                "jellyfin session probe failed (%s): %s",
                self._base_url, exc,
            )
            return False
        if status != HTTPStatus.OK or not isinstance(body, list):
            _log.info(
                "jellyfin session probe %s -> status=%s body=%s",
                _SESSIONS_PATH, status, type(body).__name__,
            )
            return False
        return True

    # ---- SessionAdminProvider -----------------------------------------

    def list_sessions(self, external_id: str) -> list[ExternalSession]:
        """Live Jellyfin sessions for ``external_id`` (username).

        Filters by ``UserName`` (case-sensitive — Jellyfin
        normalizes display name to its stored login name). Returns
        ``[]`` for unknown users or when the backend is unreachable.
        """
        if not external_id or not self.available or not self._api_key:
            return []
        rows = self._fetch_all()
        out: list[ExternalSession] = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            if str(r.get("UserName", "")) != external_id:
                continue
            out.append(_row_to_external(r))
        return out

    def revoke_sessions(self, external_id: str) -> int:
        """Revoke every Jellyfin session for the user. Returns count.

        Iterates the live session list, deletes each by id. The
        upstream protocol declares ``-> None`` so any caller binding
        the count is implementation-detail; tests rely on it.
        """
        if not external_id or not self.available or not self._api_key:
            return 0
        rows = self._fetch_all()
        n = 0
        for r in rows:
            if not isinstance(r, dict):
                continue
            if str(r.get("UserName", "")) != external_id:
                continue
            sid = str(r.get("Id", ""))
            if not sid:
                continue
            if self._delete_one(sid):
                n += 1
        return n

    def revoke_session(self, external_id: str, session_id: str) -> bool:
        """Revoke one Jellyfin session by id. Returns True on success.

        Idempotent: a 404 on an unknown id counts as success because
        the contract is "treat unknown id as already-gone".
        """
        del external_id
        if not session_id or not self.available or not self._api_key:
            return False
        return self._delete_one(session_id)

    # ---- internals -----------------------------------------------------

    def _fetch_all(self) -> list[Any]:
        try:
            status, body, _ = self._http.request(
                self._base_url, _SESSIONS_PATH,
                api_key=self._api_key, timeout=_PROBE_TIMEOUT,
            )
        except Exception as exc:  # noqa: BLE001
            _log.debug("jellyfin list_sessions HTTP failed: %s", exc)
            return []
        if status != HTTPStatus.OK or not isinstance(body, list):
            return []
        return body

    def _delete_one(self, session_id: str) -> bool:
        try:
            status, _, _ = self._http.request(
                self._base_url,
                f"{_SESSIONS_PATH}/{session_id}",
                method="DELETE", api_key=self._api_key,
                timeout=_PROBE_TIMEOUT,
            )
        except Exception as exc:  # noqa: BLE001
            _log.debug("jellyfin revoke %s failed: %s", session_id, exc)
            return False
        return status in _OK_DELETE_STATUSES


def _row_to_external(r: dict[str, Any]) -> ExternalSession:
    """Map a Jellyfin /Sessions row to an ExternalSession."""
    client_name = str(r.get("Client", "") or "")
    client_ver = str(r.get("ApplicationVersion", "") or "")
    client = client_name + (f" {client_ver}" if client_ver else "")
    device = str(r.get("DeviceName", "") or r.get("Device", "") or "")
    return ExternalSession(
        session_id=str(r.get("Id", "")),
        device=device,
        client=client.strip(),
        last_activity=str(r.get("LastActivityDate", "") or ""),
        ip=str(r.get("RemoteEndPoint", "") or ""),
    )


def from_env(
    env: dict[str, str] | None = None,
    http_client: HttpClient | None = None,
) -> JellyfinSessionProvider | None:
    """Build the Jellyfin session provider from controller env.

    ``JELLYFIN_URL`` (default ``http://jellyfin:8096``) +
    ``JELLYFIN_API_KEY`` (required — without it Jellyfin returns
    401 and we'd register a permanently-unavailable provider).
    """
    e = env if env is not None else os.environ
    url = (e.get("JELLYFIN_URL") or "http://jellyfin:8096").strip()
    api_key = (e.get("JELLYFIN_API_KEY") or "").strip()
    if not url or not api_key:
        _log.info(
            "jellyfin session provider not configured "
            "(url=%r, api_key=%s)",
            url, "set" if api_key else "missing",
        )
        return None
    return JellyfinSessionProvider(
        base_url=url, api_key=api_key, http_client=http_client,
    )


__all__ = ["JellyfinSessionProvider", "from_env"]
