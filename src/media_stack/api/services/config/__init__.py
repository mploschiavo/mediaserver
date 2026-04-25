"""Configuration services — class-based with backward-compatible module-level API.

Handlers import this as ``from .services import config as config_svc`` and call
``config_svc.get_libraries()``.  Internally each domain is a service class that
takes ``ProfileService`` as a dependency.
"""
from __future__ import annotations

from .._resolve import resolve_config_path, resolve_profile_path
from ._profile import ProfileService
from ._media_server import LibraryConfigService
from ._downloads import DownloadConfigService
from ._metadata import MetadataConfigService
from ._livetv import LiveTvConfigService, APP_CONFIG_SECTIONS, DEDICATED_ENDPOINT_SECTIONS, STRIPPED_FROM_PROFILE
from ._routing import RoutingConfigService
from ._diagnostics import DiagnosticsService

# ---------------------------------------------------------------------------
# Singleton instances — shared across all handler calls
# ---------------------------------------------------------------------------

_profile = ProfileService()
_libraries = LibraryConfigService(_profile)
_downloads = DownloadConfigService(_profile)
_metadata = MetadataConfigService(_profile)
_livetv = LiveTvConfigService(_profile)
_routing = RoutingConfigService(_profile)
_diagnostics = DiagnosticsService(_profile)

# ---------------------------------------------------------------------------
# Module-level functions — backward compat for ``config_svc.get_*()`` callers
# ---------------------------------------------------------------------------

# Profile
_load_profile_yaml = _profile.load
_invalidate_profile_cache = _profile.invalidate_cache
_validate_profile_data = _profile.validate
_save_profile_yaml = _profile.save
update_profile_section = _profile.update_section
_media_server_id = _profile.media_server_id

# Libraries
get_libraries = _libraries.get_libraries
update_libraries = _libraries.update_libraries

# Downloads
get_download_categories = _downloads.get_download_categories
update_download_categories = _downloads.update_download_categories

# Metadata
get_metadata_settings = _metadata.get_metadata_settings
update_metadata_settings = _metadata.update_metadata_settings

# Live TV
get_livetv_sources = _livetv.get_livetv_sources
update_livetv_sources = _livetv.update_livetv_sources
get_discovery_lists = _livetv.get_discovery_lists
update_discovery_lists = _livetv.update_discovery_lists
get_iptv_countries = _livetv.get_iptv_countries

# Routing & Profile
get_profile = _routing.get_profile
save_profile = _routing.save_profile
get_routing = _routing.get_routing
update_routing = _routing.update_routing

# Diagnostics
get_env = _diagnostics.get_env
get_backup = _diagnostics.get_backup
restore_backup = _diagnostics.restore_backup
get_envvars = _diagnostics.get_envvars
set_envvar = _diagnostics.set_envvar
delete_envvar = _diagnostics.delete_envvar
get_manifests = _diagnostics.get_manifests
get_onboarding_status = _diagnostics.get_onboarding_status
add_custom_service = _diagnostics.add_custom_service
get_config_drift = _diagnostics.get_config_drift

# Constants (used by routing)
_APP_CONFIG_SECTIONS = APP_CONFIG_SECTIONS
_DEDICATED_ENDPOINT_SECTIONS = DEDICATED_ENDPOINT_SECTIONS
_STRIPPED_FROM_PROFILE = STRIPPED_FROM_PROFILE
