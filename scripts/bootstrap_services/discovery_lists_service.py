"""Arr discovery-list bootstrap service logic."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .config_models import ArrDiscoveryListsConfig

BoolCfgFn = Callable[[dict[str, Any], str, bool], bool]
CoerceListFn = Callable[[Any], list[Any]]
LogFn = Callable[[str], None]
HttpRequestFn = Callable[..., tuple[int, Any, str]]
ResolveEnvPlaceholderFn = Callable[[Any], Any]
FieldMapFn = Callable[[Any], dict[str, Any]]
FieldListFn = Callable[[dict[str, Any]], list[dict[str, Any]]]
ToIntFn = Callable[[Any, Any], Any]
NormalizeTokenFn = Callable[[Any], str]
ResolveQualityPrefFn = Callable[[dict[str, Any], dict[str, Any]], tuple[int | None, list[str]]]
GetArrQualityProfileFn = Callable[..., dict[str, Any]]
PickFirstProfileIdFn = Callable[..., int | None]
EnvTruthyFn = Callable[[str, bool], bool]
TriggerArrCommandFn = Callable[..., bool]


@dataclass
class DiscoveryListsService:
    bool_cfg: BoolCfgFn
    coerce_list: CoerceListFn
    log: LogFn
    http_request: HttpRequestFn
    resolve_env_placeholder: ResolveEnvPlaceholderFn
    field_map: FieldMapFn
    field_list: FieldListFn
    to_int: ToIntFn
    normalize_token: NormalizeTokenFn
    resolve_arr_quality_preferences: ResolveQualityPrefFn
    get_arr_quality_profile: GetArrQualityProfileFn
    pick_first_profile_id: PickFirstProfileIdFn
    env_truthy: EnvTruthyFn
    trigger_arr_command: TriggerArrCommandFn

    @staticmethod
    def _coerce_for_example(value: Any, example: Any) -> Any:
        if isinstance(example, bool):
            if isinstance(value, str):
                return value.strip().lower() in ("1", "true", "yes", "on")
            return bool(value)
        if isinstance(example, int) and not isinstance(example, bool):
            try:
                if value is None:
                    return value
                return int(value)
            except Exception:
                return value
        return value

    def resolve_import_list_definitions(
        self,
        arr_discovery_cfg: ArrDiscoveryListsConfig | dict[str, Any],
        app_cfg: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if isinstance(arr_discovery_cfg, ArrDiscoveryListsConfig):
            model = arr_discovery_cfg
        else:
            model = ArrDiscoveryListsConfig.from_dict(arr_discovery_cfg)
        app_impl = str(app_cfg.get("implementation") or "")
        return self.coerce_list(
            model.by_app.get(app_impl) or model.by_app.get(app_impl.lower()) or []
        )

    def build_arr_import_list_payload(
        self,
        app_cfg: dict[str, Any],
        schema: dict[str, Any],
        list_cfg: dict[str, Any],
        default_quality_profile_id: int | None,
        default_metadata_profile_id: int | None = None,
    ) -> dict[str, Any]:
        name = str(list_cfg.get("name") or schema.get("implementationName") or "").strip()
        if not name:
            raise RuntimeError(
                f"{app_cfg.get('name', app_cfg.get('implementation', 'Arr'))}: "
                "import list entry missing name."
            )

        values = self.field_map(schema.get("fields"))
        allow_unknown_overrides = bool(list_cfg.get("allow_unknown_field_overrides", False))
        for field_name, field_value in (list_cfg.get("field_overrides") or {}).items():
            resolved_value = self.resolve_env_placeholder(field_value)
            if field_name in values:
                values[field_name] = self._coerce_for_example(
                    resolved_value, values.get(field_name)
                )
            elif allow_unknown_overrides:
                values[field_name] = resolved_value

        payload = {
            "name": name,
            "implementation": schema.get("implementation"),
            "configContract": schema.get("configContract"),
            "fields": self.field_list(values),
        }

        for key in (
            "enabled",
            "enableAuto",
            "monitor",
            "qualityProfileId",
            "metadataProfileId",
            "searchOnAdd",
            "minimumAvailability",
            "listType",
            "listOrder",
            "minRefreshInterval",
            "enableAutomaticAdd",
            "searchForMissingEpisodes",
            "shouldMonitor",
            "monitorNewItems",
            "seriesType",
            "seasonFolder",
            "shouldSearch",
        ):
            if key in schema:
                payload[key] = schema.get(key)

        cfg_key_map = {
            "enabled": "enabled",
            "enable_auto": "enableAuto",
            "monitor": "monitor",
            "quality_profile_id": "qualityProfileId",
            "metadata_profile_id": "metadataProfileId",
            "search_on_add": "searchOnAdd",
            "minimum_availability": "minimumAvailability",
            "list_type": "listType",
            "list_order": "listOrder",
            "min_refresh_interval": "minRefreshInterval",
            "enable_automatic_add": "enableAutomaticAdd",
            "search_for_missing_episodes": "searchForMissingEpisodes",
            "should_monitor": "shouldMonitor",
            "monitor_new_items": "monitorNewItems",
            "series_type": "seriesType",
            "season_folder": "seasonFolder",
            "should_search": "shouldSearch",
        }
        for src_key, dst_key in cfg_key_map.items():
            if src_key not in list_cfg:
                continue
            value = self.resolve_env_placeholder(list_cfg.get(src_key))
            if dst_key in payload:
                payload[dst_key] = self._coerce_for_example(value, payload.get(dst_key))
            else:
                payload[dst_key] = value

        # Backward-compatibility across Arr variants:
        # some schemas (Lidarr/Readarr) use shouldMonitor/shouldSearch/enableAutomaticAdd,
        # while others (older Sonarr/Radarr-style) use monitor/searchOnAdd/enableAuto.
        def apply_alias(src_key: str, dst_keys: tuple[str, ...]) -> None:
            if src_key not in list_cfg:
                return
            value = self.resolve_env_placeholder(list_cfg.get(src_key))
            for dst_key in dst_keys:
                if dst_key in payload:
                    payload[dst_key] = self._coerce_for_example(value, payload.get(dst_key))

        apply_alias("enable_auto", ("enableAutomaticAdd", "enableAuto"))
        apply_alias("enable_automatic_add", ("enableAutomaticAdd", "enableAuto"))
        apply_alias("monitor", ("shouldMonitor", "monitor"))
        apply_alias("should_monitor", ("shouldMonitor", "monitor"))
        apply_alias("search_on_add", ("shouldSearch", "searchOnAdd"))
        apply_alias("should_search", ("shouldSearch", "searchOnAdd"))

        quality_profile_id = self.to_int(payload.get("qualityProfileId"))
        if (quality_profile_id is None or quality_profile_id <= 0) and default_quality_profile_id:
            payload["qualityProfileId"] = int(default_quality_profile_id)

        metadata_profile_id = self.to_int(payload.get("metadataProfileId"))
        if (
            metadata_profile_id is None or metadata_profile_id <= 0
        ) and default_metadata_profile_id:
            payload["metadataProfileId"] = int(default_metadata_profile_id)

        app_impl = str(app_cfg.get("implementation") or "").strip().lower()
        # Readarr/Lidarr use different enums for shouldMonitor than Sonarr-style "all/none".
        monitor_value = str(payload.get("shouldMonitor") or "").strip().lower()
        if monitor_value == "all":
            if app_impl == "readarr":
                payload["shouldMonitor"] = "entireAuthor"
            elif app_impl == "lidarr":
                payload["shouldMonitor"] = "entireArtist"

        root_folder_path = (
            str(list_cfg.get("root_folder_path") or "").strip()
            or str(app_cfg.get("root_folder") or "").strip()
        )
        if root_folder_path:
            payload["rootFolderPath"] = root_folder_path

        return payload

    def ensure_arr_discovery_lists_for_app(
        self,
        cfg: dict[str, Any],
        app_cfg: dict[str, Any],
        app_url: str,
        api_base: str,
        api_key: str,
    ) -> None:
        arr_discovery_cfg = ArrDiscoveryListsConfig.from_dict(cfg.get("arr_discovery_lists") or {})
        if not arr_discovery_cfg.enabled:
            return

        app_name = str(app_cfg.get("name") or app_cfg.get("implementation") or "Arr")
        list_defs = self.resolve_import_list_definitions(arr_discovery_cfg, app_cfg)
        if not list_defs:
            return
        prune_unmanaged = arr_discovery_cfg.prune_unmanaged

        status, schemas, body = self.http_request(
            app_url, f"{api_base}/importlist/schema", api_key=api_key
        )
        if status != 200 or not isinstance(schemas, list):
            raise RuntimeError(
                f"{app_name}: failed reading import list schema (HTTP {status}): {body}"
            )
        schemas_by_impl = {
            str(item.get("implementation") or "").strip().lower(): item
            for item in schemas
            if isinstance(item, dict) and str(item.get("implementation") or "").strip()
        }

        status, existing_lists, body = self.http_request(
            app_url, f"{api_base}/importlist", api_key=api_key
        )
        if status != 200 or not isinstance(existing_lists, list):
            raise RuntimeError(f"{app_name}: failed listing import lists (HTTP {status}): {body}")

        preferred_id, preferred_names = self.resolve_arr_quality_preferences(cfg, app_cfg)
        selected_profile = self.get_arr_quality_profile(
            app_name,
            app_url,
            api_base,
            api_key,
            preferred_id=preferred_id,
            preferred_names=preferred_names,
        )
        selected_profile_id = self.to_int(selected_profile.get("id"))
        selected_profile_name = str(selected_profile.get("name") or "")
        self.log(
            f"[OK] {app_name}: using quality profile '{selected_profile_name}' "
            f"(id={selected_profile_id}) for discovery lists"
        )

        app_impl = str(app_cfg.get("implementation") or "")
        selected_metadata_profile_id = None
        if app_impl in ("Lidarr", "Readarr"):
            for metadata_endpoint in ("metadataprofile", "metadataProfile"):
                try:
                    selected_metadata_profile_id = self.pick_first_profile_id(
                        app_name,
                        app_url,
                        api_base,
                        api_key,
                        metadata_endpoint,
                        "metadata profiles",
                    )
                    break
                except Exception:
                    continue
            if selected_metadata_profile_id:
                self.log(
                    f"[OK] {app_name}: using metadata profile id "
                    f"{selected_metadata_profile_id} for discovery lists"
                )
            else:
                self.log(
                    f"[WARN] {app_name}: could not resolve metadata profile id; "
                    "list creation may fail if this Arr requires metadataProfileId."
                )

        created = 0
        updated = 0
        deleted = 0
        skipped = 0
        desired_keys = set()
        managed_implementations = {
            str(item.get("implementation") or "").strip().lower()
            for item in list_defs
            if isinstance(item, dict) and str(item.get("implementation") or "").strip()
        }

        for list_cfg in list_defs:
            if not isinstance(list_cfg, dict):
                continue

            impl_raw = str(list_cfg.get("implementation") or "").strip()
            if not impl_raw:
                self.log(f"[WARN] {app_name}: skipping import list entry without implementation")
                skipped += 1
                continue
            schema = schemas_by_impl.get(impl_raw.lower())
            if not schema:
                msg = f"{app_name}: import list implementation '{impl_raw}' is not supported by this Arr build."
                if self.bool_cfg(list_cfg, "required", False):
                    raise RuntimeError(msg)
                self.log(f"[WARN] {msg}")
                skipped += 1
                continue

            schema_fields = {str(f.get("name") or "") for f in (schema.get("fields") or [])}
            list_name = str(
                list_cfg.get("name") or schema.get("implementationName") or impl_raw
            ).strip()

            # Some providers (for example Trakt popular imports in Sonarr) require OAuth.
            if "signIn" in schema_fields:
                access_token = str(
                    self.resolve_env_placeholder(
                        ((list_cfg.get("field_overrides") or {}).get("accessToken", ""))
                    )
                ).strip()
                if not access_token and self.bool_cfg(list_cfg, "skip_if_auth_required", True):
                    self.log(
                        f"[WARN] {app_name}: skipping import list '{list_name}' "
                        f"({impl_raw}) because provider auth is required "
                        "(set field_overrides.accessToken/refreshToken to enable)."
                    )
                    skipped += 1
                    continue

            payload = self.build_arr_import_list_payload(
                app_cfg,
                schema,
                list_cfg,
                selected_profile_id,
                selected_metadata_profile_id,
            )
            desired_keys.add(
                (
                    str(payload.get("implementation") or "").strip().lower(),
                    str(payload.get("name") or "").strip().lower(),
                )
            )

            existing = None
            for item in existing_lists:
                if not isinstance(item, dict):
                    continue
                if (
                    str(item.get("implementation") or "").strip().lower()
                    == str(payload.get("implementation") or "").strip().lower()
                    and str(item.get("name") or "").strip().lower()
                    == str(payload.get("name") or "").strip().lower()
                ):
                    existing = item
                    break

            if existing and existing.get("id") is not None:
                payload["id"] = existing.get("id")
                status, _, body = self.http_request(
                    app_url,
                    f"{api_base}/importlist/{existing.get('id')}",
                    api_key=api_key,
                    method="PUT",
                    payload=payload,
                )
                if status in (200, 201, 202):
                    updated += 1
                    self.log(f"[OK] {app_name}: updated discovery list '{payload['name']}'")
                    continue
                msg = (
                    f"{app_name}: failed updating discovery list '{payload['name']}' "
                    f"(HTTP {status}): {body}"
                )
                if self.bool_cfg(list_cfg, "required", False):
                    raise RuntimeError(msg)
                self.log(f"[WARN] {msg}")
                skipped += 1
                continue

            status, _, body = self.http_request(
                app_url,
                f"{api_base}/importlist",
                api_key=api_key,
                method="POST",
                payload=payload,
            )
            if status in (200, 201, 202):
                created += 1
                self.log(f"[OK] {app_name}: created discovery list '{payload['name']}'")
                continue

            msg = (
                f"{app_name}: failed creating discovery list '{payload['name']}' "
                f"(HTTP {status}): {body}"
            )
            if self.bool_cfg(list_cfg, "required", False):
                raise RuntimeError(msg)
            self.log(f"[WARN] {msg}")
            skipped += 1

        if prune_unmanaged and managed_implementations:
            status, existing_lists, body = self.http_request(
                app_url, f"{api_base}/importlist", api_key=api_key
            )
            if status != 200 or not isinstance(existing_lists, list):
                raise RuntimeError(
                    f"{app_name}: failed listing import lists for prune (HTTP {status}): {body}"
                )
            for item in existing_lists:
                if not isinstance(item, dict):
                    continue
                item_id = item.get("id")
                impl = str(item.get("implementation") or "").strip().lower()
                name = str(item.get("name") or "").strip().lower()
                key = (impl, name)
                if item_id is None or impl not in managed_implementations or key in desired_keys:
                    continue
                status, _, body = self.http_request(
                    app_url,
                    f"{api_base}/importlist/{item_id}",
                    api_key=api_key,
                    method="DELETE",
                )
                if status in (200, 202, 204):
                    deleted += 1
                    self.log(
                        f"[OK] {app_name}: pruned unmanaged discovery list "
                        f"'{item.get('name', item_id)}'"
                    )
                    continue
                self.log(
                    f"[WARN] {app_name}: failed pruning unmanaged discovery list "
                    f"'{item.get('name', item_id)}' (HTTP {status}): {body}"
                )

        self.log(
            f"[OK] {app_name}: discovery list reconcile complete "
            f"(created={created}, updated={updated}, deleted={deleted}, skipped={skipped})"
        )

    def trigger_arr_discovery_kickoff(
        self,
        cfg: dict[str, Any],
        app_cfg: dict[str, Any],
        app_url: str,
        api_base: str,
        api_key: str,
    ) -> None:
        arr_discovery_cfg = ArrDiscoveryListsConfig.from_dict(cfg.get("arr_discovery_lists") or {})
        if not arr_discovery_cfg.trigger_initial_sync:
            return

        impl = str(app_cfg.get("implementation") or "").strip()
        app_name = str(app_cfg.get("name") or impl or "Arr")
        commands: list[str] = []
        if impl == "Lidarr":
            commands = ["MissingAlbumSearch", "RssSync"]
        elif impl == "Readarr":
            commands = ["MissingBookSearch", "RssSync"]
        else:
            return

        # Import list sync can be expensive/rate-limited (especially Readarr metadata providers).
        # Force it only on first-run (empty library) unless explicitly overridden.
        force_import_sync = self.env_truthy("ARR_FORCE_IMPORTLIST_SYNC", False)
        if force_import_sync:
            commands.insert(0, "ImportListSync")
        else:
            seed_endpoint = None
            if impl == "Lidarr":
                seed_endpoint = f"{api_base}/artist"
            elif impl == "Readarr":
                seed_endpoint = f"{api_base}/author"
            should_seed = True
            if seed_endpoint:
                status, existing, _ = self.http_request(app_url, seed_endpoint, api_key=api_key)
                if status == 200 and isinstance(existing, list) and len(existing) > 0:
                    should_seed = False
            if should_seed:
                commands.insert(0, "ImportListSync")
            else:
                self.log(
                    f"[OK] {app_name}: skipping ImportListSync during bootstrap "
                    "(library already has managed entries)"
                )

        for command_name in commands:
            self.trigger_arr_command(
                app_name,
                app_url,
                api_base,
                api_key,
                command_name,
            )
