"""ProfileCatalogValidator — Validator for catalog-driven allow-lists.

The bootstrap profile catalog
(``contracts/media-stack.catalog.yaml``) ships allow-lists for
operator-tunable string fields:

* Route strategies — ``subdomain`` / ``path-prefix`` / ``hybrid``
  (with aliases that normalise to the canonical names).
* Edge router providers — derived from the registry's compose-
  label-specs keys (whatever providers the build supports today).

Validator pattern: this class is the "is this string acceptable?"
oracle for those two fields. Callers (the runner's _validate_inputs
phase) compare the operator's CLI/env value to these tuples.
Returning the tuples (not booleans) lets the validation error
message enumerate the allowed values so the operator sees what's
valid.

Constructor-injects :class:`EdgeRoutingResolver` because
:meth:`valid_edge_router_providers` derives from the compose-
provider-specs map (which lives on the edge resolver) — the
validator doesn't re-read the registry, it asks the
edge-routing strategy what it sees.
"""

from __future__ import annotations

from media_stack.core.controller_profile import load_bootstrap_profile_catalog
from media_stack.cli.workflows.deploy_config.edge_routing_resolver import (
    EdgeRoutingResolver,
)


class ProfileCatalogValidator:
    """Validator: route-strategy + edge-router-provider allow-lists."""

    def __init__(self, edge_routing: EdgeRoutingResolver) -> None:
        self._edge_routing = edge_routing

    def valid_route_strategies(self) -> tuple[str, ...]:
        """Distinct normalised route-strategy values from the catalog.

        ``catalog.route_strategy_aliases`` maps several aliases
        (e.g. ``path_prefix``, ``pathprefix``) to the canonical
        ``path-prefix``. We return the canonical SET (deduped via
        ``dict.fromkeys``) so the validation error message lists
        each strategy once.
        """
        catalog = load_bootstrap_profile_catalog()
        values = tuple(dict.fromkeys(catalog.route_strategy_aliases.values()))
        return tuple(
            str(value).strip().lower()
            for value in values
            if str(value).strip()
        )

    def valid_edge_router_providers(self) -> tuple[str, ...]:
        """Sorted set of supported edge router providers.

        Derived from whichever providers the edge_routing strategy
        currently exposes in its compose-provider-specs map. New
        provider support adds a key there; this method picks it
        up automatically without a code change here.
        """
        providers = {
            str(provider or "").strip().lower()
            for provider in self._edge_routing.compose_provider_specs().keys()
            if str(provider or "").strip()
        }
        return tuple(sorted(providers))


__all__ = ["ProfileCatalogValidator"]
