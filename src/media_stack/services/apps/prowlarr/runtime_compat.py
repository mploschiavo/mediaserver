"""Backward-compatible runtime property accessors for Prowlarr fields.

These map legacy named kwargs to generic dict slots in ControllerRuntime
and provide property descriptors for callers that still use attribute access.
As consumers migrate to the generic dict API, entries here should shrink.
"""

from __future__ import annotations

from typing import Any

# Maps old named-param keywords to (dest_dict_attr, dict_key, value_kind).
LEGACY_KWARG_MAP: dict[str, tuple[str, str, str]] = {
    "prowlarr_url": ("service_urls", "prowlarr", "str"),
    "prowlarr_key": ("service_keys", "prowlarr", "str"),
    "prowlarr_indexers": ("service_data", "indexer_manager_indexers", "list"),
}

# Legacy arg-token aliases for runner phase plan configs that still
# reference "prowlarr_*" tokens.  The values are runtime attribute names.
LEGACY_ARG_TOKEN_ALIASES: dict[str, str] = {
    "prowlarr_url": "prowlarr_url",
    "prowlarr_key": "prowlarr_key",
    "prowlarr_indexers": "prowlarr_indexers",
}


class ProwlarrRuntimeCompatMixin:
    """Mixin providing backward-compat property accessors for Prowlarr fields."""

    @property
    def prowlarr_url(self) -> str:
        return self.service_urls.get("prowlarr", "")

    @property
    def prowlarr_key(self) -> str:
        return self.service_keys.get("prowlarr", "")

    @property
    def prowlarr_indexers(self) -> list[dict[str, Any]]:
        return (
            self.service_data.get("prowlarr_indexers")
            or self.service_data.get("indexer_manager_indexers", [])
        )
