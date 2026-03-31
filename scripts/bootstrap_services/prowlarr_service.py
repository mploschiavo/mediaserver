"""Prowlarr API orchestration service."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
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

    @staticmethod
    def _reputation_key(implementation: str, name: str) -> str:
        return f"{implementation}::{name}".lower()

    def _load_reputation_state(self, path: Path) -> dict[str, Any]:
        if path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    return loaded
            except Exception:
                pass
        return {"schema": 1, "indexers": {}}

    def _save_reputation_state(self, path: Path, state: dict[str, Any]) -> bool:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            state["updated_at_epoch"] = int(time.time())
            path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
            return True
        except Exception as exc:
            self.log(
                "[WARN] Auto indexer reputation: failed persisting state "
                f"to {path}: {exc}"
            )
            return False

    def _set_indexer_enabled(
        self,
        prowlarr_url: str,
        prowlarr_key: str,
        indexer: dict[str, Any],
        enabled: bool,
    ) -> bool:
        idx_id = indexer.get("id")
        if idx_id in (None, ""):
            return False
        payload = dict(indexer)
        payload["enable"] = bool(enabled)
        status, _, body = self.http_request(
            prowlarr_url,
            f"/api/v1/indexer/{idx_id}",
            api_key=prowlarr_key,
            method="PUT",
            payload=payload,
        )
        if status in (200, 201, 202):
            return True
        self.log(
            "[WARN] Auto indexer reputation: failed updating indexer enable state "
            f"(id={idx_id}, enable={enabled}, HTTP {status}): {body}"
        )
        return False

    def auto_add_tested_indexers(
        self,
        prowlarr_url: str,
        prowlarr_key: str,
        exclude_name_tokens: list[str] | None = None,
        reputation_cfg: dict[str, Any] | None = None,
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
        existing_by_key = {
            self._reputation_key(str(item.get("implementation")), str(item.get("name"))): item
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

        reputation_cfg = dict(reputation_cfg or {})
        reputation_enabled = bool(reputation_cfg.get("enabled", True))
        reputation_state_path = Path(
            str(
                reputation_cfg.get("state_path")
                or os.environ.get(
                    "AUTO_INDEXER_REPUTATION_STATE_PATH",
                    "/srv-config/prowlarr/indexer-reputation-state.json",
                )
            )
        )
        quarantine_threshold = int(reputation_cfg.get("quarantine_score_threshold", -10))
        quarantine_failures = int(reputation_cfg.get("quarantine_failure_threshold", 3))
        quarantine_ttl_hours = int(reputation_cfg.get("quarantine_ttl_hours", 72))
        success_delta = int(reputation_cfg.get("success_score_delta", 2))
        test_fail_delta = int(reputation_cfg.get("test_failure_score_delta", -4))
        create_fail_delta = int(reputation_cfg.get("create_failure_score_delta", -3))

        reputation_state = self._load_reputation_state(reputation_state_path)
        if not isinstance(reputation_state.get("indexers"), dict):
            reputation_state["indexers"] = {}
        now_epoch = int(time.time())

        def state_for(impl: str, name: str) -> dict[str, Any]:
            key = self._reputation_key(impl, name)
            states = reputation_state["indexers"]
            item = states.get(key)
            if not isinstance(item, dict):
                item = {
                    "implementation": impl,
                    "name": name,
                    "score": 0,
                    "successes": 0,
                    "failures": 0,
                    "quarantined": False,
                    "quarantined_at_epoch": 0,
                }
                states[key] = item
            return item

        def maybe_quarantine(impl: str, name: str, rep: dict[str, Any]) -> None:
            nonlocal quarantined_now
            score = int(rep.get("score") or 0)
            failures = int(rep.get("failures") or 0)
            should_quarantine = (
                score <= quarantine_threshold
                and failures >= quarantine_failures
                and not bool(rep.get("quarantined", False))
            )
            if not should_quarantine:
                return

            rep["quarantined"] = True
            rep["quarantined_at_epoch"] = now_epoch
            quarantined_now += 1
            self.log(
                f"[WARN] Auto indexer: quarantined {name} "
                f"(score={score}, failures={failures})"
            )
            existing_item = existing_by_key.get(self._reputation_key(str(impl), str(name)))
            if existing_item and bool(existing_item.get("enable", True)):
                if self._set_indexer_enabled(
                    prowlarr_url,
                    prowlarr_key,
                    existing_item,
                    enabled=False,
                ):
                    self.log(f"[OK] Auto indexer: disabled quarantined indexer {name}")

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
        quarantined_now = 0
        skipped_quarantined = 0

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

            rep = state_for(str(impl), str(name))
            if reputation_enabled and bool(rep.get("quarantined", False)):
                quarantined_at = int(rep.get("quarantined_at_epoch") or 0)
                age_seconds = now_epoch - quarantined_at if quarantined_at > 0 else 0
                ttl_seconds = max(0, quarantine_ttl_hours * 3600)
                if ttl_seconds and age_seconds >= ttl_seconds:
                    rep["quarantined"] = False
                    rep["quarantined_at_epoch"] = 0
                    self.log(f"[INFO] Auto indexer: quarantine expired for {name}; retrying.")
                else:
                    skipped_quarantined += 1
                    if log_skip_details:
                        self.log(f"[SKIP] {name}: quarantined by reputation policy")
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
                if reputation_enabled:
                    rep["score"] = int(rep.get("score") or 0) + test_fail_delta
                    rep["failures"] = int(rep.get("failures") or 0) + 1
                    rep["last_failure_epoch"] = now_epoch
                    maybe_quarantine(str(impl), str(name), rep)
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
                existing_by_key[self._reputation_key(str(impl), str(name))] = {
                    "implementation": impl,
                    "name": name,
                    "enable": True,
                }
                added += 1
                if reputation_enabled:
                    rep["score"] = int(rep.get("score") or 0) + success_delta
                    rep["successes"] = int(rep.get("successes") or 0) + 1
                    rep["last_success_epoch"] = now_epoch
                    rep["quarantined"] = False
                    rep["quarantined_at_epoch"] = 0
                self.log(f"[ADD] {name}")
            else:
                failed_create += 1
                if reputation_enabled:
                    rep["score"] = int(rep.get("score") or 0) + create_fail_delta
                    rep["failures"] = int(rep.get("failures") or 0) + 1
                    rep["last_failure_epoch"] = now_epoch
                self.log(f"[FAIL] {name}: create failed (HTTP {status}) {body}")

            if reputation_enabled:
                maybe_quarantine(str(impl), str(name), rep)

            if scanned % heartbeat_every == 0:
                self.log(
                    "[WAIT] Auto indexer progress: "
                    f"scanned={scanned}/{len(candidates)}, attempted={attempted}, "
                    f"added={added}, skipped_existing={skipped_existing}, skipped_excluded={skipped_excluded}, "
                    f"skipped_quarantined={skipped_quarantined}, skipped_test={skipped_test}, "
                    f"failed_create={failed_create}, quarantined_now={quarantined_now}"
                )

        if reputation_enabled:
            self._save_reputation_state(reputation_state_path, reputation_state)

        self.log(
            "[OK] Auto indexer summary: "
            f"scanned={scanned}/{len(candidates)}, attempted={attempted}, added={added}, "
            f"skipped_existing={skipped_existing}, skipped_excluded={skipped_excluded}, skipped_test={skipped_test}, "
            f"skipped_quarantined={skipped_quarantined}, failed_create={failed_create}, "
            f"quarantined_now={quarantined_now}"
        )
