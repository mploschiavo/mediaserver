"""Maintainerr policy-rule translation helpers."""

from __future__ import annotations


from media_stack.core.logging_utils import log_swallowed
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
import logging

LogFn = Callable[[str], None]
RequestFn = Callable[..., tuple[int, Any, str]]
ResolvePathFn = Callable[[str, str], Any]


@dataclass
class MaintainerrRuleTranslationDependencies:
    log: LogFn
    request: RequestFn
    resolve_path: ResolvePathFn


@dataclass
class MaintainerrRuleTranslationService:
    deps: MaintainerrRuleTranslationDependencies

    @staticmethod
    def _text(value: Any) -> str:
        return str(value or "").strip()

    @staticmethod
    def _token(value: Any) -> str:
        return str(value or "").strip().lower()

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
        status, data, body = self.deps.request(maintainerr_url, "/api/media-server/libraries")
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

    def _normalize_media_type(self, value: Any) -> str:
        token = self._token(value)
        if token in {"movie", "movies"}:
            return "movie"
        if token in {"show", "shows", "series", "tv", "tvshows", "tv_show", "tv-shows"}:
            return "show"
        return ""

    @staticmethod
    def _looks_like_yaml_rule_sections(value: Any) -> bool:
        if not isinstance(value, list) or not value:
            return False
        first = value[0]
        if not isinstance(first, dict):
            return False
        for key, entries in first.items():
            if str(key).isdigit() and isinstance(entries, list):
                return True
        return False

    def _decode_yaml_rule_payload(
        self,
        *,
        maintainerr_url: str,
        rule: dict[str, Any],
        base_name: str,
    ) -> dict[str, Any]:
        yaml_blob = self._text(rule.get("yaml"))
        decoded_source = dict(rule)
        media_type = self._normalize_media_type(rule.get("dataType") or rule.get("mediaType"))

        if yaml_blob:
            if not media_type:
                media_match = re.search(
                    r"^\s*mediaType\s*:\s*([A-Za-z_ -]+)\s*$",
                    yaml_blob,
                    flags=re.IGNORECASE | re.MULTILINE,
                )
                if media_match:
                    media_type = self._normalize_media_type(media_match.group(1))
        elif self._looks_like_yaml_rule_sections(rule.get("rules")):
            media_token = self._text(rule.get("mediaType")) or "MOVIES"
            yaml_blob = json.dumps(
                {"mediaType": media_token, "rules": rule.get("rules") or []},
                ensure_ascii=True,
                separators=(",", ":"),
            )
        else:
            return rule

        if not media_type:
            media_type = "movie"
            self.deps.log(
                f"[WARN] Maintainerr: YAML rule '{base_name}' did not declare media type; "
                "defaulting to movie."
            )

        status, data, body = self.deps.request(
            maintainerr_url,
            "/api/rules/yaml/decode",
            method="POST",
            payload={"yaml": yaml_blob, "mediaType": media_type},
        )
        if status < 200 or status >= 300:
            raise RuntimeError(
                f"Maintainerr: failed decoding YAML rule '{base_name}' (HTTP {status}): {body}"
            )
        if not isinstance(data, dict) or int(data.get("code") or 0) != 1:
            raise RuntimeError(
                f"Maintainerr: YAML decode failed for '{base_name}': "
                f"{data.get('message') if isinstance(data, dict) else body}"
            )
        result_raw = data.get("result")
        if not isinstance(result_raw, str) or not result_raw.strip():
            raise RuntimeError(f"Maintainerr: YAML decode returned empty result for '{base_name}'.")
        try:
            decoded = json.loads(result_raw)
        except Exception as exc:
            raise RuntimeError(
                f"Maintainerr: YAML decode returned invalid JSON for '{base_name}': {exc}"
            ) from exc
        if not isinstance(decoded, dict):
            raise RuntimeError(
                f"Maintainerr: YAML decode result is not an object for '{base_name}'."
            )

        decoded_rules = decoded.get("rules")
        if not isinstance(decoded_rules, list):
            raise RuntimeError(
                f"Maintainerr: YAML decode result missing rules list for '{base_name}'."
            )

        decoded_media_type = self._normalize_media_type(decoded.get("mediaType")) or media_type
        decoded_source["rules"] = decoded_rules
        decoded_source["dataType"] = decoded_media_type
        decoded_source.pop("yaml", None)
        return decoded_source

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
            matches = [
                lib for lib in libraries if self._text(lib.get("id")) == requested_library_id
            ]
            if not matches:
                self.deps.log(
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
            requested_media_type = self._normalize_media_type(
                rule.get("dataType") or rule.get("mediaType")
            )
            if requested_media_type:
                typed = [
                    lib for lib in libraries if self._token(lib.get("type")) == requested_media_type
                ]
                if typed:
                    return typed
            return libraries

        target_libraries: list[dict[str, str]] = []
        missing: list[str] = []
        for lib_name in requested_libraries:
            lib = by_title.get(lib_name)
            if lib is None:
                # Don't WARN per missing library — the rules ship with
                # music/books/movies/tv defaults and most home stacks
                # have only movies+tv. The previous behaviour produced
                # 8-12 scary [WARN] lines per fresh install for what
                # is normal config drift between defaults and reality.
                missing.append(lib_name)
                continue
            target_libraries.append(lib)
        if missing and not target_libraries:
            self.deps.log(
                f"[INFO] Maintainerr: rule '{rule_name}' skipped — "
                f"target libraries not present ({', '.join(missing)})."
            )
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
                    except Exception as exc:
                        log_swallowed(exc)
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
            self.deps.log(
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
                    overrides=(
                        rule.get("collection") if isinstance(rule.get("collection"), dict) else None
                    ),
                ),
            }
            if cron_schedule:
                payload["ruleHandlerCronSchedule"] = cron_schedule
            if tautulli_override is not None:
                payload["tautulliWatchedPercentOverride"] = tautulli_override
            # *arr server linking is now applied PER-PAYLOAD by the
            # caller via ``_link_arr_settings``. Doing it here would
            # set BOTH IDs on every rule regardless of library type,
            # which breaks Maintainerr's "select your *arr" UI.
            payloads.append(payload)
        return payloads

    def _link_arr_settings(
        self,
        payload: dict[str, Any],
        radarr_id: int | None,
        sonarr_id: int | None,
        *,
        explicit_radarr: int | None = None,
        explicit_sonarr: int | None = None,
    ) -> None:
        """Set radarrSettingsId/sonarrSettingsId on a rule payload
        based on its dataType. Movies → Radarr only; TV Shows →
        Sonarr only. Explicit values from the rule definition win
        over the auto-resolved IDs. Other dataTypes (music, books)
        get neither — Maintainerr only links rules to *arr that
        manage that media type."""
        data_type = str(payload.get("dataType") or "").strip().lower()
        if data_type in ("movie", "movies"):
            chosen = explicit_radarr if explicit_radarr is not None else radarr_id
            if chosen is not None:
                payload["radarrSettingsId"] = chosen
        elif data_type in ("show", "shows", "tv", "episode", "season"):
            chosen = explicit_sonarr if explicit_sonarr is not None else sonarr_id
            if chosen is not None:
                payload["sonarrSettingsId"] = chosen

    def _resolve_arr_settings_ids(self, maintainerr_url: str) -> tuple[int | None, int | None]:
        """Look up the first configured Radarr/Sonarr server IDs from
        Maintainerr's settings API. The rule payloads need these to
        link a rule to its *arr — without them, Maintainerr's UI shows
        "Radarr server: None" and rules can't delete from the *arr,
        only from the Jellyfin library. (v1.0.146.)"""
        radarr_id = sonarr_id = None
        try:
            status, data, _ = self.deps.request(maintainerr_url, "/api/settings/radarr")
            if status == 200 and isinstance(data, list) and data:
                radarr_id = data[0].get("id")
        except Exception as exc:
            self.deps.log(f"[WARN] Maintainerr: radarr settings lookup failed: {exc}")
        try:
            status, data, _ = self.deps.request(maintainerr_url, "/api/settings/sonarr")
            if status == 200 and isinstance(data, list) and data:
                sonarr_id = data[0].get("id")
        except Exception as exc:
            self.deps.log(f"[WARN] Maintainerr: sonarr settings lookup failed: {exc}")
        return radarr_id, sonarr_id

    def _desired_rule_payloads(
        self,
        *,
        maintainerr_url: str,
        policy_rules: list[dict[str, Any]],
        libraries: list[dict[str, str]],
    ) -> list[dict[str, Any]]:
        by_title = {self._token(lib["title"]): lib for lib in libraries}
        desired: list[dict[str, Any]] = []
        radarr_settings_id, sonarr_settings_id = self._resolve_arr_settings_ids(maintainerr_url)

        for rule in policy_rules:
            if not isinstance(rule, dict):
                continue
            base_name = self._text(rule.get("name"))
            if not base_name:
                continue
            resolved_rule = self._decode_yaml_rule_payload(
                maintainerr_url=maintainerr_url,
                rule=rule,
                base_name=base_name,
            )
            description = self._text(rule.get("description")) or "Seeded by bootstrap policy"
            target_libraries = self._resolve_target_libraries(
                rule=resolved_rule,
                libraries=libraries,
                by_title=by_title,
                rule_name=base_name,
            )
            if not target_libraries:
                # Already INFO-logged in _resolve_target_libraries when
                # the missing-library list is non-empty; suppress the
                # follow-up [WARN] which produced duplicate noise.
                continue

            if isinstance(resolved_rule.get("rules"), list):
                native_payloads = self._native_rule_payloads(
                    rule=resolved_rule,
                    base_name=base_name,
                    description=description,
                    target_libraries=target_libraries,
                )
                # Library-type-aware *arr linking: a Movies-library
                # rule should ONLY have radarrSettingsId; a TV Shows-
                # library rule should ONLY have sonarrSettingsId.
                # Setting both — or the wrong one — leaves the UI's
                # required dropdown blank. (v1.0.146.)
                for p in native_payloads:
                    self._link_arr_settings(
                        p, radarr_settings_id, sonarr_settings_id,
                        explicit_radarr=resolved_rule.get("radarrSettingsId"),
                        explicit_sonarr=resolved_rule.get("sonarrSettingsId"),
                    )
                desired.extend(native_payloads)
                continue

            conditions = resolved_rule.get("conditions") or {}
            actions = resolved_rule.get("actions") or {}
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
                    # Real WARN: the rule's declared conditions
                    # couldn't be translated, so we substituted a
                    # generic watched-content rule. Operator should
                    # re-author the rule with conditions Maintainerr
                    # can interpret natively.
                    self.deps.log(
                        f"[WARN] Maintainerr: rule '{rule_name}' had no translatable conditions; "
                        "using watched-content fallback condition."
                    )
                    rules = [self._fallback_condition(data_type)]

                payload = {
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
                self._link_arr_settings(
                    payload, radarr_settings_id, sonarr_settings_id,
                )
                desired.append(payload)

        return desired
