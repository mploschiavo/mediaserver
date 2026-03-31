"""Maintainerr integration orchestration service."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

HttpRequestFn = Callable[..., tuple[int, Any, str]]
LogFn = Callable[[str], None]
BoolCfgFn = Callable[[dict[str, Any], str, bool], bool]
NormalizeUrlFn = Callable[[str], str]
WaitForServiceFn = Callable[[str, str, str, int], None]
ReadApiKeyFn = Callable[[str, str], str]
ReadJellyseerrApiKeyFn = Callable[[str, int], str]
GetArrAppFn = Callable[[list[dict[str, Any]], str], dict[str, Any] | None]
ResolvePathFn = Callable[[str, str], Any]


@dataclass
class MaintainerrService:
    log: LogFn
    bool_cfg: BoolCfgFn
    normalize_url: NormalizeUrlFn
    wait_for_service: WaitForServiceFn
    http_request: HttpRequestFn
    read_api_key: ReadApiKeyFn
    read_jellyseerr_api_key: ReadJellyseerrApiKeyFn
    get_arr_app: GetArrAppFn
    resolve_path: ResolvePathFn

    @staticmethod
    def _text(value: Any) -> str:
        return str(value or "").strip()

    @staticmethod
    def _token(value: Any) -> str:
        return str(value or "").strip().lower()

    def _ensure_enabled(self, cfg: dict[str, Any], key: str, default: bool = True) -> bool:
        return self.bool_cfg(cfg, key, default)

    def _service_section(self, integrations_cfg: dict[str, Any], name: str) -> dict[str, Any]:
        section = integrations_cfg.get(name) or {}
        return section if isinstance(section, dict) else {}

    def _request(
        self,
        base_url: str,
        path: str,
        *,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
    ) -> tuple[int, Any, str]:
        return self.http_request(
            base_url,
            path,
            method=method,
            payload=payload,
            timeout=30,
        )

    def _resolve_servarr_key(
        self,
        *,
        config_root: str,
        app_name: str,
        section_cfg: dict[str, Any],
        default_env: str,
    ) -> str:
        env_name = self._text(section_cfg.get("api_key_env") or default_env) or default_env
        env_value = self._text(os.environ.get(env_name))
        if env_value:
            self.log(f"[OK] Maintainerr: using {app_name} API key from env {env_name}")
            return env_value
        key = self._text(self.read_api_key(config_root, app_name))
        if not key:
            raise RuntimeError(
                f"Maintainerr: {app_name} API key is required but could not be resolved."
            )
        return key

    def _resolve_jellyseerr_key(
        self,
        *,
        config_root: str,
        wait_timeout: int,
        section_cfg: dict[str, Any],
    ) -> str:
        env_name = self._text(section_cfg.get("api_key_env") or "JELLYSEERR_API_KEY")
        env_value = self._text(os.environ.get(env_name))
        if env_value:
            self.log(f"[OK] Maintainerr: using Jellyseerr API key from env {env_name}")
            return env_value
        key = self._text(self.read_jellyseerr_api_key(config_root, wait_timeout))
        if not key:
            raise RuntimeError("Maintainerr: Jellyseerr API key is required but missing.")
        return key

    def _resolve_tautulli_key(self, *, config_root: str, section_cfg: dict[str, Any]) -> str:
        env_name = self._text(section_cfg.get("api_key_env") or "TAUTULLI_API_KEY")
        env_value = self._text(os.environ.get(env_name))
        if env_value:
            self.log(f"[OK] Maintainerr: using Tautulli API key from env {env_name}")
            return env_value

        ini_rel_path = self._text(section_cfg.get("api_key_config_path") or "tautulli/config.ini")
        ini_path = self.resolve_path(config_root, ini_rel_path)
        if not ini_path.exists():
            raise RuntimeError(
                f"Maintainerr: Tautulli API key is required and {ini_path} does not exist."
            )
        text = ini_path.read_text(encoding="utf-8", errors="replace")
        match = re.search(r"^\s*api_key\s*=\s*(\S+)\s*$", text, flags=re.MULTILINE)
        if not match:
            raise RuntimeError(
                f"Maintainerr: Tautulli API key is required and not present in {ini_path}."
            )
        return self._text(match.group(1))

    def _resolve_url(self, section_cfg: dict[str, Any], default_url: str) -> str:
        url = self._text(section_cfg.get("url") or default_url)
        if not url:
            raise RuntimeError("Maintainerr: integration URL is missing.")
        return self.normalize_url(url)

    @staticmethod
    def _as_int(value: Any, default: int = 0) -> int:
        try:
            return int(str(value).strip())
        except Exception:
            return default

    @staticmethod
    def _as_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(str(value).strip())
        except Exception:
            return default

    @staticmethod
    def _iso_days_ago(days: int) -> str:
        dt = datetime.now(timezone.utc) - timedelta(days=max(days, 0))
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    @staticmethod
    def _coerce_list(value: Any) -> list[Any]:
        if value is None:
            return []
        if isinstance(value, list):
            return value
        return [value]

    def _resolve_relative_time_token(self, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        token = value.strip()
        if not token:
            return value

        patterns = (
            r"^\{\{\s*days_ago:(\d+)\s*\}\}$",
            r"^days_ago:(\d+)$",
            r"^now-(\d+)d$",
        )
        for pattern in patterns:
            match = re.match(pattern, token, flags=re.IGNORECASE)
            if match:
                return self._iso_days_ago(self._as_int(match.group(1), 0))
        return value

    def _resolve_libraries(self, maintainerr_url: str) -> list[dict[str, str]]:
        status, data, body = self._request(maintainerr_url, "/api/media-server/libraries")
        if status != 200 or not isinstance(data, list):
            raise RuntimeError(
                f"Maintainerr: failed reading media libraries (HTTP {status}): {body}"
            )
        libraries: list[dict[str, str]] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            lib_id = self._text(item.get("id"))
            title = self._text(item.get("title"))
            lib_type = self._token(item.get("type"))
            if not lib_id or not title or lib_type not in {"movie", "show"}:
                continue
            libraries.append({"id": lib_id, "title": title, "type": lib_type})
        return libraries

    def _jellyfin_rule_entry(
        self,
        *,
        first_prop_id: int,
        action: int,
        section: int,
        operator: int | None,
        custom_rule_type_id: int,
        custom_value: str,
    ) -> dict[str, Any]:
        return {
            "firstVal": [6, int(first_prop_id)],
            "operator": operator,
            "action": int(action),
            "customVal": {
                "ruleTypeId": int(custom_rule_type_id),
                "value": str(custom_value),
            },
            "section": int(section),
        }

    def _fallback_condition(self, data_type: str) -> dict[str, Any]:
        watched_prop = 5 if data_type == "movie" else 17
        return self._jellyfin_rule_entry(
            first_prop_id=watched_prop,
            action=0,
            section=0,
            operator=None,
            custom_rule_type_id=0,
            custom_value="0",
        )

    def _build_rule_conditions(
        self,
        *,
        conditions: dict[str, Any],
        data_type: str,
    ) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []

        def append(entry: dict[str, Any]) -> None:
            if entries:
                entry["operator"] = 0
            entries.append(entry)

        if conditions.get("favorited_by_any_user") is True:
            fav_prop = 39 if data_type == "movie" else 41
            append(
                self._jellyfin_rule_entry(
                    first_prop_id=fav_prop,
                    action=16,
                    section=0,
                    operator=None,
                    custom_rule_type_id=0,
                    custom_value="0",
                )
            )

        if "watched" in conditions:
            watched = bool(conditions.get("watched"))
            watched_prop = 5 if data_type == "movie" else 17
            append(
                self._jellyfin_rule_entry(
                    first_prop_id=watched_prop,
                    action=0 if watched else 2,
                    section=0,
                    operator=None,
                    custom_rule_type_id=0,
                    custom_value="0",
                )
            )

        if "added_days_ago_gte" in conditions:
            days = self._as_int(conditions.get("added_days_ago_gte"), 0)
            append(
                self._jellyfin_rule_entry(
                    first_prop_id=0,
                    action=5,
                    section=0,
                    operator=None,
                    custom_rule_type_id=1,
                    custom_value=self._iso_days_ago(days),
                )
            )

        if "not_watched_for_days" in conditions:
            days = self._as_int(conditions.get("not_watched_for_days"), 0)
            last_watch_prop = 7 if data_type == "movie" else 13
            append(
                self._jellyfin_rule_entry(
                    first_prop_id=last_watch_prop,
                    action=5,
                    section=0,
                    operator=None,
                    custom_rule_type_id=1,
                    custom_value=self._iso_days_ago(days),
                )
            )

        if "last_watched_days_ago_gte" in conditions:
            days = self._as_int(conditions.get("last_watched_days_ago_gte"), 0)
            last_watch_prop = 7 if data_type == "movie" else 13
            append(
                self._jellyfin_rule_entry(
                    first_prop_id=last_watch_prop,
                    action=5,
                    section=0,
                    operator=None,
                    custom_rule_type_id=1,
                    custom_value=self._iso_days_ago(days),
                )
            )

        if self._token(conditions.get("requested_via")) == "jellyseerr":
            entry: dict[str, Any] = {
                "firstVal": [3, 6],
                "operator": 0 if entries else None,
                "action": 2,
                "customVal": {"ruleTypeId": 3, "value": "1"},
                "section": 0,
            }
            entries.append(entry)

        if "requested_days_ago_gte" in conditions:
            days = self._as_int(conditions.get("requested_days_ago_gte"), 0)
            entry = {
                "firstVal": [3, 1],
                "operator": 0 if entries else None,
                "action": 5,
                "customVal": {"ruleTypeId": 1, "value": self._iso_days_ago(days)},
                "section": 0,
            }
            entries.append(entry)

        if "community_rating_gte" in conditions:
            rating_prop = 34 if data_type == "movie" else 38
            rating_val = self._as_float(conditions.get("community_rating_gte"), 0.0)
            append(
                self._jellyfin_rule_entry(
                    first_prop_id=rating_prop,
                    action=0,
                    section=0,
                    operator=None,
                    custom_rule_type_id=0,
                    custom_value=str(rating_val),
                )
            )

        return entries

    def _map_arr_action(self, actions: dict[str, Any]) -> int:
        if actions.get("arr_unmonitor") is True:
            return 3
        mode = self._token(actions.get("arr_delete_or_unmonitor"))
        if mode == "unmonitor":
            return 3
        if mode == "delete":
            return 0
        if actions.get("delete_item") is True:
            return 0
        return 4

    def _resolve_target_libraries(
        self,
        *,
        rule: dict[str, Any],
        libraries: list[dict[str, str]],
        by_title: dict[str, dict[str, str]],
        rule_name: str,
    ) -> list[dict[str, str]]:
        requested_library_id = self._text(rule.get("libraryId") or rule.get("library_id"))
        if requested_library_id:
            matches = [lib for lib in libraries if self._text(lib.get("id")) == requested_library_id]
            if not matches:
                self.log(
                    f"[WARN] Maintainerr: library id '{requested_library_id}' not found for rule '{rule_name}'."
                )
            return matches

        library_names_payload = None
        if "library_titles" in rule:
            library_names_payload = rule.get("library_titles")
        elif "libraryTitles" in rule:
            library_names_payload = rule.get("libraryTitles")
        elif "libraryTitle" in rule:
            library_names_payload = rule.get("libraryTitle")
        elif "libraries" in rule:
            library_names_payload = rule.get("libraries")

        requested_libraries = [
            self._token(name)
            for name in self._coerce_list(library_names_payload)
            if self._text(name)
        ]
        if not requested_libraries:
            return libraries

        target_libraries: list[dict[str, str]] = []
        for lib_name in requested_libraries:
            lib = by_title.get(lib_name)
            if lib is None:
                self.log(
                    f"[WARN] Maintainerr: skipping unsupported library '{lib_name}' for rule '{rule_name}'."
                )
                continue
            target_libraries.append(lib)
        return target_libraries

    def _normalize_native_rules(self, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for idx, entry in enumerate(entries):
            if not isinstance(entry, dict):
                continue
            clause_source: Any = entry
            if "ruleJson" in entry and "firstVal" not in entry:
                rule_json = entry.get("ruleJson")
                if isinstance(rule_json, str) and rule_json.strip():
                    try:
                        parsed = json.loads(rule_json)
                        if isinstance(parsed, dict):
                            clause_source = parsed
                    except Exception:
                        continue

            copied = json.loads(json.dumps(clause_source))
            first_val = copied.get("firstVal")
            if isinstance(first_val, list) and len(first_val) >= 2:
                copied["firstVal"] = [self._as_int(first_val[0], 0), self._as_int(first_val[1], 0)]

            copied["section"] = self._as_int(copied.get("section"), 0)
            if "operator" not in copied:
                copied["operator"] = None if idx == 0 else 0

            custom_val = copied.get("customVal")
            if isinstance(custom_val, dict) and "value" in custom_val:
                custom_val["value"] = self._resolve_relative_time_token(custom_val.get("value"))
                copied["customVal"] = custom_val

            normalized.append(copied)
        return normalized

    def _default_collection_config(
        self,
        *,
        name: str,
        delete_after_days: int | None = None,
        overrides: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        base = {
            "visibleOnRecommended": False,
            "visibleOnHome": False,
            "deleteAfterDays": delete_after_days,
            "manualCollection": False,
            "manualCollectionName": name,
            "keepLogsForMonths": 3,
            "sortTitle": "",
        }
        if isinstance(overrides, dict):
            for key, value in overrides.items():
                base[key] = json.loads(json.dumps(value))
        return base

    def _native_rule_payloads(
        self,
        *,
        rule: dict[str, Any],
        base_name: str,
        description: str,
        target_libraries: list[dict[str, str]],
    ) -> list[dict[str, Any]]:
        native_rules_raw = rule.get("rules") or []
        native_rules = self._normalize_native_rules(
            [entry for entry in native_rules_raw if isinstance(entry, dict)]
        )
        if not native_rules:
            self.log(
                f"[WARN] Maintainerr: native rule '{base_name}' had no valid rule clauses; skipping."
            )
            return []

        arr_action = self._as_int(rule.get("arrAction"), 4)
        use_rules = bool(rule.get("useRules", True))
        is_active = bool(rule.get("isActive", True))
        list_exclusions = bool(rule.get("listExclusions", False))
        force_seerr = bool(rule.get("forceSeerr", False))
        notifications = rule.get("notifications")
        if not isinstance(notifications, list):
            notifications = []
        cron_schedule = self._text(rule.get("ruleHandlerCronSchedule"))
        tautulli_override = rule.get("tautulliWatchedPercentOverride")
        radarr_settings_id = rule.get("radarrSettingsId")
        sonarr_settings_id = rule.get("sonarrSettingsId")
        payloads: list[dict[str, Any]] = []
        multi_library = len(target_libraries) > 1

        for lib in target_libraries:
            rule_name = f"{base_name} ({lib['title']})" if multi_library else base_name
            data_type = self._token(rule.get("dataType")) or lib["type"]
            payload: dict[str, Any] = {
                "libraryId": lib["id"],
                "name": rule_name,
                "description": description,
                "isActive": is_active,
                "arrAction": arr_action,
                "useRules": use_rules,
                "listExclusions": list_exclusions,
                "forceSeerr": force_seerr,
                "rules": json.loads(json.dumps(native_rules)),
                "dataType": data_type,
                "notifications": json.loads(json.dumps(notifications)),
                "collection": self._default_collection_config(
                    name=rule_name,
                    overrides=rule.get("collection") if isinstance(rule.get("collection"), dict) else None,
                ),
            }
            if cron_schedule:
                payload["ruleHandlerCronSchedule"] = cron_schedule
            if tautulli_override is not None:
                payload["tautulliWatchedPercentOverride"] = tautulli_override
            if radarr_settings_id is not None:
                payload["radarrSettingsId"] = radarr_settings_id
            if sonarr_settings_id is not None:
                payload["sonarrSettingsId"] = sonarr_settings_id
            payloads.append(payload)
        return payloads

    def _desired_rule_payloads(
        self,
        *,
        policy_rules: list[dict[str, Any]],
        libraries: list[dict[str, str]],
    ) -> list[dict[str, Any]]:
        by_title = {self._token(lib["title"]): lib for lib in libraries}
        desired: list[dict[str, Any]] = []

        for rule in policy_rules:
            if not isinstance(rule, dict):
                continue
            base_name = self._text(rule.get("name"))
            if not base_name:
                continue
            description = self._text(rule.get("description")) or "Seeded by bootstrap policy"
            target_libraries = self._resolve_target_libraries(
                rule=rule,
                libraries=libraries,
                by_title=by_title,
                rule_name=base_name,
            )
            if not target_libraries:
                self.log(f"[WARN] Maintainerr: no compatible libraries found for rule '{base_name}'.")
                continue

            if isinstance(rule.get("rules"), list):
                desired.extend(
                    self._native_rule_payloads(
                        rule=rule,
                        base_name=base_name,
                        description=description,
                        target_libraries=target_libraries,
                    )
                )
                continue

            conditions = rule.get("conditions") or {}
            actions = rule.get("actions") or {}
            if not isinstance(conditions, dict):
                conditions = {}
            if not isinstance(actions, dict):
                actions = {}

            delete_after_days = None
            if "collection_days_before_delete" in actions:
                days = self._as_int(actions.get("collection_days_before_delete"), 0)
                delete_after_days = days if days > 0 else None
            collection_title = self._text(actions.get("add_to_collection")) or base_name
            arr_action = self._map_arr_action(actions)
            force_seerr = bool(actions.get("remove_request_record"))

            multi_library = len(target_libraries) > 1
            for lib in target_libraries:
                lib_name = lib["title"]
                data_type = lib["type"]
                rule_name = f"{base_name} ({lib_name})" if multi_library else base_name
                rules = self._build_rule_conditions(conditions=conditions, data_type=data_type)
                if not rules:
                    self.log(
                        f"[WARN] Maintainerr: rule '{rule_name}' had no translatable conditions; "
                        "using watched-content fallback condition."
                    )
                    rules = [self._fallback_condition(data_type)]

                desired.append(
                    {
                        "libraryId": lib["id"],
                        "name": rule_name,
                        "description": description,
                        "isActive": True,
                        "arrAction": arr_action,
                        "useRules": True,
                        "listExclusions": False,
                        "forceSeerr": force_seerr,
                        "rules": rules,
                        "dataType": data_type,
                        "notifications": [],
                        "collection": self._default_collection_config(
                            name=collection_title,
                            delete_after_days=delete_after_days,
                        ),
                    }
                )

        return desired

    def _sync_policy_rules(
        self,
        *,
        maintainerr_url: str,
        maintainerr_cfg: dict[str, Any],
        config_root: str,
    ) -> None:
        policy_rel_path = self._text(
            maintainerr_cfg.get("policy_relative_path") or "maintainerr/policy.json"
        )
        policy_path = self.resolve_path(config_root, policy_rel_path)
        if not policy_path.exists():
            self.log(f"[WARN] Maintainerr: policy file not found at {policy_path}; skipping rule sync.")
            return
        policy_doc = json.loads(policy_path.read_text(encoding="utf-8", errors="replace") or "{}")
        policy_rules = policy_doc.get("rules") or []
        if not isinstance(policy_rules, list):
            raise RuntimeError("Maintainerr: policy rules must be a list.")
        if not policy_rules:
            self.log("[INFO] Maintainerr: no policy rules to sync.")
            return

        libraries = self._resolve_libraries(maintainerr_url)
        if not libraries:
            raise RuntimeError("Maintainerr: no compatible media-server libraries available.")

        desired_rules = self._desired_rule_payloads(
            policy_rules=policy_rules,
            libraries=libraries,
        )
        if not desired_rules:
            self.log("[WARN] Maintainerr: no translatable rules were produced from policy.")
            return

        status, existing_data, body = self._request(maintainerr_url, "/api/rules?activeOnly=false")
        if status != 200 or not isinstance(existing_data, list):
            raise RuntimeError(
                f"Maintainerr: failed reading existing rules (HTTP {status}): {body}"
            )
        existing_by_name: dict[str, dict[str, Any]] = {}
        for item in existing_data:
            if not isinstance(item, dict):
                continue
            name = self._text(item.get("name"))
            if not name:
                continue
            existing_by_name[name] = item

        created = 0
        updated = 0
        for desired in desired_rules:
            existing = existing_by_name.get(self._text(desired.get("name")))
            method = "POST"
            payload = dict(desired)
            if isinstance(existing, dict):
                existing_id = existing.get("id")
                if existing_id is not None:
                    payload["id"] = existing_id
                    method = "PUT"
            status, data, body = self._request(
                maintainerr_url,
                "/api/rules",
                method=method,
                payload=payload,
            )
            if status < 200 or status >= 300:
                raise RuntimeError(
                    f"Maintainerr: failed syncing rule '{payload.get('name')}' "
                    f"(HTTP {status}): {body}"
                )
            if isinstance(data, dict) and data.get("code") == 0:
                raise RuntimeError(
                    f"Maintainerr: rule sync failed for '{payload.get('name')}': {data.get('result')}"
                )
            if method == "POST":
                created += 1
            else:
                updated += 1

        self.log(
            f"[OK] Maintainerr: synced policy rules (created={created}, updated={updated}, "
            f"total_desired={len(desired_rules)})"
        )

    def _test_connection(
        self,
        maintainerr_url: str,
        integration_name: str,
        payload: dict[str, Any],
        *,
        enabled: bool,
    ) -> None:
        if not enabled:
            return
        status, _, body = self._request(
            maintainerr_url,
            f"/api/settings/test/{integration_name}",
            method="POST",
            payload=payload,
        )
        if status < 200 or status >= 300:
            raise RuntimeError(
                f"Maintainerr: {integration_name} test failed (HTTP {status}): {body}"
            )
        self.log(f"[OK] Maintainerr: {integration_name} connection test passed")

    def _find_matching_servarr_entry(
        self,
        entries: list[dict[str, Any]],
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        desired_server_name = self._token(payload.get("serverName"))
        desired_url = self._token(payload.get("url"))
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            server_name = self._token(entry.get("serverName"))
            entry_url = self._token(entry.get("url"))
            if desired_server_name and server_name == desired_server_name:
                return entry
            if desired_url and entry_url == desired_url:
                return entry
        return None

    def _ensure_servarr_integration(
        self,
        maintainerr_url: str,
        integration_name: str,
        payload: dict[str, Any],
        *,
        test_connections: bool,
    ) -> None:
        endpoint = f"/api/settings/{integration_name}"
        status, data, body = self._request(maintainerr_url, endpoint)
        if status != 200 or not isinstance(data, list):
            raise RuntimeError(
                f"Maintainerr: failed reading {integration_name} settings (HTTP {status}): {body}"
            )

        current = self._find_matching_servarr_entry(data, payload)
        desired_url = self._token(payload.get("url"))
        desired_name = self._token(payload.get("serverName"))
        desired_key = self._text(payload.get("apiKey"))
        needs_update = True
        if isinstance(current, dict):
            cur_name = self._token(current.get("serverName"))
            cur_url = self._token(current.get("url"))
            cur_key = self._text(current.get("apiKey"))
            needs_update = not (
                cur_name == desired_name and cur_url == desired_url and cur_key == desired_key
            )

        if needs_update:
            status, _, body = self._request(
                maintainerr_url,
                endpoint,
                method="POST",
                payload=payload,
            )
            if status < 200 or status >= 300:
                raise RuntimeError(
                    f"Maintainerr: failed saving {integration_name} settings (HTTP {status}): {body}"
                )
            self.log(f"[OK] Maintainerr: configured {integration_name} integration")
        else:
            self.log(f"[OK] Maintainerr: {integration_name} integration already configured")

        self._test_connection(
            maintainerr_url,
            integration_name,
            payload,
            enabled=test_connections,
        )

    def _ensure_single_endpoint_integration(
        self,
        maintainerr_url: str,
        integration_name: str,
        payload: dict[str, Any],
        *,
        test_connections: bool,
    ) -> None:
        endpoint = f"/api/settings/{integration_name}"
        status, data, body = self._request(maintainerr_url, endpoint)
        if status != 200 or not isinstance(data, dict):
            raise RuntimeError(
                f"Maintainerr: failed reading {integration_name} settings (HTTP {status}): {body}"
            )

        needs_update = not (
            self._token(data.get("url")) == self._token(payload.get("url"))
            and self._text(data.get("api_key")) == self._text(payload.get("api_key"))
        )
        if needs_update:
            status, _, body = self._request(
                maintainerr_url,
                endpoint,
                method="POST",
                payload=payload,
            )
            if status < 200 or status >= 300:
                raise RuntimeError(
                    f"Maintainerr: failed saving {integration_name} settings (HTTP {status}): {body}"
                )
            self.log(f"[OK] Maintainerr: configured {integration_name} integration")
        else:
            self.log(f"[OK] Maintainerr: {integration_name} integration already configured")

        self._test_connection(
            maintainerr_url,
            integration_name,
            payload,
            enabled=test_connections,
        )

    def _ensure_main_settings(
        self,
        maintainerr_url: str,
        *,
        cfg: dict[str, Any],
        maintainerr_cfg: dict[str, Any],
        integrations_cfg: dict[str, Any],
        config_root: str,
        wait_timeout: int,
    ) -> None:
        main_section = self._service_section(integrations_cfg, "main")
        if not self._ensure_enabled(main_section, "enabled", True):
            return

        status, current, body = self._request(maintainerr_url, "/api/settings")
        if status != 200 or not isinstance(current, dict):
            raise RuntimeError(
                f"Maintainerr: failed reading main settings (HTTP {status}): {body}"
            )

        desired = dict(current)

        application_url = self._text(
            main_section.get("application_url")
            or maintainerr_cfg.get("application_url")
            or maintainerr_cfg.get("external_url")
            or os.environ.get("MAINTAINERR_APPLICATION_URL")
            or "maintainerr.local"
        )
        if application_url:
            desired["applicationUrl"] = application_url

        media_server_type = self._text(
            main_section.get("media_server_type")
            or desired.get("media_server_type")
            or "jellyfin"
        ).lower()
        if media_server_type:
            desired["media_server_type"] = media_server_type

        jellyseerr_section = self._service_section(integrations_cfg, "jellyseerr")
        jellyseerr_cfg = cfg.get("jellyseerr") or {}
        desired["seerr_url"] = self._resolve_url(
            jellyseerr_section,
            self._text(jellyseerr_cfg.get("url") or "http://jellyseerr:5055"),
        )
        if self._ensure_enabled(jellyseerr_section, "enabled", True):
            desired["seerr_api_key"] = self._resolve_jellyseerr_key(
                config_root=config_root,
                wait_timeout=wait_timeout,
                section_cfg=jellyseerr_section,
            )

        jellyfin_cfg = cfg.get("jellyfin") or {}
        desired["jellyfin_url"] = self._resolve_url(
            main_section,
            self._text(main_section.get("jellyfin_url") or jellyfin_cfg.get("url") or "http://jellyfin:8096"),
        )
        desired["jellyfin_server_name"] = self._text(
            main_section.get("jellyfin_server_name")
            or desired.get("jellyfin_server_name")
            or "Jellyfin"
        )

        jellyfin_api_env = self._text(main_section.get("jellyfin_api_key_env") or "JELLYFIN_API_KEY")
        jellyfin_api_key = self._text(os.environ.get(jellyfin_api_env))
        if jellyfin_api_key:
            desired["jellyfin_api_key"] = jellyfin_api_key

        jellyfin_user_env = self._text(main_section.get("jellyfin_user_id_env") or "JELLYFIN_USER_ID")
        jellyfin_user_id = self._text(os.environ.get(jellyfin_user_env))
        if jellyfin_user_id:
            desired["jellyfin_user_id"] = jellyfin_user_id

        tautulli_section = self._service_section(integrations_cfg, "tautulli")
        if self._ensure_enabled(tautulli_section, "enabled", True):
            desired["tautulli_url"] = self._resolve_url(
                tautulli_section,
                self._text(
                    (cfg.get("tautulli") or {}).get("url") or "http://tautulli:8181"
                ),
            )
            desired["tautulli_api_key"] = self._resolve_tautulli_key(
                config_root=config_root,
                section_cfg=tautulli_section,
            )

        watched_fields = [
            "applicationUrl",
            "media_server_type",
            "seerr_url",
            "jellyfin_url",
            "jellyfin_api_key",
            "jellyfin_user_id",
            "jellyfin_server_name",
            "seerr_api_key",
            "tautulli_url",
            "tautulli_api_key",
        ]
        needs_update = any(self._text(current.get(field)) != self._text(desired.get(field)) for field in watched_fields)
        if not needs_update:
            self.log("[OK] Maintainerr: main settings already configured")
            return

        status, _, body = self._request(
            maintainerr_url,
            "/api/settings",
            method="POST",
            payload=desired,
        )
        if status < 200 or status >= 300:
            raise RuntimeError(
                f"Maintainerr: failed saving main settings (HTTP {status}): {body}"
            )
        self.log("[OK] Maintainerr: configured main settings")

    def ensure_integrations(
        self,
        cfg: dict[str, Any],
        config_root: str,
        arr_apps: list[dict[str, Any]],
        wait_timeout: int,
    ) -> None:
        maintainerr_cfg = cfg.get("maintainerr") or {}
        if not self.bool_cfg(maintainerr_cfg, "enabled", False):
            return

        integrations_cfg = maintainerr_cfg.get("integrations") or {}
        if not isinstance(integrations_cfg, dict):
            raise RuntimeError("Maintainerr: maintainerr.integrations must be an object.")
        if not self._ensure_enabled(integrations_cfg, "enabled", True):
            return

        maintainerr_url = self._resolve_url(
            integrations_cfg,
            self._text(maintainerr_cfg.get("url") or "http://maintainerr:6246"),
        )
        self.wait_for_service("Maintainerr", maintainerr_url, "/api/settings", wait_timeout)
        test_connections = self._ensure_enabled(integrations_cfg, "test_connections", True)

        self._ensure_main_settings(
            maintainerr_url,
            cfg=cfg,
            maintainerr_cfg=maintainerr_cfg,
            integrations_cfg=integrations_cfg,
            config_root=config_root,
            wait_timeout=wait_timeout,
        )

        radarr_section = self._service_section(integrations_cfg, "radarr")
        if self._ensure_enabled(radarr_section, "enabled", True):
            radarr_app = self.get_arr_app(arr_apps, "radarr")
            radarr_url = self._resolve_url(
                radarr_section,
                self._text((radarr_app or {}).get("url") or "http://radarr:7878"),
            )
            radarr_payload = {
                "serverName": self._text(
                    radarr_section.get("server_name")
                    or (radarr_app or {}).get("name")
                    or "Radarr"
                ),
                "url": radarr_url,
                "apiKey": self._resolve_servarr_key(
                    config_root=config_root,
                    app_name="radarr",
                    section_cfg=radarr_section,
                    default_env="RADARR_API_KEY",
                ),
            }
            self._ensure_servarr_integration(
                maintainerr_url,
                "radarr",
                radarr_payload,
                test_connections=test_connections,
            )

        sonarr_section = self._service_section(integrations_cfg, "sonarr")
        if self._ensure_enabled(sonarr_section, "enabled", True):
            sonarr_app = self.get_arr_app(arr_apps, "sonarr")
            sonarr_url = self._resolve_url(
                sonarr_section,
                self._text((sonarr_app or {}).get("url") or "http://sonarr:8989"),
            )
            sonarr_payload = {
                "serverName": self._text(
                    sonarr_section.get("server_name")
                    or (sonarr_app or {}).get("name")
                    or "Sonarr"
                ),
                "url": sonarr_url,
                "apiKey": self._resolve_servarr_key(
                    config_root=config_root,
                    app_name="sonarr",
                    section_cfg=sonarr_section,
                    default_env="SONARR_API_KEY",
                ),
            }
            self._ensure_servarr_integration(
                maintainerr_url,
                "sonarr",
                sonarr_payload,
                test_connections=test_connections,
            )

        jellyseerr_section = self._service_section(integrations_cfg, "jellyseerr")
        if self._ensure_enabled(jellyseerr_section, "enabled", True):
            jellyseerr_cfg = cfg.get("jellyseerr") or {}
            jellyseerr_payload = {
                "url": self._resolve_url(
                    jellyseerr_section,
                    self._text(jellyseerr_cfg.get("url") or "http://jellyseerr:5055"),
                ),
                "api_key": self._resolve_jellyseerr_key(
                    config_root=config_root,
                    wait_timeout=wait_timeout,
                    section_cfg=jellyseerr_section,
                ),
            }
            self._ensure_single_endpoint_integration(
                maintainerr_url,
                "seerr",
                jellyseerr_payload,
                test_connections=test_connections,
            )

        tautulli_section = self._service_section(integrations_cfg, "tautulli")
        if self._ensure_enabled(tautulli_section, "enabled", True):
            tautulli_payload = {
                "url": self._resolve_url(
                    tautulli_section,
                    self._text(
                        (cfg.get("tautulli") or {}).get("url") or "http://tautulli:8181"
                    ),
                ),
                "api_key": self._resolve_tautulli_key(
                    config_root=config_root,
                    section_cfg=tautulli_section,
                ),
            }
            self._ensure_single_endpoint_integration(
                maintainerr_url,
                "tautulli",
                tautulli_payload,
                test_connections=test_connections,
            )

        if self._ensure_enabled(integrations_cfg, "sync_rules", True):
            self._sync_policy_rules(
                maintainerr_url=maintainerr_url,
                maintainerr_cfg=maintainerr_cfg,
                config_root=config_root,
            )

        self.log("[OK] Maintainerr: integration reconcile complete")
