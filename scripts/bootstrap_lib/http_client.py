from __future__ import annotations

import json
from urllib import error, request

from .common import normalize_url


def http_request(base_url, path, api_key=None, method="GET", payload=None, timeout=20):
    url = f"{normalize_url(base_url)}{path}"
    data = None
    headers = {"Accept": "application/json"}

    if api_key:
        headers["X-Api-Key"] = api_key

    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = request.Request(url=url, data=data, method=method, headers=headers)
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
        return exc.code, None, body
    except error.URLError as exc:
        raise RuntimeError(f"Request failed for {url}: {exc}") from exc
