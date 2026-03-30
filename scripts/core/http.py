"""Shared HTTP adapter with retry/timing behavior."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Callable
from urllib import error, request

from .decorators import retry, timed

HTTP_RETRY_ATTEMPTS = max(1, int(os.environ.get("MEDIA_STACK_HTTP_RETRY_ATTEMPTS", "3")))
HTTP_RETRY_DELAY_SECONDS = float(os.environ.get("MEDIA_STACK_HTTP_RETRY_DELAY_SECONDS", "0.5"))
HTTP_RETRY_MAX_DELAY_SECONDS = float(
    os.environ.get("MEDIA_STACK_HTTP_RETRY_MAX_DELAY_SECONDS", "3")
)
HTTP_RETRY_BACKOFF = float(os.environ.get("MEDIA_STACK_HTTP_RETRY_BACKOFF", "2"))
RETRYABLE_HTTP_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}


class RetryableHttpStatusError(RuntimeError):
    def __init__(self, status_code: int, body: str) -> None:
        super().__init__(f"Retryable HTTP status: {status_code}")
        self.status_code = status_code
        self.body = body


def _is_retryable_http_error(exc: Exception) -> bool:
    return isinstance(exc, (error.URLError, RetryableHttpStatusError))


def _default_normalize_url(url: str) -> str:
    return str(url or "").rstrip("/")


@dataclass
class HttpClient:
    normalize_url: Callable[[str], str] = _default_normalize_url

    @retry(
        attempts=HTTP_RETRY_ATTEMPTS,
        delay_seconds=HTTP_RETRY_DELAY_SECONDS,
        max_delay_seconds=HTTP_RETRY_MAX_DELAY_SECONDS,
        backoff_multiplier=HTTP_RETRY_BACKOFF,
        retry_if=_is_retryable_http_error,
        logger=logging.getLogger("media_stack"),
        operation="http.request",
    )
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
            headers["X-Api-Key"] = api_key

        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = request.Request(url=url, data=data, method=method, headers=headers)
        try:
            return self._execute_request(req, timeout)
        except RetryableHttpStatusError as exc:
            return exc.status_code, None, exc.body
        except error.URLError as exc:
            raise RuntimeError(f"Request failed for {url}: {exc}") from exc
