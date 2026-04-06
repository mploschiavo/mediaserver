from __future__ import annotations

from typing import Any

from media_stack.core.http import HttpClient

from .common import normalize_url

_HTTP_CLIENT = HttpClient(normalize_url=normalize_url)


def http_request(
    base_url: str,
    path: str,
    api_key: str | None = None,
    method: str = "GET",
    payload: Any = None,
    timeout: int = 20,
) -> tuple[int, Any, str]:
    return _HTTP_CLIENT.request(
        base_url,
        path,
        api_key=api_key,
        method=method,
        payload=payload,
        timeout=timeout,
    )
