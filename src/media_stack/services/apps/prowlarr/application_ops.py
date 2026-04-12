"""Application link operations for Prowlarr."""

from __future__ import annotations

import json
from typing import Any


class ProwlarrApplicationOps:

    def resolve_schema_contract(self, 
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

    def find_existing_application(self, 
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

    def find_existing_application_by_name(self, 
        service,
        prowlarr_url: str,
        prowlarr_key: str,
        implementation: str,
        app_name: str,
    ) -> dict[str, Any] | None:
        status, data, body = service.http_request(
            prowlarr_url,
            "/api/v1/applications",
            api_key=prowlarr_key,
        )
        if status != 200 or not isinstance(data, list):
            raise RuntimeError(f"Prowlarr: failed to list applications (HTTP {status}): {body}")

        expected = str(app_name or "").strip().lower()
        for app in data:
            if app.get("implementation") != implementation:
                continue
            name = str(app.get("name") or "").strip().lower()
            if expected and name == expected:
                return app
        return None

    @staticmethod
    def _is_name_unique_conflict(status: int, body: str) -> bool:
        if int(status) != 400:
            return False
        token = str(body or "").strip().lower()
        if "name should be unique" in token:
            return True
        if "should be unique" in token and "propertyname" in token and "name" in token:
            return True
        try:
            payload = json.loads(str(body or ""))
        except Exception:
            return False
        if not isinstance(payload, list):
            return False
        for item in payload:
            if not isinstance(item, dict):
                continue
            property_name = str(item.get("propertyName") or "").strip().lower()
            error_message = str(item.get("errorMessage") or "").strip().lower()
            if property_name == "name" and "unique" in error_message:
                return True
        return False

    def ensure_application(self, 
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
        if _is_name_unique_conflict(status, body):
            duplicate = find_existing_application_by_name(
                service,
                prowlarr_url,
                prowlarr_key,
                implementation,
                app_name,
            )
            if duplicate and duplicate.get("id") is not None:
                payload["id"] = duplicate.get("id")
                ok2, status2, body2 = put_or_post(
                    "PUT",
                    f"/api/v1/applications/{duplicate.get('id')}",
                    payload,
                )
                if ok2:
                    service.log(
                        f"[OK] Prowlarr: reconciled duplicate-name application link for {app_name}"
                    )
                    return
                raise RuntimeError(
                    "Prowlarr: failed updating duplicate-name app "
                    f"{app_name} (HTTP {status2}): {body2}"
                )
        raise RuntimeError(f"Prowlarr: failed creating app {app_name} (HTTP {status}): {body}")

    def trigger_sync(self, service, prowlarr_url: str, prowlarr_key: str) -> None:
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


_instance = ProwlarrApplicationOps()
resolve_schema_contract = _instance.resolve_schema_contract
find_existing_application = _instance.find_existing_application
find_existing_application_by_name = _instance.find_existing_application_by_name
ensure_application = _instance.ensure_application
trigger_sync = _instance.trigger_sync
_is_name_unique_conflict = _instance._is_name_unique_conflict
