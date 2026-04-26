"""Arr discovery-list bootstrap service logic."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from media_stack.services.apps.servarr.config_models import ArrDiscoveryListsConfig
from .ops import (
    build_arr_import_list_payload,
    ensure_arr_discovery_lists_for_app,
    resolve_import_list_definitions,
    trigger_arr_discovery_kickoff,
)

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

    def resolve_import_list_definitions(
        self,
        arr_discovery_cfg: ArrDiscoveryListsConfig | dict[str, Any],
        app_cfg: dict[str, Any],
    ) -> list[dict[str, Any]]:
        return resolve_import_list_definitions(self, arr_discovery_cfg, app_cfg)

    def build_arr_import_list_payload(
        self,
        app_cfg: dict[str, Any],
        schema: dict[str, Any],
        list_cfg: dict[str, Any],
        default_quality_profile_id: int | None,
        default_metadata_profile_id: int | None = None,
    ) -> dict[str, Any]:
        return build_arr_import_list_payload(
            self,
            app_cfg,
            schema,
            list_cfg,
            default_quality_profile_id,
            default_metadata_profile_id,
        )

    def ensure_arr_discovery_lists_for_app(
        self,
        cfg: dict[str, Any],
        app_cfg: dict[str, Any],
        app_url: str,
        api_base: str,
        api_key: str,
    ) -> None:
        ensure_arr_discovery_lists_for_app(self, cfg, app_cfg, app_url, api_base, api_key)

    def trigger_arr_discovery_kickoff(
        self,
        cfg: dict[str, Any],
        app_cfg: dict[str, Any],
        app_url: str,
        api_base: str,
        api_key: str,
    ) -> None:
        trigger_arr_discovery_kickoff(self, cfg, app_cfg, app_url, api_base, api_key)
