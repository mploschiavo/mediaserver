"""Jellyseerr ``SessionAdminProvider`` — placeholder.

Jellyseerr (and its Overseerr ancestor) does not expose a session
listing or session revocation API as of v2.0. Sessions live in the
``Session`` table of its sqlite/postgres database, keyed by an
opaque cookie token. There is no documented HTTP route under
``/api/v1`` that admins can call to enumerate or revoke them.

Upstream tracking:
- https://github.com/Fallenbagel/jellyseerr/issues/843 (request)
- https://github.com/sct/overseerr/issues/2700 (Overseerr parent)

Until upstream lands a route, this provider:

- Returns ``[]`` from ``list_sessions``.
- Returns ``0`` from ``revoke_sessions`` and ``False`` from
  ``revoke_session`` (idempotent no-ops).

It is still registered with the aggregator so that the controller
surfaces a consistent provider list to the UI (the "providers
queried" line in /api/sessions/active enumerates the registered
names — a missing Jellyseerr would show as "not queried" rather
than "0 sessions").

Construction probes ``GET /api/v1/status`` to confirm Jellyseerr is
reachable; if not, ``available`` flips False and the no-op
behaviour is identical (the provider stays cheap to call).
"""

from __future__ import annotations

import logging
import os
from http import HTTPStatus

from media_stack.core.auth.users.provider import ExternalSession
from media_stack.core.http import HttpClient

_log = logging.getLogger("media_stack.security.jellyseerr_session_provider")

_STATUS_PATH = "/api/v1/status"
_PROBE_TIMEOUT = 5


class JellyseerrSessionProvider:
    """SessionAdminProvider for Jellyseerr — currently no-op.

    Once upstream Jellyseerr ships a session-list endpoint, fill in
    ``list_sessions``: GET that endpoint with ``X-Api-Key``, filter
    on ``user.jellyfinUsername == external_id`` (Jellyseerr stores
    federated usernames under that field), map to ExternalSession.
    """

    name = "jellyseerr"

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
            status, _, _ = self._http.request(
                self._base_url, _STATUS_PATH,
                api_key=self._api_key, timeout=_PROBE_TIMEOUT,
            )
        except Exception as exc:  # noqa: BLE001
            _log.info(
                "jellyseerr probe failed (%s): %s",
                self._base_url, exc,
            )
            return False
        if status != HTTPStatus.OK:
            _log.info(
                "jellyseerr probe %s -> status=%s",
                _STATUS_PATH, status,
            )
            return False
        return True

    # ---- SessionAdminProvider -----------------------------------------

    def list_sessions(self, external_id: str) -> list[ExternalSession]:
        """No upstream API — always returns ``[]``.

        TODO: implement once upstream Jellyseerr exposes a session
        list endpoint. See module docstring.
        """
        del external_id
        return []

    def revoke_sessions(self, external_id: str) -> int:
        """No-op — Jellyseerr session revocation is unimplemented.

        Deleting the user record (via ``JellyseerrApiProvider.delete_user``)
        is the closest thing — the cookie becomes invalid on the next
        request. That path runs from the user-management surface, not
        here.
        """
        del external_id
        return 0

    def revoke_session(self, external_id: str, session_id: str) -> bool:
        """No-op — Jellyseerr does not expose per-session revoke."""
        del external_id, session_id
        return False


def from_env(
    env: dict[str, str] | None = None,
    http_client: HttpClient | None = None,
) -> JellyseerrSessionProvider | None:
    """Build the Jellyseerr session provider from controller env.

    Returns ``None`` when the API key is unset — there's nothing to
    probe and the provider would just register a permanent no-op.
    """
    from media_stack.core.service_registry.registry import service_internal_url
    e = env if env is not None else os.environ
    url = (e.get("JELLYSEERR_URL") or service_internal_url("jellyseerr")).strip()
    api_key = (e.get("JELLYSEERR_API_KEY") or "").strip()
    if not url or not api_key:
        _log.info(
            "jellyseerr session provider not configured "
            "(url=%r, api_key=%s)",
            url, "set" if api_key else "missing",
        )
        return None
    return JellyseerrSessionProvider(
        base_url=url, api_key=api_key, http_client=http_client,
    )


__all__ = ["JellyseerrSessionProvider", "from_env"]
