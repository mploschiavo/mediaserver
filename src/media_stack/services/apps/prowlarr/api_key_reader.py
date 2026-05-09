"""Prowlarr API key discovery and service-dict wiring."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any, Callable


#: Service ID constant — single source of truth for the prowlarr service name.
SERVICE_ID = "prowlarr"

ReadApiKeyFn = Callable[[str, str], str]


@dataclass
class ProwlarrRuntimeWiring:
    """Runtime wiring data for prowlarr, produced by the app layer."""

    url: str = ""
    key: str = ""
    indexers: list[Any] = field(default_factory=list)
    include_in_app_auth: bool = False
    display_name: str = "Prowlarr"


class ProwlarrApiKeyReader:
    """Loose helpers folded onto a class per ADR-0012.

    Holds the module-level prowlarr API-key discovery + service-dict
    population helpers. Module-level aliases preserve the public import
    API; the underscore-free public methods carry full type annotations.
    """

    def read_prowlarr_api_key(
        self,
        *,
        config_root: str,
        read_api_key: ReadApiKeyFn,
    ) -> tuple[str, list[str]]:
        """Attempt to read the Prowlarr API key, returning (key, skipped_apps).

        On failure the key is empty and the service ID is returned as skipped
        so the caller can report it generically.
        """
        skipped: list[str] = []
        try:
            key = read_api_key(config_root, SERVICE_ID)
        except RuntimeError as exc:
            print(
                f"[WARN] {SERVICE_ID}: API key unavailable, skipping indexer sync ({exc}). "
                f"Run Reconcile after {SERVICE_ID.title()} generates its config.",
                file=sys.stderr,
            )
            skipped.append(SERVICE_ID)
            key = ""
        return key, skipped

    def resolve_prowlarr_wiring(
        self,
        *,
        cfg: dict[str, Any],
    ) -> ProwlarrRuntimeWiring:
        """Extract prowlarr-specific config from the top-level config dict.

        This keeps the prowlarr service name in the app layer rather than
        in the runtime builder.
        """
        url = str(cfg.get("prowlarr_url") or "").strip().rstrip("/")
        indexers = cfg.get("prowlarr_indexers", [])
        return ProwlarrRuntimeWiring(
            url=url,
            indexers=indexers,
            include_in_app_auth=bool(url),
        )

    def populate_prowlarr_service_dicts(
        self,
        wiring: ProwlarrRuntimeWiring,
        *,
        service_urls: dict[str, str],
        service_keys: dict[str, str],
        service_data: dict[str, Any],
    ) -> None:
        """Populate the generic service dicts with prowlarr runtime data.

        Stores under both the technology-specific key (SERVICE_ID) and the
        role-based key so platform code can look up values by role.
        """
        for key in (SERVICE_ID, "indexer_manager"):
            if wiring.url:
                service_urls[key] = wiring.url
            if wiring.key:
                service_keys[key] = wiring.key
        service_data["prowlarr_indexers"] = wiring.indexers
        # Role-based alias for platform code that does not want to hardcode a service name
        service_data["indexer_manager_indexers"] = wiring.indexers


_INSTANCE = ProwlarrApiKeyReader()
read_prowlarr_api_key = _INSTANCE.read_prowlarr_api_key
resolve_prowlarr_wiring = _INSTANCE.resolve_prowlarr_wiring
populate_prowlarr_service_dicts = _INSTANCE.populate_prowlarr_service_dicts
