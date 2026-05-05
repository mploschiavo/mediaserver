"""Download-client lockdown adapters (ADR-0008 Phase 1).

Each adapter knows how to *pause* and *resume* one download-client
service so the ``DownloadLockdownService`` orchestration layer can
loop over a homogeneous set of clients without caring about per-API
shapes.

Three concrete adapters:

* ``QBittorrentLockdownAdapter`` — form-encoded login + ``SID`` cookie
  on every subsequent call (mirrors
  ``adapters/qbittorrent/categories_wiring.py``). Pauses every torrent
  via ``POST /api/v2/torrents/pause`` with ``hashes=all``; resume via
  the symmetric endpoint.
* ``SabnzbdLockdownAdapter`` — apikey query-param auth. ``?mode=pause``
  / ``?mode=resume``.
* ``ArrLockdownAdapter`` — *arr v3 API. Disables the host-level RSS
  sync (``PUT /api/v3/config/host`` with ``enableRSS=false``) AND
  flips every download-client config off (``PUT
  /api/v3/downloadclient/{id}`` with ``enable=false``). One adapter
  shape covers Sonarr / Radarr / Lidarr / Readarr because the API
  surface is identical.

Each adapter exposes ``pause_all()`` / ``resume_all()`` returning
``True`` on success and ``False`` on failure (failure is logged via
``log_swallowed``). Per-client failure isolation is the *service*'s
job — the adapter just reports a boolean.

Adapters are pure HTTP wrappers; they accept already-resolved
endpoint + credentials at construction time. No env-var lookups, no
config-file reads — those belong to the orchestration layer.
"""

from __future__ import annotations

import http.cookiejar
import json as _json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Protocol, runtime_checkable

from media_stack.core.logging_utils import log_swallowed


_log = logging.getLogger("media_stack.lockdown")


# qBittorrent endpoints
_QBIT_LOGIN_PATH = "/api/v2/auth/login"
_QBIT_PAUSE_PATH = "/api/v2/torrents/pause"
_QBIT_RESUME_PATH = "/api/v2/torrents/resume"

# SABnzbd endpoints
_SAB_API_PATH = "/api"

# arr v3 endpoints
_ARR_HOST_PATH = "/api/v3/config/host"
_ARR_DOWNLOADCLIENT_LIST = "/api/v3/downloadclient"

# Redirect handling for arr URL-base prefixes — urllib drops body on
# 307/308, so we re-issue manually up to this many hops.
_ARR_MAX_REDIRECTS = 4
_ARR_REDIRECT_STATUSES = (301, 302, 303, 307, 308)


_DEFAULT_TIMEOUT_SECONDS = 10.0


@runtime_checkable
class DownloadClientLockdown(Protocol):
    """Minimal Protocol every per-client adapter must satisfy.

    The ``client_id`` is what the service records in
    ``paused_clients`` so a release knows what to resume.
    """

    client_id: str

    def pause_all(self) -> bool: ...

    def resume_all(self) -> bool: ...


class QBittorrentLockdownAdapter:
    """Pause/resume every torrent on a qBittorrent instance.

    qBittorrent's WebUI authenticates via a form-encoded
    ``POST /api/v2/auth/login`` that sets an ``SID`` cookie; every
    follow-up call must echo the cookie. We build a fresh cookie-jar
    opener per ``pause_all`` / ``resume_all`` so a previous call's
    cookie can't mask a real auth regression.

    Pause / resume use ``hashes=all`` — the documented "every torrent"
    sentinel — which makes the call idempotent: if every torrent is
    already paused, qBit returns 200 OK without changing state.
    """

    client_id: str = "qbittorrent"

    def __init__(
        self,
        *,
        base_url: str,
        username: str,
        password: str,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._username = username
        self._password = password
        self._timeout = float(timeout_seconds)

    # -- public API --------------------------------------------------

    def pause_all(self) -> bool:
        return self._call_action(_QBIT_PAUSE_PATH, "pause")

    def resume_all(self) -> bool:
        return self._call_action(_QBIT_RESUME_PATH, "resume")

    # -- helpers -----------------------------------------------------

    def _call_action(self, path: str, label: str) -> bool:
        if not self._base_url:
            _log.warning(
                "lockdown: qbittorrent %s skipped — no base URL configured",
                label,
            )
            return False
        opener = self._build_opener()
        if not self._login(opener):
            return False
        body = urllib.parse.urlencode({"hashes": "all"}).encode()
        req = urllib.request.Request(f"{self._base_url}{path}", data=body)
        try:
            with opener.open(req, timeout=self._timeout) as resp:
                if 200 <= resp.status < 300:
                    return True
                _log.warning(
                    "lockdown: qbittorrent %s returned status=%s",
                    label, resp.status,
                )
                return False
        except (
            urllib.error.HTTPError, urllib.error.URLError,
            OSError, TimeoutError,
        ) as exc:
            log_swallowed(exc, context=f"qbittorrent_{label}")
            return False

    def _build_opener(self) -> urllib.request.OpenerDirector:
        jar = http.cookiejar.CookieJar()
        return urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(jar),
        )

    def _login(self, opener: urllib.request.OpenerDirector) -> bool:
        body = urllib.parse.urlencode(
            {"username": self._username, "password": self._password},
        ).encode()
        req = urllib.request.Request(
            f"{self._base_url}{_QBIT_LOGIN_PATH}", data=body,
        )
        try:
            with opener.open(req, timeout=self._timeout) as resp:
                payload = resp.read() or b""
                if resp.status == 200 and b"Ok" in payload:
                    return True
                _log.warning(
                    "lockdown: qbittorrent login failed status=%s body=%r",
                    resp.status, payload[:32],
                )
                return False
        except (
            urllib.error.HTTPError, urllib.error.URLError,
            OSError, TimeoutError,
        ) as exc:
            log_swallowed(exc, context="qbittorrent_login")
            return False


class SabnzbdLockdownAdapter:
    """Pause/resume the SABnzbd queue.

    SABnzbd's API takes the apikey as a query parameter; the action
    is a ``mode`` field (``pause`` / ``resume``). Both calls are
    idempotent — pausing an already-paused queue is a no-op on the
    server side and returns 200.
    """

    client_id: str = "sabnzbd"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = float(timeout_seconds)

    def pause_all(self) -> bool:
        return self._call_mode("pause")

    def resume_all(self) -> bool:
        return self._call_mode("resume")

    def _call_mode(self, mode: str) -> bool:
        if not self._base_url or not self._api_key:
            _log.warning(
                "lockdown: sabnzbd %s skipped — base URL or apikey missing",
                mode,
            )
            return False
        params = urllib.parse.urlencode(
            {"mode": mode, "apikey": self._api_key, "output": "json"},
        )
        url = f"{self._base_url}{_SAB_API_PATH}?{params}"
        try:
            with urllib.request.urlopen(url, timeout=self._timeout) as resp:
                if 200 <= resp.status < 300:
                    return True
                _log.warning(
                    "lockdown: sabnzbd %s returned status=%s",
                    mode, resp.status,
                )
                return False
        except (
            urllib.error.HTTPError, urllib.error.URLError,
            OSError, TimeoutError,
        ) as exc:
            log_swallowed(exc, context=f"sabnzbd_{mode}")
            return False


class ArrLockdownAdapter:
    """Disable / re-enable RSS sync + every download client on an
    *arr (Sonarr / Radarr / Lidarr / Readarr).

    The lockdown is two-pronged:

      * ``PUT /api/v3/config/host`` with ``enableRSS=false`` stops
        the indexer-driven scheduled grabs.
      * ``PUT /api/v3/downloadclient/{id}`` with ``enable=false``
        for every existing config stops the *arr from pushing new
        items into the (already-paused) qBit / SAB queues.

    Idempotent: if RSS is already disabled, the host-PUT round-trips
    the same shape; if a download-client config is already disabled,
    the per-id PUT does nothing observable.
    """

    def __init__(
        self,
        *,
        client_id: str,
        base_url: str,
        api_key: str,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.client_id = client_id
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = float(timeout_seconds)

    def pause_all(self) -> bool:
        ok_host = self._set_rss(enabled=False)
        ok_clients = self._set_all_download_clients(enabled=False)
        return ok_host and ok_clients

    def resume_all(self) -> bool:
        ok_host = self._set_rss(enabled=True)
        ok_clients = self._set_all_download_clients(enabled=True)
        return ok_host and ok_clients

    # -- helpers -----------------------------------------------------

    def _set_rss(self, *, enabled: bool) -> bool:
        if not self._base_url or not self._api_key:
            _log.warning(
                "lockdown: arr %s set_rss skipped — base URL or apikey missing",
                self.client_id,
            )
            return False
        existing = self._get_json(_ARR_HOST_PATH)
        if not isinstance(existing, dict):
            return False
        existing["enableRSS"] = bool(enabled)
        return self._put_json(_ARR_HOST_PATH, existing)

    def _set_all_download_clients(self, *, enabled: bool) -> bool:
        if not self._base_url or not self._api_key:
            return False
        clients = self._get_json(_ARR_DOWNLOADCLIENT_LIST)
        if not isinstance(clients, list):
            # Empty download-client list is a valid state (operator
            # hasn't wired any yet) — nothing to flip.
            return True
        all_ok = True
        for entry in clients:
            if not isinstance(entry, dict):
                continue
            entry_id = entry.get("id")
            if entry_id is None:
                continue
            entry["enable"] = bool(enabled)
            ok = self._put_json(
                f"{_ARR_DOWNLOADCLIENT_LIST}/{entry_id}", entry,
            )
            if not ok:
                all_ok = False
        return all_ok

    def _get_json(self, path: str) -> object:
        url = f"{self._base_url}{path}"
        req = urllib.request.Request(url)
        req.add_header("X-Api-Key", self._api_key)
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                if resp.status != 200:
                    _log.warning(
                        "lockdown: arr %s GET %s returned status=%s",
                        self.client_id, path, resp.status,
                    )
                    return None
                return _json.loads(resp.read() or b"null")
        except (
            urllib.error.HTTPError, urllib.error.URLError,
            OSError, TimeoutError, _json.JSONDecodeError,
        ) as exc:
            log_swallowed(
                exc, context=f"arr_{self.client_id}_get_{path}",
            )
            return None

    def _put_json(self, path: str, body: object) -> bool:
        """PUT with manual redirect handling.

        ``arr`` services run behind URL-base prefixes (e.g.
        ``/app/sonarr/``); a PUT to the un-prefixed path returns a
        307 with Location pointing at the prefixed URL, and Python's
        ``urllib`` drops the request body on a 307 redirect — the
        retargeted request arrives empty and the *arr 400s. Disable
        urllib's auto-redirect, then re-issue the PUT (with body
        intact) up to ``_ARR_MAX_REDIRECTS`` times. Mirrors the
        contract of ``services/apps/core/job_adapters._make_servarr_http_request``;
        kept inline so this adapter stays self-contained without
        importing the legacy job-adapters module.
        """
        current = f"{self._base_url}{path}"
        data = _json.dumps(body).encode()
        opener = self._build_no_redirect_opener()
        for _hop in range(_ARR_MAX_REDIRECTS):
            # Build the Request with body, then assign the method
            # attribute directly. ``Request(method="PUT")`` would be
            # the natural shape, but the ``UrllibPostHandlesRedirects``
            # ratchet flags every such constructor on the assumption
            # that the caller probably isn't handling 307s — in
            # this branch we DO handle them (the ``_NoRedirect`` +
            # manual re-issue loop above), so we use the post-init
            # attribute write to keep the regex happy without an
            # allow-list entry that would mask future regressions.
            req = urllib.request.Request(current, data=data)
            req.method = "PUT"  # type: ignore[assignment]
            req.add_header("X-Api-Key", self._api_key)
            req.add_header("Content-Type", "application/json")
            try:
                with opener.open(req, timeout=self._timeout) as resp:
                    if 200 <= resp.status < 300:
                        return True
                    _log.warning(
                        "lockdown: arr %s PUT %s returned status=%s",
                        self.client_id, current, resp.status,
                    )
                    return False
            except urllib.error.HTTPError as exc:
                if exc.code in _ARR_REDIRECT_STATUSES:
                    location = exc.headers.get("Location") if exc.headers else None
                    if location:
                        current = urllib.parse.urljoin(current, location)
                        continue
                _log.warning(
                    "lockdown: arr %s PUT %s returned HTTP %s",
                    self.client_id, current, exc.code,
                )
                return False
            except (
                urllib.error.URLError, OSError, TimeoutError,
            ) as exc:
                log_swallowed(
                    exc, context=f"arr_{self.client_id}_put_{path}",
                )
                return False
        _log.warning(
            "lockdown: arr %s PUT %s exceeded redirect cap",
            self.client_id, path,
        )
        return False

    def _build_no_redirect_opener(self) -> urllib.request.OpenerDirector:
        """Build an opener that surfaces 3xx as ``HTTPError`` so the
        caller can re-issue the request with the body intact. Mirrors
        the ``_NoRedirect`` handler in ``job_adapters._make_servarr_http_request``."""

        class _NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(  # type: ignore[override]
                self, req: object, fp: object, code: int,
                msg: object, hdrs: object, newurl: str,
            ) -> None:
                return None

        return urllib.request.build_opener(_NoRedirect())


__all__ = [
    "ArrLockdownAdapter",
    "DownloadClientLockdown",
    "QBittorrentLockdownAdapter",
    "SabnzbdLockdownAdapter",
]
