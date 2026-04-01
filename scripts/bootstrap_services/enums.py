"""Shared bootstrap enums."""

from __future__ import annotations

from enum import Enum


class BootstrapMode(str, Enum):
    FULL = "full"
    MEDIA_SERVER_PREWARM = "media-server-prewarm"
    MEDIA_SERVER_HOME_RAILS = "media-server-home-rails"
    MEDIA_HYGIENE = "media-hygiene"

    @classmethod
    def choices(cls) -> list[str]:
        return [
            cls.FULL.value,
            cls.MEDIA_SERVER_PREWARM.value,
            cls.MEDIA_SERVER_HOME_RAILS.value,
            cls.MEDIA_HYGIENE.value,
        ]

    @classmethod
    def from_cli(cls, value: str) -> "BootstrapMode":
        text = str(value or "").strip().lower()
        for mode in cls:
            if mode.value == text:
                return mode
        raise ValueError(f"Unsupported bootstrap mode: {value}")


class RunnerOperation(str, Enum):
    ENSURE_APP_AUTH_SETTINGS = "ensure_app_auth_settings"
    TORRENT_CLIENT_LOGIN = "torrent_client_login"
    QBIT_LOGIN = "qbit_login"
    READ_SABNZBD_API_KEY = "read_sabnzbd_api_key"
    ENSURE_SABNZBD_DEFAULTS = "ensure_sabnzbd_defaults"
    ENSURE_SABNZBD_CATEGORIES = "ensure_sabnzbd_categories"
    SETUP_TORRENT_CATEGORIES = "setup_torrent_categories"
    SETUP_QBIT_CATEGORIES = "setup_qbit_categories"
    RUN_SERVARR_PIPELINE = "run_servarr_pipeline"
    ENSURE_BAZARR_INTEGRATION = "ensure_bazarr_arr_integration"
    CONFIGURE_JELLYSEERR = "configure_jellyseerr"
    ENSURE_JELLYFIN_LIVETV = "ensure_jellyfin_livetv"
    ENSURE_JELLYFIN_LIBRARIES = "ensure_jellyfin_libraries"
    ENSURE_JELLYFIN_PLUGINS = "ensure_jellyfin_plugins"
    ENSURE_JELLYFIN_PLAYBACK = "ensure_jellyfin_playback_defaults"
    ENSURE_JELLYFIN_HOME_RAILS = "ensure_jellyfin_home_rails"
    ENSURE_JELLYFIN_AUTO_COLLECTIONS = "ensure_jellyfin_auto_collections_config"
    ENFORCE_DISK_GUARDRAILS = "enforce_disk_guardrails"
    RUN_MEDIA_HYGIENE = "run_media_hygiene"
    ENSURE_JELLYFIN_PREWARM = "ensure_jellyfin_prewarm"
    ENSURE_MAINTAINERR_POLICY = "ensure_maintainerr_policy"
    ENSURE_MAINTAINERR_INTEGRATIONS = "ensure_maintainerr_integrations"
    ENSURE_HOMEPAGE_SERVICES = "ensure_homepage_services_config"
    ENSURE_PROWLARR_READY = "ensure_prowlarr_ready"
    ENSURE_PROWLARR_FLARESOLVERR_PROXY = "ensure_prowlarr_flaresolverr_proxy"
    ENSURE_PROWLARR_INDEXER = "ensure_prowlarr_indexer"
    AUTO_ADD_TESTED_INDEXERS = "auto_add_tested_indexers"
    TRIGGER_PROWLARR_SYNC = "trigger_prowlarr_sync"
    SYNC_ARR_INDEXERS_FROM_PROWLARR = "sync_arr_indexers_from_prowlarr"
    RUN_PROWLARR_INDEXER_PIPELINE = "run_prowlarr_indexer_pipeline"
