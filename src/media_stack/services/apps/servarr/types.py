"""Shared Servarr pipeline datatypes."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ServarrRunConfig:
    configure_arr_media_management: bool
    configure_arr_download_handling: bool
    configure_arr_quality_upgrade: bool
    configure_arr_discovery_lists: bool
    configure_qbit_arr_clients: bool
    qbit_login_ok: bool
    configure_sab_arr_clients: bool
    sab_api_key: str
    refresh_health_after_setup: bool


@dataclass(frozen=True)
class ClientAuth:
    username: str = ""
    password: str = ""
