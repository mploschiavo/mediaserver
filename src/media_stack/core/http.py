"""Shared HTTP adapter with retry/timing behavior."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Callable
from urllib import error, request
from urllib.parse import urlparse

from .decorators import retry, timed

HTTP_RETRY_ATTEMPTS = max(1, int(os.environ.get("MEDIA_STACK_HTTP_RETRY_ATTEMPTS", "3")))
HTTP_RETRY_DELAY_SECONDS = float(os.environ.get("MEDIA_STACK_HTTP_RETRY_DELAY_SECONDS", "0.5"))
HTTP_RETRY_MAX_DELAY_SECONDS = float(
    os.environ.get("MEDIA_STACK_HTTP_RETRY_MAX_DELAY_SECONDS", "3")
)
HTTP_RETRY_BACKOFF = float(os.environ.get("MEDIA_STACK_HTTP_RETRY_BACKOFF", "2"))
RETRYABLE_HTTP_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}

# Named HTTP status codes used widely across arr/service adapters. Kept in
# one place so service code reads as ``status != HTTP_OK`` rather than
# ``status != 200`` (the magic-number ratchet flags the literal form).
HTTP_OK = 200                       # 200 OK — successful request
HTTP_BAD_REQUEST = 400              # 400 Bad Request — usually a validation reject
HTTP_FORBIDDEN = 403                # 403 Forbidden — qBittorrent anti-brute-force
HTTP_CONFLICT = 409                 # 409 Conflict — arr "already exists" on create
HTTP_CLIENT_CLOSED_REQUEST = 499    # 499 ~= client gave up (used for local decorators)
HTTP_INTERNAL_ERROR = 500           # 500 Internal Server Error
HTTP_SYNTHETIC_CLIENT_ERROR = 599   # 599 — synthetic status we emit on urllib failures

# Tuple of 2xx codes that arr/Jellyseerr adapters treat as "request accepted".
# 200 OK, 201 Created, 202 Accepted — the arr APIs vary between them.
HTTP_2XX_ACCEPTED_STATUSES: tuple[int, int, int] = (HTTP_OK, 201, 202)

# Jellyfin also uses 204 No Content for successful POSTs that have no body.
HTTP_2XX_JELLYFIN_STATUSES: tuple[int, int, int, int] = (HTTP_OK, 201, 202, 204)


class RetryableHttpStatusError(RuntimeError):
    def __init__(self, status_code: int, body: str) -> None:
        super().__init__(f"Retryable HTTP status: {status_code}")
        self.status_code = status_code
        self.body = body


def _is_retryable_http_error(exc: Exception) -> bool:
    return isinstance(exc, (error.URLError, RetryableHttpStatusError, ConnectionError, OSError))


def _default_normalize_url(url: str) -> str:
    return str(url or "").rstrip("/")


@dataclass
class HttpClient:
    normalize_url: Callable[[str], str] = _default_normalize_url

    def _execute_request_with_retry(
        self,
        req: request.Request,
        timeout: int,
        *,
        attempts_override: int | None = None,
    ) -> tuple[int, Any, str]:
        """Inline retry loop so the WARN line names the URL + method
        of the request being retried — the previous decorator-based
        version logged ``retry operation=http.request attempt=N/3
        ...`` with no context, which made indexer-discovery storms
        unreadable (~70 indexers all timing out, no way to tell
        which). Also reads ``MEDIA_STACK_HTTP_RETRY_ATTEMPTS`` at
        call time so the discover-indexers job can drop attempts to
        1 for its short-lived scope."""
        log = logging.getLogger("media_stack")
        attempts = (
            max(1, int(attempts_override))
            if attempts_override is not None
            else max(1, int(os.environ.get(
                "MEDIA_STACK_HTTP_RETRY_ATTEMPTS", "3"
            )))
        )
        delay = float(os.environ.get(
            "MEDIA_STACK_HTTP_RETRY_DELAY_SECONDS", "0.5"
        ))
        max_delay = float(os.environ.get(
            "MEDIA_STACK_HTTP_RETRY_MAX_DELAY_SECONDS", "3"
        ))
        backoff = float(os.environ.get(
            "MEDIA_STACK_HTTP_RETRY_BACKOFF", "2"
        ))
        attempt = 1
        sleep_seconds = delay
        while True:
            try:
                return self._execute_request(req, timeout)
            except Exception as exc:
                if not _is_retryable_http_error(exc):
                    raise
                if attempt >= attempts:
                    raise
                log.warning(
                    "retry %s %s attempt=%s/%s delay_seconds=%.2f error=%s",
                    req.get_method(), req.full_url,
                    attempt, attempts, sleep_seconds, exc,
                )
                import time as _t
                _t.sleep(sleep_seconds)
                attempt += 1
                sleep_seconds = min(max_delay, sleep_seconds * backoff)

    def _execute_request(self, req: request.Request, timeout: int) -> tuple[int, Any, str]:
        try:
            with request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                if body:
                    try:
                        parsed = json.loads(body)
                    except json.JSONDecodeError:
                        parsed = body
                else:
                    parsed = None
                return resp.status, parsed, body
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            # Follow 307/308 redirects that urllib didn't handle automatically.
            if exc.code in (307, 308):
                location = exc.headers.get("Location", "")
                if location:
                    # Resolve relative Location against the original URL.
                    if location.startswith("/"):
                        parsed = urlparse(req.full_url)
                        location = f"{parsed.scheme}://{parsed.netloc}{location}"
                    redirect_req = request.Request(
                        url=location,
                        data=req.data,
                        method=req.get_method(),
                        headers=dict(req.headers),
                    )
                    return self._execute_request(redirect_req, timeout)
            if exc.code in RETRYABLE_HTTP_STATUS_CODES:
                raise RetryableHttpStatusError(exc.code, body) from exc
            return exc.code, None, body

    @timed("http.request")
    def request(
        self,
        base_url: str,
        path: str,
        *,
        api_key: str | None = None,
        method: str = "GET",
        payload: Any = None,
        timeout: int = 20,
    ) -> tuple[int, Any, str]:
        url = f"{self.normalize_url(base_url)}{path}"
        data = None
        headers = {"Accept": "application/json"}

        if api_key:
            # ``X-Api-Key`` is what Sonarr/Radarr/Prowlarr/Bazarr/SAB
            # all accept, so it's the default. Jellyfin / Emby don't
            # accept it — they need ``X-Emby-Token`` (or a
            # ``MediaBrowser`` Authorization header). Send BOTH so
            # one client can probe any backend without callers having
            # to know which header style applies. The two headers are
            # mutually exclusive across vendors (no service rejects
            # the OTHER header — they just ignore it), so this is
            # safe. Without this fix, the jellyfin session-admin
            # provider gets 401 on /Sessions and the operator's
            # "active sessions" view lists only the controller's
            # synth caller-row, never the real Jellyfin clients.
            headers["X-Api-Key"] = api_key
            headers["X-Emby-Token"] = api_key

        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        _logger = logging.getLogger("media_stack")
        _logger.debug("[DEBUG] HTTP %s %s (timeout=%ds)", method, url, timeout)

        req = request.Request(url=url, data=data, method=method, headers=headers)
        try:
            status, parsed, body = self._execute_request_with_retry(req, timeout)
            _logger.debug("[DEBUG] HTTP %s %s → %d (%d bytes)", method, url, status, len(body or ""))
            return status, parsed, body
        except RetryableHttpStatusError as exc:
            _logger.debug("[DEBUG] HTTP %s %s → retryable %d", method, url, exc.status_code)
            return exc.status_code, None, exc.body
        except error.URLError as exc:
            _logger.debug("[DEBUG] HTTP %s %s → error: %s", method, url, exc)
            raise RuntimeError(f"Request failed for {url}: {exc}") from exc
