"""Ratchet R-4: Envoy v2 route precedence is deterministic.

Envoy evaluates routes top-down within a virtual_host; the *first*
match wins. The v2 generator emits routes in this order:

    1. path_aliases       (path_separated_prefix match — most specific)
    2. host[].path_prefix (specific path-rooted services)
    3. host[].canonical   ("/" prefix forwards on path-routed hosts)
    4. apex               (exact "/" path match — wins over generic prefix)
    5. catch_all          ("/" prefix — last-resort fallthrough)

Order matters for correctness: a catch-all that runs before the apex
swallows the apex; a path_prefix that runs before path_aliases makes
aliases unreachable. The ratchet covers a fan of permutations
(every combination of present/absent route classes) and verifies the
ordering invariant holds for each.

This is structural, not a single end-to-end test, because the
generator can drop classes that aren't configured (e.g. apex=NONE
emits no apex route). The ratchet must verify that *whichever
classes are present* still come out in precedence order.
"""
from __future__ import annotations

import sys
import unittest
from itertools import product
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.api.services.config.routing.schema_v2 import (  # noqa: E402
    ApexAction,
    ApexConfig,
    CatchAllAction,
    CatchAllConfig,
    HostEntry,
    PathAlias,
    RoutingConfigV2,
)
from media_stack.services.edge.envoy_route_generator_v2 import (  # noqa: E402
    generate_route_config_v2,
)


# Precedence tiers — lower index = matches first.
KIND_RANK = {
    "alias": 0,
    "path_prefix": 1,
    "subdomain_root": 2,  # canonical with no path_prefix
    "apex": 3,
    "catch_all": 4,
}


def _kind(route: dict) -> str:
    m = route.get("match", {})
    if "path_separated_prefix" in m:
        return "alias"
    if m.get("path") == "/":
        return "apex"
    prefix = m.get("prefix")
    if prefix == "/":
        # Disambiguate subdomain_root vs catch_all: the former forwards
        # to a cluster, the latter is the last route in the vhost.
        # Treat both the same for ordering rank — they only differ in
        # `route` vs `redirect`/`direct_response` which is handled by
        # other tests.
        if "route" in route:
            return "subdomain_root"
        return "catch_all"
    if prefix and prefix != "/":
        return "path_prefix"
    return "unknown"


def _ordering_holds(routes: list[dict]) -> tuple[bool, list[str]]:
    kinds = [_kind(r) for r in routes]
    ranks = [KIND_RANK.get(k, -1) for k in kinds]
    return (ranks == sorted(ranks)), kinds


class EnvoyV2RouteOrderingRatchet(unittest.TestCase):
    def test_sample_full_config_obeys_ordering(self) -> None:
        cfg = RoutingConfigV2(gateway_host="m.iomio.io")
        cfg.hosts.append(HostEntry(role="dashboard", service_id="homepage",
                                    canonical="m.iomio.io",
                                    path_prefix="/apps"))
        cfg.path_aliases.append(PathAlias(from_path="/app/jellyfin",
                                          to_path="/app/jf"))
        cfg.path_aliases.append(PathAlias(from_path="/app/ui",
                                          to_path="/app/dashboard"))
        cfg.apex = ApexConfig(action=ApexAction.REDIRECT, target="/apps")
        cfg.catch_all = CatchAllConfig(action=CatchAllAction.REDIRECT, target="/apps")
        rc = generate_route_config_v2(cfg)
        vh = next(v for v in rc["virtual_hosts"] if "m.iomio.io" in v["domains"])
        ok, kinds = _ordering_holds(vh["routes"])
        self.assertTrue(ok, f"order broken: {kinds}")

    def test_every_subset_obeys_ordering(self) -> None:
        # Generate the full power-set of (apex on/off, catch_all on/off,
        # path_aliases on/off, path_prefix host on/off) and check each.
        switches = list(product([False, True], repeat=4))
        for has_apex, has_ca, has_alias, has_pp in switches:
            with self.subTest(
                apex=has_apex, catch_all=has_ca,
                alias=has_alias, path_prefix=has_pp,
            ):
                cfg = RoutingConfigV2(gateway_host="m.iomio.io")
                if has_apex:
                    cfg.apex = ApexConfig(action=ApexAction.REDIRECT,
                                          target="/apps")
                if has_ca:
                    cfg.catch_all = CatchAllConfig(
                        action=CatchAllAction.REDIRECT, target="/apps",
                    )
                if has_alias:
                    cfg.path_aliases.append(
                        PathAlias(from_path="/old", to_path="/new"),
                    )
                if has_pp:
                    cfg.hosts.append(HostEntry(
                        role="dashboard", service_id="homepage",
                        canonical="m.iomio.io", path_prefix="/apps",
                    ))
                rc = generate_route_config_v2(cfg)
                gw_vhosts = [
                    v for v in rc["virtual_hosts"]
                    if "m.iomio.io" in v["domains"]
                ]
                if not gw_vhosts:
                    # No routes emitted — nothing to verify.
                    continue
                ok, kinds = _ordering_holds(gw_vhosts[0]["routes"])
                self.assertTrue(
                    ok,
                    f"order broken with switches {switches}: {kinds}",
                )


if __name__ == "__main__":
    unittest.main()
