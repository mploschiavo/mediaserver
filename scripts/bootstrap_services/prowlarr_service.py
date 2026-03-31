"""Prowlarr API orchestration service."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable

HttpRequestFn = Callable[..., tuple[int, Any, str]]
FieldMapFn = Callable[[Any], dict[str, Any]]
FieldListFn = Callable[[dict[str, Any]], list[dict[str, Any]]]
LogFn = Callable[[str], None]


@dataclass
class ProwlarrService:
    http_request: HttpRequestFn
    field_map: FieldMapFn
    field_list: FieldListFn
    log: LogFn

    def resolve_schema_contract(
        self, prowlarr_url: str, prowlarr_key: str, implementation: str
    ) -> dict[str, Any]:
        status, data, body = self.http_request(
            prowlarr_url,
            "/api/v1/applications/schema",
            api_key=prowlarr_key,
        )
        if status != 200 or not isinstance(data, list):
            raise RuntimeError(
                f"Prowlarr: failed to read application schema (HTTP {status}): {body}"
            )

        for entry in data:
            if entry.get("implementation") == implementation:
                return entry
        raise RuntimeError(f"Prowlarr: no application schema found for {implementation}")

    def find_existing_application(
        self,
        prowlarr_url: str,
        prowlarr_key: str,
        implementation: str,
        base_url: str,
    ) -> dict[str, Any] | None:
        status, data, body = self.http_request(
            prowlarr_url,
            "/api/v1/applications",
            api_key=prowlarr_key,
        )
        if status != 200 or not isinstance(data, list):
            raise RuntimeError(f"Prowlarr: failed to list applications (HTTP {status}): {body}")

        for app in data:
            if app.get("implementation") != implementation:
                continue
            values = self.field_map(app.get("fields"))
            app_base = str(values.get("baseUrl", "")).rstrip("/")
            if app_base == base_url.rstrip("/"):
                return app
        return None

    def ensure_application(
        self,
        prowlarr_url: str,
        prowlarr_key: str,
        app_name: str,
        implementation: str,
        app_url: str,
        app_key: str,
    ) -> None:
        schema = self.resolve_schema_contract(prowlarr_url, prowlarr_key, implementation)
        current = self.find_existing_application(
            prowlarr_url,
            prowlarr_key,
            implementation,
            app_url,
        )

        values = self.field_map(schema.get("fields"))
        values["baseUrl"] = app_url
        values["apiKey"] = app_key
        if "prowlarrUrl" in values:
            values["prowlarrUrl"] = prowlarr_url

        payload = {
            "name": app_name,
            "implementation": implementation,
            "configContract": schema.get("configContract", f"{implementation}Settings"),
            "enable": True,
            "fields": self.field_list(values),
            "tags": [],
            "syncLevel": "fullSync",
        }

        def put_or_post(method: str, path: str, body: dict[str, Any]):
            status, _, response_body = self.http_request(
                prowlarr_url,
                path,
                api_key=prowlarr_key,
                method=method,
                payload=body,
            )
            if status in (200, 201, 202):
                return True, status, response_body

            # Compatibility fallback for versions that do not accept syncLevel shape/value.
            if "syncLevel" in body:
                fallback = dict(body)
                fallback.pop("syncLevel", None)
                status2, _, response_body2 = self.http_request(
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
            ok, status, body = put_or_post(
                "PUT",
                f"/api/v1/applications/{current.get('id')}",
                payload,
            )
            if ok:
                self.log(f"[OK] Prowlarr: updated application link for {app_name}")
                return
            raise RuntimeError(
                f"Prowlarr: failed updating app {app_name} (HTTP {status}): {body}"
            )

        ok, status, body = put_or_post("POST", "/api/v1/applications", payload)
        if ok:
            self.log(f"[OK] Prowlarr: created application link for {app_name}")
            return
        raise RuntimeError(f"Prowlarr: failed creating app {app_name} (HTTP {status}): {body}")

    def trigger_sync(self, prowlarr_url: str, prowlarr_key: str) -> None:
        status, _, body = self.http_request(
            prowlarr_url,
            "/api/v1/command",
            api_key=prowlarr_key,
            method="POST",
            payload={"name": "ApplicationIndexerSync"},
        )
        if status in (200, 201, 202):
            self.log("[OK] Prowlarr: triggered ApplicationIndexerSync")
            return
        raise RuntimeError(
            f"Prowlarr: failed to trigger ApplicationIndexerSync (HTTP {status}): {body}"
        )

    def ensure_indexer(
        self,
        prowlarr_url: str,
        prowlarr_key: str,
        indexer_cfg: dict[str, Any],
    ) -> None:
        implementation = indexer_cfg["implementation"]
        name = indexer_cfg["name"]
        field_overrides = indexer_cfg.get("fields", {})

        status, schemas, body = self.http_request(
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

        status, current_indexers, body = self.http_request(
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

        values = self.field_map(schema.get("fields"))
        values.update(field_overrides)

        payload = {
            "name": name,
            "implementation": implementation,
            "configContract": schema.get("configContract", f"{implementation}Settings"),
            "enable": bool(indexer_cfg.get("enable", True)),
            "priority": int(indexer_cfg.get("priority", 25)),
            "tags": indexer_cfg.get("tags", []),
            "fields": self.field_list(values),
        }

        if current:
            payload["id"] = current.get("id")
            status, _, body = self.http_request(
                prowlarr_url,
                f"/api/v1/indexer/{current.get('id')}",
                api_key=prowlarr_key,
                method="PUT",
                payload=payload,
            )
            if status in (200, 202):
                self.log(f"[OK] Prowlarr: updated indexer {name}")
                return
            raise RuntimeError(
                f"Prowlarr: failed to update indexer {name} (HTTP {status}): {body}"
            )

        status, _, body = self.http_request(
            prowlarr_url,
            "/api/v1/indexer",
            api_key=prowlarr_key,
            method="POST",
            payload=payload,
        )
        if status in (200, 201, 202):
            self.log(f"[OK] Prowlarr: created indexer {name}")
            return
        raise RuntimeError(f"Prowlarr: failed to create indexer {name} (HTTP {status}): {body}")

    def build_indexer_payload(self, template: dict[str, Any]) -> dict[str, Any]:
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
        return payload

    @staticmethod
    def _coerce_exclude_name_tokens(raw_tokens: Any) -> list[str]:
        if isinstance(raw_tokens, list):
            tokens = [str(item).strip().lower() for item in raw_tokens if str(item).strip()]
            return tokens
        if raw_tokens is None:
            return []
        text = str(raw_tokens).strip()
        if not text:
            return []
        return [item.strip().lower() for item in text.split(",") if item.strip()]

    def auto_add_tested_indexers(
        self,
        prowlarr_url: str,
        prowlarr_key: str,
        exclude_name_tokens: list[str] | None = None,
    ) -> None:
        status, schemas, body = self.http_request(
            prowlarr_url,
            "/api/v1/indexer/schema",
            api_key=prowlarr_key,
        )
        if status != 200 or not isinstance(schemas, list):
            raise RuntimeError(f"Prowlarr: failed to read indexer schema (HTTP {status}): {body}")

        status, existing, body = self.http_request(
            prowlarr_url,
            "/api/v1/indexer",
            api_key=prowlarr_key,
        )
        if status != 200 or not isinstance(existing, list):
            raise RuntimeError(
                f"Prowlarr: failed to list existing indexers (HTTP {status}): {body}"
            )

        existing_keys = {
            (item.get("implementation"), item.get("name"))
            for item in existing
            if item.get("implementation") and item.get("name")
        }

        candidates: list[dict[str, Any]] = []
        for schema in schemas:
            presets = schema.get("presets") or []
            if presets:
                candidates.extend(presets)
            else:
                candidates.append(schema)

        configured_excludes = self._coerce_exclude_name_tokens(exclude_name_tokens)
        env_excludes = self._coerce_exclude_name_tokens(
            os.environ.get("AUTO_INDEXER_EXCLUDE_NAME_TOKENS", "")
        )
        exclude_tokens = list(dict.fromkeys(configured_excludes + env_excludes))
        if exclude_tokens:
            self.log(
                "[INFO] Auto indexer: excluding candidates matching name tokens: "
                + ", ".join(exclude_tokens)
            )

        heartbeat_every = int(os.environ.get("AUTO_INDEXER_HEARTBEAT_EVERY", "25"))
        heartbeat_every = max(1, heartbeat_every)
        log_skip_details = str(os.environ.get("AUTO_INDEXER_LOG_SKIPS", "0")).strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )

        scanned = 0
        attempted = 0
        added = 0
        skipped_existing = 0
        skipped_excluded = 0
        skipped_test = 0
        failed_create = 0

        for candidate in candidates:
            payload = self.build_indexer_payload(candidate)
            impl = payload.get("implementation")
            name = payload.get("name")
            if not impl or not name:
                continue

            scanned += 1
            key = (impl, name)
            if key in existing_keys:
                skipped_existing += 1
                if scanned % heartbeat_every == 0:
                    self.log(
                        "[WAIT] Auto indexer progress: "
                        f"scanned={scanned}/{len(candidates)}, attempted={attempted}, "
                        f"added={added}, skipped_existing={skipped_existing}, "
                        f"skipped_test={skipped_test}, failed_create={failed_create}"
                    )
                continue

            attempted += 1
            if exclude_tokens:
                name_lc = str(name).lower()
                if any(token in name_lc for token in exclude_tokens):
                    skipped_excluded += 1
                    if log_skip_details:
                        self.log(f"[SKIP] {name}: excluded by name token policy")
                    continue

            status, _, body = self.http_request(
                prowlarr_url,
                "/api/v1/indexer/test",
                api_key=prowlarr_key,
                method="POST",
                payload=payload,
            )
            if status not in (200, 201, 202):
                skipped_test += 1
                if log_skip_details:
                    self.log(f"[SKIP] {name}: test failed (HTTP {status})")
                continue

            status, _, body = self.http_request(
                prowlarr_url,
                "/api/v1/indexer",
                api_key=prowlarr_key,
                method="POST",
                payload=payload,
            )
            if status in (200, 201, 202):
                existing_keys.add(key)
                added += 1
                self.log(f"[ADD] {name}")
            else:
                failed_create += 1
                self.log(f"[FAIL] {name}: create failed (HTTP {status}) {body}")

            if scanned % heartbeat_every == 0:
                self.log(
                    "[WAIT] Auto indexer progress: "
                    f"scanned={scanned}/{len(candidates)}, attempted={attempted}, "
                    f"added={added}, skipped_existing={skipped_existing}, skipped_excluded={skipped_excluded}, "
                    f"skipped_test={skipped_test}, failed_create={failed_create}"
                )

        self.log(
            "[OK] Auto indexer summary: "
            f"scanned={scanned}/{len(candidates)}, attempted={attempted}, added={added}, "
            f"skipped_existing={skipped_existing}, skipped_excluded={skipped_excluded}, skipped_test={skipped_test}, "
            f"failed_create={failed_create}"
        )
