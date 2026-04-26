"""Backward-compatible runtime property accessors for Prowlarr fields.

These map legacy named kwargs to generic dict slots in ControllerRuntime
and provide property descriptors for callers that still use attribute access.
As consumers migrate to the generic dict API, entries here should shrink.
"""

from __future__ import annotations

from typing import Any

# Maps old named-param keywords to (dest_dict_attr, dict_key, value_kind).
LEGACY_KWARG_MAP: dict[str, tuple[str, str, str]] = {
    # Generic names (preferred by platform code)
    "indexer_url": ("service_urls", "prowlarr", "str"),
    "indexer_key": ("service_keys", "prowlarr", "str"),
    "indexer_entries": ("service_data", "indexer_manager_indexers", "list"),
    # Backward-compat: old names still accepted
    "prowlarr_url": ("service_urls", "prowlarr", "str"),
    "prowlarr_key": ("service_keys", "prowlarr", "str"),
    "prowlarr_indexers": ("service_data", "indexer_manager_indexers", "list"),
}

# Arg-token aliases for runner phase plan configs.
# Both generic and legacy names map to the same runtime attributes.
LEGACY_ARG_TOKEN_ALIASES: dict[str, str] = {
    "indexer_url": "indexer_url",
    "indexer_key": "indexer_key",
    "indexer_entries": "indexer_entries",
    "prowlarr_url": "indexer_url",
    "prowlarr_key": "indexer_key",
    "prowlarr_indexers": "indexer_entries",
}


class ProwlarrRuntimeCompatMixin:
    """Mixin providing property accessors for indexer manager fields.

    Generic names (indexer_url, indexer_key, indexer_entries) are preferred.
    Legacy names (prowlarr_*) are kept for backward compatibility.
    """

    # --- Generic names (platform code should use these) ---

    @property
    def indexer_url(self) -> str:
        return self.service_urls.get("prowlarr", "")  # type: ignore[attr-defined]

    @property
    def indexer_key(self) -> str:
        return self.service_keys.get("prowlarr", "")  # type: ignore[attr-defined]

    @property
    def indexer_entries(self) -> list[dict[str, Any]]:
        return (  # type: ignore[attr-defined]
            self.service_data.get("prowlarr_indexers")
            or self.service_data.get("indexer_manager_indexers", [])
        )

    # --- Backward-compat aliases (app-layer code may still use these) ---

    @property
    def prowlarr_url(self) -> str:
        return self.indexer_url

    @property
    def prowlarr_key(self) -> str:
        return self.indexer_key

    @property
    def prowlarr_indexers(self) -> list[dict[str, Any]]:
        return self.indexer_entries
