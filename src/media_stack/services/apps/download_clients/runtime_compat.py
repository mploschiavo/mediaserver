"""Backward-compatible runtime property accessors for download-client fields.

These map legacy named kwargs to generic dict slots in ControllerRuntime
and provide property descriptors for callers that still use attribute access.
As consumers migrate to the generic dict API, entries here should shrink.
"""

from __future__ import annotations

from typing import Any

# Maps old named-param keywords to (dest_dict_attr, dict_key, value_kind).
LEGACY_KWARG_MAP: dict[str, tuple[str, str, str]] = {
    "qbit_cfg": ("service_configs", "qbittorrent", "dict"),
    "sab_cfg": ("service_configs", "sabnzbd", "dict"),
    "qb_user": ("service_credentials", "qbittorrent.user", "str"),
    "qb_pass": ("service_credentials", "qbittorrent.pass", "str"),
    "sab_username": ("service_credentials", "sabnzbd.user", "str"),
    "sab_password": ("service_credentials", "sabnzbd.pass", "str"),
    "sab_remote_path_mappings": ("service_data", "sab_remote_path_mappings", "list"),
}


class DownloadClientRuntimeCompatMixin:
    """Mixin providing backward-compat property accessors for download-client fields."""

    @property
    def qbit_cfg(self) -> dict[str, Any]:
        return self.service_configs.get("qbittorrent", {})

    @property
    def sab_cfg(self) -> dict[str, Any]:
        return self.service_configs.get("sabnzbd", {})

    @property
    def qb_user(self) -> str:
        return self.service_credentials.get("qbittorrent", {}).get("user", "")

    @property
    def qb_pass(self) -> str:
        return self.service_credentials.get("qbittorrent", {}).get("pass", "")

    @property
    def sab_username(self) -> str:
        return self.service_credentials.get("sabnzbd", {}).get("user", "")

    @property
    def sab_password(self) -> str:
        return self.service_credentials.get("sabnzbd", {}).get("pass", "")

    @property
    def sab_remote_path_mappings(self) -> list[dict[str, Any]]:
        return self.service_data.get("sab_remote_path_mappings", [])

    @property
    def torrent_client_cfg(self) -> dict[str, Any]:
        return self.service_configs.get(self.torrent_client_key or "qbittorrent", {})

    @property
    def torrent_client_username(self) -> str:
        key = self.torrent_client_key or "qbittorrent"
        return self.service_credentials.get(key, {}).get("user", "")

    @property
    def torrent_client_password(self) -> str:
        key = self.torrent_client_key or "qbittorrent"
        return self.service_credentials.get(key, {}).get("pass", "")
