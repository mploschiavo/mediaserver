"""Resolve technology bindings into active runtime integrations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..apps.download_clients.config_models import DownloadClientsConfig, TechnologyBindingsConfig


@dataclass(frozen=True)
class RuntimeBindingResolution:
    technology_aliases: dict[str, str] = field(default_factory=dict)
    torrent_client_key: str = ""
    usenet_client_key: str = ""
    media_server_backend: str = ""
    request_manager_key: str = ""
    torrent_client_cfg: dict[str, Any] = field(default_factory=dict)
    usenet_client_cfg: dict[str, Any] = field(default_factory=dict)


@dataclass
class RuntimeBindingResolver:
    def _aliases(self, adapter_hooks_cfg: dict[str, Any]) -> dict[str, str]:
        raw_aliases = (adapter_hooks_cfg or {}).get("technology_aliases") or {}
        aliases: dict[str, str] = {}
        if not isinstance(raw_aliases, dict):
            return aliases
        for source, target in raw_aliases.items():
            src = str(source or "").strip().lower()
            dst = str(target or "").strip().lower()
            if src and dst:
                aliases[src] = dst
        return aliases

    def _canonical(self, value: str, aliases: dict[str, str]) -> str:
        token = str(value or "").strip().lower()
        if not token:
            return ""
        return aliases.get(token, token)

    def resolve(
        self,
        *,
        technology_bindings: TechnologyBindingsConfig,
        adapter_hooks_cfg: dict[str, Any],
        download_clients: DownloadClientsConfig,
        media_server_cfg: dict[str, Any],
    ) -> RuntimeBindingResolution:
        aliases = self._aliases(adapter_hooks_cfg)
        configured_download_client_keys = download_clients.configured_keys()

        def _resolve_optional_download_client(
            binding_value: str, binding_name: str
        ) -> tuple[str, dict[str, Any]]:
            canonical = self._canonical(binding_value, aliases)
            if not canonical:
                return "", {}
            selected = download_clients.get(canonical)
            if not selected:
                raise ValueError(
                    "technology_bindings."
                    f"{binding_name}='{canonical}' does not match a configured download client. "
                    f"Configured clients: {', '.join(configured_download_client_keys) or '<none>'}"
                )
            return canonical, selected.raw

        torrent_client_key, torrent_client_cfg = _resolve_optional_download_client(
            technology_bindings.torrent_client,
            "torrent_client",
        )
        usenet_client_key, usenet_client_cfg = _resolve_optional_download_client(
            technology_bindings.usenet_client,
            "usenet_client",
        )

        media_server_backend = self._canonical(
            str(media_server_cfg.get("backend") or technology_bindings.media_server),
            aliases,
        )
        if not media_server_backend:
            raise ValueError(
                "Missing media server binding. Set technology_bindings.media_server "
                "or media_server.backend in bootstrap config."
            )

        request_manager_key = self._canonical(
            technology_bindings.request_manager,
            aliases,
        )
        if not request_manager_key:
            request_manager_key = "jellyseerr"

        return RuntimeBindingResolution(
            technology_aliases=aliases,
            torrent_client_key=torrent_client_key,
            usenet_client_key=usenet_client_key,
            media_server_backend=media_server_backend,
            request_manager_key=request_manager_key,
            torrent_client_cfg=torrent_client_cfg,
            usenet_client_cfg=usenet_client_cfg,
        )
