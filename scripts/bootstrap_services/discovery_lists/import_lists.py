"""Arr discovery list reconciliation operations."""

from __future__ import annotations

from typing import Any

from ..config_models import ArrDiscoveryListEntry, ArrDiscoveryListsConfig
from ..config_models_discovery import (
    GenericDiscoveryProviderOptions,
    GoodreadsListImportOptions,
    LastFmTagOptions,
    TmdbPopularImportOptions,
    TraktPopularImportOptions,
)
from .common import coerce_for_example
from .sonarr_seed import ensure_sonarr_seed_series


def resolve_import_list_definitions(
    service,
    arr_discovery_cfg: ArrDiscoveryListsConfig | dict[str, Any],
    app_cfg: dict[str, Any],
) -> list[ArrDiscoveryListEntry]:
    if isinstance(arr_discovery_cfg, ArrDiscoveryListsConfig):
        model = arr_discovery_cfg
    else:
        model = ArrDiscoveryListsConfig.from_dict(arr_discovery_cfg)
    app_impl = str(app_cfg.get("implementation") or "")
    typed = model.typed_by_app.get(app_impl) or model.typed_by_app.get(app_impl.lower()) or []
    if typed:
        return list(typed)
    fallback = service.coerce_list(
        model.by_app.get(app_impl) or model.by_app.get(app_impl.lower()) or []
    )
    return [ArrDiscoveryListEntry.from_dict(item) for item in fallback if isinstance(item, dict)]


def build_arr_import_list_payload(
    service,
    app_cfg: dict[str, Any],
    schema: dict[str, Any],
    list_cfg: ArrDiscoveryListEntry | dict[str, Any],
    default_quality_profile_id: int | None,
    default_metadata_profile_id: int | None = None,
) -> dict[str, Any]:
    entry = (
        list_cfg
        if isinstance(list_cfg, ArrDiscoveryListEntry)
        else ArrDiscoveryListEntry.from_dict(list_cfg)
    )
    name = str(entry.name or schema.get("implementationName") or "").strip()
    if not name:
        raise RuntimeError(
            f"{app_cfg.get('name', app_cfg.get('implementation', 'Arr'))}: "
            "import list entry missing name."
        )

    values = service.field_map(schema.get("fields"))
    provider_option_overrides: dict[str, Any] = {}
    provider_opts = entry.provider_options
    if isinstance(provider_opts, TmdbPopularImportOptions):
        if provider_opts.tmdb_list_type is not None:
            provider_option_overrides["tMDbListType"] = provider_opts.tmdb_list_type
    elif isinstance(provider_opts, LastFmTagOptions):
        if provider_opts.tag_id:
            provider_option_overrides["tagId"] = provider_opts.tag_id
        if provider_opts.count is not None:
            provider_option_overrides["count"] = provider_opts.count
    elif isinstance(provider_opts, GoodreadsListImportOptions):
        if provider_opts.list_id:
            provider_option_overrides["listId"] = provider_opts.list_id
    elif isinstance(provider_opts, TraktPopularImportOptions):
        if provider_opts.list_type:
            provider_option_overrides["listType"] = provider_opts.list_type
        if provider_opts.access_token:
            provider_option_overrides["accessToken"] = provider_opts.access_token
        if provider_opts.refresh_token:
            provider_option_overrides["refreshToken"] = provider_opts.refresh_token
    elif isinstance(provider_opts, GenericDiscoveryProviderOptions):
        provider_option_overrides.update(dict(provider_opts.values or {}))

    effective_field_overrides = dict(provider_option_overrides)
    effective_field_overrides.update(dict(entry.field_overrides or {}))

    allow_unknown_overrides = bool(entry.allow_unknown_field_overrides)
    for field_name, field_value in effective_field_overrides.items():
        resolved_value = service.resolve_env_placeholder(field_value)
        if field_name in values:
            values[field_name] = coerce_for_example(resolved_value, values.get(field_name))
        elif allow_unknown_overrides:
            values[field_name] = resolved_value

    payload = {
        "name": name,
        "implementation": schema.get("implementation"),
        "configContract": schema.get("configContract"),
        "fields": service.field_list(values),
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

    scalar_overrides = entry.resolved_payload_overrides(service.resolve_env_placeholder)
    for dst_key, value in scalar_overrides.items():
        if dst_key in payload:
            payload[dst_key] = coerce_for_example(value, payload.get(dst_key))
        else:
            payload[dst_key] = value

    quality_profile_id = service.to_int(payload.get("qualityProfileId"))
    if (quality_profile_id is None or quality_profile_id <= 0) and default_quality_profile_id:
        payload["qualityProfileId"] = int(default_quality_profile_id)

    metadata_profile_id = service.to_int(payload.get("metadataProfileId"))
    if (metadata_profile_id is None or metadata_profile_id <= 0) and default_metadata_profile_id:
        payload["metadataProfileId"] = int(default_metadata_profile_id)

    app_impl = str(app_cfg.get("implementation") or "").strip().lower()
    monitor_value = str(payload.get("shouldMonitor") or "").strip().lower()
    if monitor_value == "all":
        if app_impl == "readarr":
            payload["shouldMonitor"] = "entireAuthor"
        elif app_impl == "lidarr":
            payload["shouldMonitor"] = "entireArtist"

    root_folder_path = str(payload.get("rootFolderPath") or "").strip()
    if not root_folder_path:
        root_folder_path = str(app_cfg.get("root_folder") or "").strip()
    if root_folder_path:
        payload["rootFolderPath"] = root_folder_path

    return payload


def ensure_arr_discovery_lists_for_app(
    service,
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
    list_defs = resolve_import_list_definitions(service, arr_discovery_cfg, app_cfg)
    app_impl = str(app_cfg.get("implementation") or "").strip().lower()
    has_seed_cfg = (
        app_impl == "sonarr"
        and isinstance(cfg.get("sonarr_seed_series"), dict)
        and service.bool_cfg(cfg.get("sonarr_seed_series") or {}, "enabled", True)
    )
    if not list_defs and not has_seed_cfg:
        return
    prune_unmanaged = arr_discovery_cfg.prune_unmanaged

    schemas_by_impl: dict[str, dict[str, Any]] = {}
    existing_lists: list[dict[str, Any]] = []
    if list_defs:
        status, schemas, body = service.http_request(
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

        status, existing_lists, body = service.http_request(
            app_url, f"{api_base}/importlist", api_key=api_key
        )
        if status != 200 or not isinstance(existing_lists, list):
            raise RuntimeError(f"{app_name}: failed listing import lists (HTTP {status}): {body}")

    preferred_id, preferred_names = service.resolve_arr_quality_preferences(cfg, app_cfg)
    selected_profile = service.get_arr_quality_profile(
        app_name,
        app_url,
        api_base,
        api_key,
        preferred_id=preferred_id,
        preferred_names=preferred_names,
    )
    selected_profile_id = service.to_int(selected_profile.get("id"))
    selected_profile_name = str(selected_profile.get("name") or "")
    service.log(
        f"[OK] {app_name}: using quality profile '{selected_profile_name}' "
        f"(id={selected_profile_id}) for discovery lists"
    )

    app_impl = str(app_cfg.get("implementation") or "").strip().lower()
    selected_metadata_profile_id = None
    if app_impl in ("lidarr", "readarr"):
        for metadata_endpoint in ("metadataprofile", "metadataProfile"):
            try:
                selected_metadata_profile_id = service.pick_first_profile_id(
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
            service.log(
                f"[OK] {app_name}: using metadata profile id {selected_metadata_profile_id} for discovery lists"
            )
        else:
            service.log(
                f"[WARN] {app_name}: could not resolve metadata profile id; "
                "list creation may fail if this Arr requires metadataProfileId."
            )

    created = 0
    updated = 0
    deleted = 0
    skipped = 0
    desired_keys = set()
    managed_implementations = {
        str(item.implementation or "").strip().lower()
        for item in list_defs
        if str(item.implementation or "").strip()
    }

    for list_cfg in list_defs:
        impl_raw = str(list_cfg.implementation or "").strip()
        if not impl_raw:
            service.log(f"[WARN] {app_name}: skipping import list entry without implementation")
            skipped += 1
            continue
        if list_cfg.contract_missing_override_fields:
            missing_fields = ", ".join(list_cfg.contract_missing_override_fields)
            msg = (
                f"{app_name}: import list '{list_cfg.name}' ({impl_raw}) is missing required "
                f"field_overrides: {missing_fields}"
            )
            if bool(list_cfg.required):
                raise RuntimeError(msg)
            service.log(f"[WARN] {msg}")
            skipped += 1
            continue
        schema = schemas_by_impl.get(impl_raw.lower())
        if not schema:
            msg = f"{app_name}: import list implementation '{impl_raw}' is not supported by this Arr build."
            if bool(list_cfg.required):
                raise RuntimeError(msg)
            service.log(f"[WARN] {msg}")
            skipped += 1
            continue

        schema_fields = {str(f.get("name") or "") for f in (schema.get("fields") or [])}
        list_name = str(list_cfg.name or schema.get("implementationName") or impl_raw).strip()
        auth_required = "signIn" in schema_fields or bool(list_cfg.contract.requires_auth)
        if auth_required:
            has_token = list_cfg.has_provider_auth_token(service.resolve_env_placeholder)
            if not has_token and bool(list_cfg.skip_if_auth_required):
                service.log(
                    f"[WARN] {app_name}: skipping import list '{list_name}' ({impl_raw}) because provider auth is required "
                    "(set field_overrides.accessToken/refreshToken to enable)."
                )
                skipped += 1
                continue

        payload = build_arr_import_list_payload(
            service,
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
            status, _, body = service.http_request(
                app_url,
                f"{api_base}/importlist/{existing.get('id')}",
                api_key=api_key,
                method="PUT",
                payload=payload,
            )
            if status in (200, 201, 202):
                updated += 1
                service.log(f"[OK] {app_name}: updated discovery list '{payload['name']}'")
                continue
            msg = f"{app_name}: failed updating discovery list '{payload['name']}' (HTTP {status}): {body}"
            if bool(list_cfg.required):
                raise RuntimeError(msg)
            service.log(f"[WARN] {msg}")
            skipped += 1
            continue

        status, _, body = service.http_request(
            app_url,
            f"{api_base}/importlist",
            api_key=api_key,
            method="POST",
            payload=payload,
        )
        if status in (200, 201, 202):
            created += 1
            service.log(f"[OK] {app_name}: created discovery list '{payload['name']}'")
            continue

        msg = f"{app_name}: failed creating discovery list '{payload['name']}' (HTTP {status}): {body}"
        if bool(list_cfg.required):
            raise RuntimeError(msg)
        service.log(f"[WARN] {msg}")
        skipped += 1

    if list_defs and prune_unmanaged and managed_implementations:
        status, existing_lists, body = service.http_request(
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
            status, _, body = service.http_request(
                app_url,
                f"{api_base}/importlist/{item_id}",
                api_key=api_key,
                method="DELETE",
            )
            if status in (200, 202, 204):
                deleted += 1
                service.log(
                    f"[OK] {app_name}: pruned unmanaged discovery list '{item.get('name', item_id)}'"
                )
                continue
            service.log(
                f"[WARN] {app_name}: failed pruning unmanaged discovery list '{item.get('name', item_id)}' (HTTP {status}): {body}"
            )

    service.log(
        f"[OK] {app_name}: discovery list reconcile complete "
        f"(created={created}, updated={updated}, deleted={deleted}, skipped={skipped})"
    )

    if app_impl == "sonarr":
        ensure_sonarr_seed_series(
            service=service,
            cfg=cfg,
            app_cfg=app_cfg,
            app_name=app_name,
            app_url=app_url,
            api_base=api_base,
            api_key=api_key,
            default_quality_profile_id=selected_profile_id,
        )
