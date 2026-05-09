"""Authelia ``SessionAdminProvider`` — HTTP probe with graceful fallback.

Authelia 4.38 does not ship a documented public ``/api/sessions``
admin endpoint; the canonical session store is either the embedded
session cookie (file backend, no enumeration) or Redis (only the
Authelia process can read it). The closest surface that's always
present is ``/api/state`` which returns *the caller's own* session
state — useless for an admin-wide list.

This provider:

a) Probes ``GET /api/sessions`` (the experimental admin route some
   Authelia forks expose) on construction. If it returns 200 with a
   JSON list, that endpoint is used for ``list_sessions`` and
   ``revoke_session`` falls through to ``POST /api/sessions/{id}/revoke``.
b) If the probe 404s / 401s / refuses, the provider degrades to the
   safe-default behaviour of returning ``[]`` for ``list_sessions``
   and ``0`` for the revoke methods. The aggregator still gets a
   live answer for the other providers in the same fan-out.

This is the right shape because:
- Most homelabs run Authelia 4.38 file-backend, which simply has no
  enumerable session list at all (the cookie IS the session).
- A loud failure here would mask Jellyfin / controller sessions in
  the same response — the contract is *best-effort, never raise*.
- When deployments DO move to a session-list backend, swapping in
  the live behaviour is a one-flag change and existing tests stay
  pinned to the fallback path.

Username mapping
----------------
``list_sessions(username)`` filters Authelia's response on the
``username`` field directly — Authelia stores the controller's
canonical username natively (it IS the IdP), so no external_id
translation is needed.
"""

from __future__ import annotations

import logging
import os
from http import HTTPStatus
from typing import Any

from media_stack.core.auth.users.provider import ExternalSession
from media_stack.core.http import HttpClient

_log = logging.getLogger("media_stack.security.authelia_session_provider")

_PROBE_PATH = "/api/sessions"
_PROBE_TIMEOUT = 5
_REVOKE_PATH_TMPL = "/api/sessions/{session_id}/revoke"


class AutheliaSessionProvider:
    """SessionAdminProvider backed by Authelia's HTTP admin surface.

    Construction performs a one-shot probe of ``/api/sessions``. If
    the probe succeeds, ``available`` is ``True`` and live calls hit
    the API. If it fails, the provider reports ``available=False``
    and silently returns empty results — the aggregator still
    treats it as a registered provider but gets nothing from it.

    This means the same code works in three deployment modes:
    - Authelia file backend (no session list): degrades to []
    - Authelia + Redis (no public list endpoint): degrades to []
    - Authelia fork with admin API: full enumeration
    """

    name = "authelia"

    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        http_client: HttpClient | None = None,
        *,
        probe_on_init: bool = True,
    ) -> None:
        self._base_url = str(base_url or "").rstrip("/")
        self._api_key = api_key or ""
        self._http = http_client or HttpClient()
        self.available = False
        if probe_on_init and self._base_url:
            self.available = self._probe()

    # ---- probe ---------------------------------------------------------

    def _probe(self) -> bool:
        """One-shot probe of the admin endpoint. Returns True iff the
        endpoint responded 200/204 with a JSON-shaped body. Anything
        else (404 missing route, 401 unauthorized, connection refused
        on a not-yet-up cluster) reports unavailable.
        """
        try:
            status, body, _ = self._http.request(
                self._base_url, _PROBE_PATH,
                api_key=self._api_key, timeout=_PROBE_TIMEOUT,
            )
        except Exception as exc:  # noqa: BLE001
            _log.info(
                "authelia session-admin probe failed (%s): %s — "
                "session enumeration disabled for this provider",
                self._base_url, exc,
            )
            return False
        if status != HTTPStatus.OK:
            _log.info(
                "authelia session-admin probe %s -> status=%s; "
                "session enumeration disabled",
                _PROBE_PATH, status,
            )
            return False
        if not isinstance(body, list):
            _log.info(
                "authelia session-admin probe returned non-list body; "
                "session enumeration disabled",
            )
            return False
        return True

    # ---- SessionAdminProvider -----------------------------------------

    def list_sessions(self, external_id: str) -> list[ExternalSession]:
        """Return live Authelia sessions for ``external_id`` (username).

        ``external_id`` is the controller-side username — matches
        Authelia's native username field directly.
        """
        if not external_id or not self.available:
            return []
        rows = self._fetch_all()
        out: list[ExternalSession] = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            if str(r.get("username", "")) != external_id:
                continue
            out.append(_row_to_external(r))
        return out

    def revoke_sessions(self, external_id: str) -> None:
        """Revoke every Authelia session for ``external_id``.

        Iterates the list (already filtered by username) and POSTs
        the per-session revoke endpoint for each. Silently no-ops if
        the admin API isn't available.
        """
        if not external_id or not self.available:
            return
        for sess in self.list_sessions(external_id):
            self._revoke_one(sess.session_id)

    def revoke_session(self, external_id: str, session_id: str) -> None:
        """Revoke a single Authelia session by id. Idempotent."""
        del external_id  # session_id is globally unique on Authelia
        if not session_id or not self.available:
            return
        self._revoke_one(session_id)

    # ---- internals -----------------------------------------------------

    def _fetch_all(self) -> list[Any]:
        try:
            status, body, _ = self._http.request(
                self._base_url, _PROBE_PATH,
                api_key=self._api_key, timeout=_PROBE_TIMEOUT,
            )
        except Exception as exc:  # noqa: BLE001
            _log.debug("authelia list_sessions HTTP failed: %s", exc)
            return []
        if status != HTTPStatus.OK or not isinstance(body, list):
            return []
        return body

    def _revoke_one(self, session_id: str) -> None:
        try:
            self._http.request(
                self._base_url,
                _REVOKE_PATH_TMPL.format(session_id=session_id),
                method="POST", api_key=self._api_key,
                timeout=_PROBE_TIMEOUT,
            )
        except Exception as exc:  # noqa: BLE001
            _log.debug("authelia revoke %s failed: %s", session_id, exc)


def _row_to_external(r: dict[str, Any]) -> ExternalSession:
    """Map an Authelia admin-API row to an ExternalSession.

    Falls back to empty strings for any missing field so the
    aggregator's renderer never trips on None.
    """
    sid = str(r.get("id") or r.get("session_id") or "")
    ip = str(r.get("ip") or r.get("remote_ip") or "")
    ua = str(r.get("user_agent") or r.get("client") or "")
    last = str(r.get("last_activity") or r.get("last_seen_at") or "")
    return ExternalSession(
        session_id=sid,
        device=ua,
        client=ua,
        last_activity=last,
        ip=ip,
    )


def from_env(
    env: dict[str, str] | None = None,
    http_client: HttpClient | None = None,
) -> AutheliaSessionProvider | None:
    """Build the Authelia session provider from the controller env.

    Reads ``AUTHELIA_URL`` (default ``http://authelia:9091``) and
    ``AUTHELIA_API_KEY`` (optional). Returns ``None`` only when no
    URL is configured — construction otherwise always returns an
    instance, with ``available`` flipped by the live probe.
    """
    from media_stack.core.service_registry.registry import service_internal_url
    e = env if env is not None else os.environ
    url = (e.get("AUTHELIA_URL") or service_internal_url("authelia")).strip()
    if not url:
        return None
    return AutheliaSessionProvider(
        base_url=url,
        api_key=e.get("AUTHELIA_API_KEY", "").strip(),
        http_client=http_client,
    )


__all__ = ["AutheliaSessionProvider", "from_env"]
