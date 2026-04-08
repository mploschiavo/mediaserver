"""Bootstrap profile model for distribution-friendly deployment defaults.

This package re-exports all public symbols so that existing imports like
``from media_stack.core.controller_profile import X`` continue to work.
"""

from media_stack.core.controller_profile.models import (  # noqa: F401
    ControllerChaosSettings,
    ControllerExposureSettings,
    ControllerProfileCatalog,
    ControllerProfileConfig,
)

from media_stack.core.controller_profile.catalog_loader import (  # noqa: F401
    _load_bootstrap_profile_catalog_cached,
    load_bootstrap_profile_catalog,
)

from media_stack.core.controller_profile.parser import (  # noqa: F401
    maybe_load_bootstrap_profile,
    normalize_selected_apps_csv,
)

from media_stack.core.controller_profile.normalizers import (  # noqa: F401
    _as_bool,
    _as_bool_with_tokens,
    _coerce_url_list,
    _install_apps_for_profile,
    _join_host,
    _normalize_alias_dict,
    _normalize_app_name,
    _normalize_app_token,
    _normalize_chaos_actions,
    _normalize_deployment_target,
    _normalize_host,
    _normalize_optional_port,
    _normalize_purpose,
    _normalize_route_strategy,
    _normalize_string_list,
    _normalize_string_list_allow_empty,
    _parse_private_network_cidr,
    _parse_storage_gb,
    _resolve_install_profile,
    _split_app_csv,
    _to_positive_int,
)

__all__ = [
    # Models
    "ControllerChaosSettings",
    "ControllerExposureSettings",
    "ControllerProfileCatalog",
    "ControllerProfileConfig",
    # Catalog loader
    "load_bootstrap_profile_catalog",
    # Parser / public helpers
    "maybe_load_bootstrap_profile",
    "normalize_selected_apps_csv",
]
