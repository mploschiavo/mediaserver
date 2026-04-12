"""Indexer CRUD operations for Prowlarr."""

from __future__ import annotations

from typing import Any


class ProwlarrIndexerOps:

    def ensure_indexer(self, 
        service,
        prowlarr_url: str,
        prowlarr_key: str,
        indexer_cfg: dict[str, Any],
    ) -> None:
        implementation = indexer_cfg["implementation"]
        name = indexer_cfg["name"]
        field_overrides = indexer_cfg.get("fields", {})

        status, schemas, body = service.http_request(
            prowlarr_url,
            "/api/v1/indexer/schema",
            api_key=prowlarr_key,
        )
        if status != 200 or not isinstance(schemas, list):
            raise RuntimeError(f"Prowlarr: failed to read indexer schema (HTTP {status}): {body}")

        schema = None
        for entry in schemas:
            if entry.get("implementation") == implementation:
                schema = entry
                break
        if not schema:
            raise RuntimeError(f"Prowlarr: no indexer schema found for {implementation}")

        status, current_indexers, body = service.http_request(
            prowlarr_url,
            "/api/v1/indexer",
            api_key=prowlarr_key,
        )
        if status != 200 or not isinstance(current_indexers, list):
            raise RuntimeError(f"Prowlarr: failed to list indexers (HTTP {status}): {body}")

        current = None
        for item in current_indexers:
            if item.get("implementation") == implementation and item.get("name") == name:
                current = item
                break

        values = service.field_map(schema.get("fields"))
        values.update(field_overrides)

        payload = {
            "name": name,
            "implementation": implementation,
            "configContract": schema.get("configContract", f"{implementation}Settings"),
            "enable": bool(indexer_cfg.get("enable", True)),
            "priority": int(indexer_cfg.get("priority", 25)),
            "tags": indexer_cfg.get("tags", []),
            "fields": service.field_list(values),
        }

        if current:
            payload["id"] = current.get("id")
            status, _, body = service.http_request(
                prowlarr_url,
                f"/api/v1/indexer/{current.get('id')}",
                api_key=prowlarr_key,
                method="PUT",
                payload=payload,
            )
            if status in (200, 202):
                service.log(f"[OK] Prowlarr: updated indexer {name}")
                return
            raise RuntimeError(f"Prowlarr: failed to update indexer {name} (HTTP {status}): {body}")

        status, _, body = service.http_request(
            prowlarr_url,
            "/api/v1/indexer",
            api_key=prowlarr_key,
            method="POST",
            payload=payload,
        )
        if status in (200, 201, 202):
            service.log(f"[OK] Prowlarr: created indexer {name}")
            return
        raise RuntimeError(f"Prowlarr: failed to create indexer {name} (HTTP {status}): {body}")

    def build_indexer_payload(self, service, template: dict[str, Any]) -> dict[str, Any]:
        allowed_keys = {
            "name",
            "implementation",
            "configContract",
            "fields",
            "priority",
            "tags",
            "appProfileId",
            "downloadClientId",
            "enable",
            "redirect",
            "enableRss",
            "enableAutomaticSearch",
            "enableInteractiveSearch",
        }
        payload = {}
        for key in allowed_keys:
            if key in template and template[key] is not None:
                payload[key] = template[key]

        payload.setdefault("enable", True)
        payload.setdefault("priority", 25)
        payload.setdefault("tags", [])
        payload.setdefault("fields", [])
        app_profile_id = payload.get("appProfileId")
        try:
            app_profile_id_int = int(app_profile_id)
        except (TypeError, ValueError):
            app_profile_id_int = 0
        if app_profile_id_int <= 0:
            payload["appProfileId"] = 1
        return payload


_instance = ProwlarrIndexerOps()
ensure_indexer = _instance.ensure_indexer
build_indexer_payload = _instance.build_indexer_payload
