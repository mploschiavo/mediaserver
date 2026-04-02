"""Proxy operations for Prowlarr."""

from __future__ import annotations

from typing import Any


def ensure_flaresolverr_proxy(
    service,
    prowlarr_url: str,
    prowlarr_key: str,
    flaresolverr_cfg: dict[str, Any] | None = None,
) -> None:
    cfg = dict(flaresolverr_cfg or {})
    proxy_name = str(cfg.get("proxy_name") or "FlareSolverr").strip() or "FlareSolverr"
    host = str(cfg.get("url") or "http://flaresolverr:8191").strip()
    if not host:
        raise RuntimeError("Prowlarr: FlareSolverr URL must be non-empty.")
    host = host.rstrip("/") + "/"
    try:
        request_timeout = int(cfg.get("request_timeout_seconds", 60))
    except (TypeError, ValueError):
        request_timeout = 60
    request_timeout = max(1, request_timeout)
    tags_raw = cfg.get("tags")
    tags: list[int] = []
    if isinstance(tags_raw, list):
        for tag in tags_raw:
            text = str(tag).strip()
            if not text:
                continue
            try:
                tags.append(int(text))
            except ValueError:
                continue
    test_connection = bool(cfg.get("test_connection", True))

    status, schema_list, body = service.http_request(
        prowlarr_url,
        "/api/v1/indexerProxy/schema",
        api_key=prowlarr_key,
    )
    if status != 200 or not isinstance(schema_list, list):
        raise RuntimeError(f"Prowlarr: failed to read indexer proxy schema (HTTP {status}): {body}")

    schema = next(
        (item for item in schema_list if item.get("implementation") == "FlareSolverr"), None
    )
    if not schema:
        raise RuntimeError("Prowlarr: FlareSolverr proxy schema not available.")

    status, proxies, body = service.http_request(
        prowlarr_url,
        "/api/v1/indexerProxy",
        api_key=prowlarr_key,
    )
    if status != 200 or not isinstance(proxies, list):
        raise RuntimeError(f"Prowlarr: failed to list indexer proxies (HTTP {status}): {body}")
    current = next(
        (
            item
            for item in proxies
            if item.get("implementation") == "FlareSolverr"
            or str(item.get("name") or "").strip().lower() == proxy_name.lower()
        ),
        None,
    )

    fields = service.field_map(schema.get("fields"))
    fields["host"] = host
    if "requestTimeout" in fields:
        fields["requestTimeout"] = request_timeout
    payload = {
        "name": proxy_name,
        "implementation": "FlareSolverr",
        "configContract": schema.get("configContract", "FlareSolverrSettings"),
        "enable": True,
        "tags": tags,
        "fields": service.field_list(fields),
    }

    if current:
        payload["id"] = current.get("id")
        status, response_data, body = service.http_request(
            prowlarr_url,
            f"/api/v1/indexerProxy/{current.get('id')}",
            api_key=prowlarr_key,
            method="PUT",
            payload=payload,
        )
        if status not in (200, 201, 202):
            raise RuntimeError(
                f"Prowlarr: failed updating FlareSolverr proxy (HTTP {status}): {body}"
            )
        resolved_proxy = response_data if isinstance(response_data, dict) else dict(payload)
        service.log(f"[OK] Prowlarr: updated FlareSolverr proxy '{proxy_name}' ({host})")
    else:
        status, response_data, body = service.http_request(
            prowlarr_url,
            "/api/v1/indexerProxy",
            api_key=prowlarr_key,
            method="POST",
            payload=payload,
        )
        if status not in (200, 201, 202):
            raise RuntimeError(
                f"Prowlarr: failed creating FlareSolverr proxy (HTTP {status}): {body}"
            )
        resolved_proxy = response_data if isinstance(response_data, dict) else dict(payload)
        service.log(f"[OK] Prowlarr: created FlareSolverr proxy '{proxy_name}' ({host})")

    if not test_connection:
        return

    status, _, body = service.http_request(
        prowlarr_url,
        "/api/v1/indexerProxy/test",
        api_key=prowlarr_key,
        method="POST",
        payload=resolved_proxy,
    )
    if status in (200, 201, 202):
        service.log("[OK] Prowlarr: FlareSolverr proxy connection test passed")
        return
    raise RuntimeError(f"Prowlarr: FlareSolverr proxy test failed (HTTP {status}): {body}")
