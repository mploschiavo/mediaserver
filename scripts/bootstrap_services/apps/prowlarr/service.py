"""Prowlarr API orchestration service."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .application_ops import (
    ensure_application,
    find_existing_application,
    resolve_schema_contract,
    trigger_sync,
)
from .indexer_ops import build_indexer_payload, ensure_indexer
from .proxy_ops import ensure_flaresolverr_proxy
from .reputation_ops import (
    auto_add_tested_indexers,
    coerce_exclude_name_tokens,
    load_reputation_state,
    reputation_key,
    save_reputation_state,
    set_indexer_enabled,
)

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
        return resolve_schema_contract(self, prowlarr_url, prowlarr_key, implementation)

    def find_existing_application(
        self,
        prowlarr_url: str,
        prowlarr_key: str,
        implementation: str,
        base_url: str,
    ) -> dict[str, Any] | None:
        return find_existing_application(self, prowlarr_url, prowlarr_key, implementation, base_url)

    def ensure_application(
        self,
        prowlarr_url: str,
        prowlarr_key: str,
        app_name: str,
        implementation: str,
        app_url: str,
        app_key: str,
    ) -> None:
        ensure_application(
            self,
            prowlarr_url,
            prowlarr_key,
            app_name,
            implementation,
            app_url,
            app_key,
        )

    def trigger_sync(self, prowlarr_url: str, prowlarr_key: str) -> None:
        trigger_sync(self, prowlarr_url, prowlarr_key)

    def ensure_indexer(
        self,
        prowlarr_url: str,
        prowlarr_key: str,
        indexer_cfg: dict[str, Any],
    ) -> None:
        ensure_indexer(self, prowlarr_url, prowlarr_key, indexer_cfg)

    def ensure_flaresolverr_proxy(
        self,
        prowlarr_url: str,
        prowlarr_key: str,
        flaresolverr_cfg: dict[str, Any] | None = None,
    ) -> None:
        ensure_flaresolverr_proxy(self, prowlarr_url, prowlarr_key, flaresolverr_cfg)

    def build_indexer_payload(self, template: dict[str, Any]) -> dict[str, Any]:
        return build_indexer_payload(self, template)

    @staticmethod
    def _coerce_exclude_name_tokens(raw_tokens: Any) -> list[str]:
        return coerce_exclude_name_tokens(raw_tokens)

    @staticmethod
    def _reputation_key(implementation: str, name: str) -> str:
        return reputation_key(implementation, name)

    def _load_reputation_state(self, path: Path) -> dict[str, Any]:
        return load_reputation_state(path)

    def _save_reputation_state(self, path: Path, state: dict[str, Any]) -> bool:
        return save_reputation_state(self, path, state)

    def _set_indexer_enabled(
        self,
        prowlarr_url: str,
        prowlarr_key: str,
        indexer: dict[str, Any],
        enabled: bool,
    ) -> bool:
        return set_indexer_enabled(self, prowlarr_url, prowlarr_key, indexer, enabled)

    def auto_add_tested_indexers(
        self,
        prowlarr_url: str,
        prowlarr_key: str,
        exclude_name_tokens: list[str] | None = None,
        reputation_cfg: dict[str, Any] | None = None,
    ) -> None:
        auto_add_tested_indexers(
            self, prowlarr_url, prowlarr_key, exclude_name_tokens, reputation_cfg
        )
