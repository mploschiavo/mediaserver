"""Application link operations for Prowlarr."""

from __future__ import annotations

from typing import Any


def resolve_schema_contract(
    service, prowlarr_url: str, prowlarr_key: str, implementation: str
) -> dict[str, Any]:
    status, data, body = service.http_request(
        prowlarr_url,
        "/api/v1/applications/schema",
        api_key=prowlarr_key,
    )
    if status != 200 or not isinstance(data, list):
        raise RuntimeError(f"Prowlarr: failed to read application schema (HTTP {status}): {body}")
    for entry in data:
        if entry.get("implementation") == implementation:
            return entry
    raise RuntimeError(f"Prowlarr: no application schema found for {implementation}")


def find_existing_application(
    service,
    prowlarr_url: str,
    prowlarr_key: str,
    implementation: str,
    base_url: str,
) -> dict[str, Any] | None:
    status, data, body = service.http_request(
        prowlarr_url,
        "/api/v1/applications",
        api_key=prowlarr_key,
    )
    if status != 200 or not isinstance(data, list):
        raise RuntimeError(f"Prowlarr: failed to list applications (HTTP {status}): {body}")

    for app in data:
        if app.get("implementation") != implementation:
            continue
        values = service.field_map(app.get("fields"))
        app_base = str(values.get("baseUrl", "")).rstrip("/")
        if app_base == base_url.rstrip("/"):
            return app
    return None


def ensure_application(
    service,
    prowlarr_url: str,
    prowlarr_key: str,
    app_name: str,
    implementation: str,
    app_url: str,
    app_key: str,
) -> None:
    schema = resolve_schema_contract(service, prowlarr_url, prowlarr_key, implementation)
    current = find_existing_application(
        service, prowlarr_url, prowlarr_key, implementation, app_url
    )

    values = service.field_map(schema.get("fields"))
    values["baseUrl"] = app_url
    values["apiKey"] = app_key
    if "prowlarrUrl" in values:
        values["prowlarrUrl"] = prowlarr_url

    payload = {
        "name": app_name,
        "implementation": implementation,
        "configContract": schema.get("configContract", f"{implementation}Settings"),
        "enable": True,
        "fields": service.field_list(values),
        "tags": [],
        "syncLevel": "fullSync",
    }

    def put_or_post(method: str, path: str, body: dict[str, Any]):
        status, _, response_body = service.http_request(
            prowlarr_url,
            path,
            api_key=prowlarr_key,
            method=method,
            payload=body,
        )
        if status in (200, 201, 202):
            return True, status, response_body

        if "syncLevel" in body:
            fallback = dict(body)
            fallback.pop("syncLevel", None)
            status2, _, response_body2 = service.http_request(
                prowlarr_url,
                path,
                api_key=prowlarr_key,
                method=method,
                payload=fallback,
            )
            if status2 in (200, 201, 202):
                return True, status2, response_body2
            return False, status2, response_body2

        return False, status, response_body

    if current:
        payload["id"] = current.get("id")
        ok, status, body = put_or_post("PUT", f"/api/v1/applications/{current.get('id')}", payload)
        if ok:
            service.log(f"[OK] Prowlarr: updated application link for {app_name}")
            return
        raise RuntimeError(f"Prowlarr: failed updating app {app_name} (HTTP {status}): {body}")

    ok, status, body = put_or_post("POST", "/api/v1/applications", payload)
    if ok:
        service.log(f"[OK] Prowlarr: created application link for {app_name}")
        return
    raise RuntimeError(f"Prowlarr: failed creating app {app_name} (HTTP {status}): {body}")


def trigger_sync(service, prowlarr_url: str, prowlarr_key: str) -> None:
    status, _, body = service.http_request(
        prowlarr_url,
        "/api/v1/command",
        api_key=prowlarr_key,
        method="POST",
        payload={"name": "ApplicationIndexerSync"},
    )
    if status in (200, 201, 202):
        service.log("[OK] Prowlarr: triggered ApplicationIndexerSync")
        return
    raise RuntimeError(
        f"Prowlarr: failed to trigger ApplicationIndexerSync (HTTP {status}): {body}"
    )
