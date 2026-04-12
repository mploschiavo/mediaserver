"""Diagnostic logging for bootstrap config policy values.

Service-specific config key names live here in the app layer so that
platform code does not need to hardcode them.
"""

from __future__ import annotations

from typing import Any, Callable

LogFn = Callable[[str], None]

# Config section / key names that are service-specific.
_DASHBOARD_SECTION_KEY = "homepage"
_INDEXER_MANAGER_URL_KEY = "prowlarr_url"


class StackConfigDiagnostics:

    def log_config_policy_values(self, cfg: dict[str, Any], info: LogFn) -> None:
        """Log key config values after policy application."""
        dashboard_hosts = (cfg.get(_DASHBOARD_SECTION_KEY) or {}).get("hosts", [])
        technology_bindings = cfg.get("technology_bindings", {})
        indexer_mgr_url = cfg.get(_INDEXER_MANAGER_URL_KEY, "")
        app_auth_url_bases = (cfg.get("app_auth") or {}).get("path_prefix_url_base_by_app", {})
        info(f"Config policy applied: dashboard.hosts={dashboard_hosts}")
        info(f"Config policy applied: technology_bindings={technology_bindings}")
        info(f"Config policy applied: indexer_manager_url={indexer_mgr_url}")
        info(f"Config policy applied: app_auth.url_bases={app_auth_url_bases}")


_instance = StackConfigDiagnostics()
log_config_policy_values = _instance.log_config_policy_values
