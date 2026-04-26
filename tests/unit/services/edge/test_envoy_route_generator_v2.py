"""Tests for ``generate_route_config_v2`` — the v2 Envoy route emitter.

Each test corresponds to a scenario from the design doc §1 (the user's
17 listed routing scenarios), with assertions on the emitted dict.
The tests use field-level assertions rather than full golden snapshots
so refactors that don't change semantics don't churn fixtures, but the
key invariants (route ordering, cluster names, redirect behaviour) are
locked.

Style mirrors the existing Envoy tests
(``tests/unit/api/test_envoy_*.py``): build a config, call the
emitter, walk the resulting structure with explicit field reads.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.api.services.config.routing.schema_v2 import (  # noqa: E402
    ApexAction,
    ApexConfig,
    AuthGate,
    CatchAllAction,
    CatchAllConfig,
    HostAuth,
    HostEntry,
    PathAlias,
    RoutingConfigV2,
    Strategy,
)
from media_stack.services.edge.envoy_route_generator_v2 import (  # noqa: E402
    generate_route_config_v2,
)


def _vhost_for(rc: dict, domain: str) -> dict:
    """Find the virtual_host whose domains include ``domain``."""
    for vh in rc["virtual_hosts"]:
        if domain in vh["domains"]:
            return vh
    raise AssertionError(
        f"no vhost for domain {domain!r}; available: "
        f"{[vh['domains'] for vh in rc['virtual_hosts']]}",
    )


class TestScenario1SubdomainHostnameToService(unittest.TestCase):
    """§1 #1: jf.iomio.io → jellyfin.

    A subdomain host emits a single virtual_host that forwards every
    path to the service's cluster."""

    def test_jellyfin_subdomain_forwards_to_cluster(self) -> None:
        cfg = RoutingConfigV2(gateway_host="m.iomio.io", strategy=Strategy.SUBDOMAIN)
        cfg.hosts.append(HostEntry(role="media_server", service_id="jellyfin",
                                    canonical="jf.iomio.io"))
        rc = generate_route_config_v2(cfg)
        vh = _vhost_for(rc, "jf.iomio.io")
        self.assertEqual(vh["domains"], ["jf.iomio.io"])
        self.assertEqual(len(vh["routes"]), 1)
        self.assertEqual(vh["routes"][0]["match"], {"prefix": "/"})
        # Cluster name is the locked invariant; other route fields
        # may be populated by the global defaults (timeout, etc.).
        self.assertEqual(vh["routes"][0]["route"]["cluster"], "service_jellyfin")


class TestScenario2AuthHostname(unittest.TestCase):
    """§1 #2: auth.iomio.io → authelia."""

    def test_auth_hostname_forwards_to_authelia(self) -> None:
        cfg = RoutingConfigV2(gateway_host="m.iomio.io")
        cfg.hosts.append(HostEntry(role="auth", service_id="authelia",
                                    canonical="auth.iomio.io",
                                    auth=HostAuth(gate=AuthGate.NONE)))
        rc = generate_route_config_v2(cfg)
        vh = _vhost_for(rc, "auth.iomio.io")
        self.assertEqual(vh["routes"][0]["route"]["cluster"], "service_authelia")


class TestScenario3PathPrefixRoute(unittest.TestCase):
    """§1 #3: m.iomio.io/apps/ → homepage.

    A host whose canonical IS the gateway and which has a path_prefix
    surfaces as a path-prefix route on the gateway vhost."""

    def test_apps_path_prefix_routes_to_homepage(self) -> None:
        cfg = RoutingConfigV2(gateway_host="m.iomio.io")
        cfg.hosts.append(HostEntry(role="dashboard", service_id="homepage",
                                    canonical="m.iomio.io",
                                    path_prefix="/apps"))
        rc = generate_route_config_v2(cfg)
        vh = _vhost_for(rc, "m.iomio.io")
        prefixes = [r["match"].get("prefix") for r in vh["routes"]]
        self.assertIn("/apps/", prefixes,
                      f"no /apps/ route — got {prefixes}")
        # Verify the cluster
        apps_route = next(r for r in vh["routes"]
                           if r["match"].get("prefix") == "/apps/")
        self.assertEqual(apps_route["route"]["cluster"], "service_homepage")


class TestScenario4ApexAction(unittest.TestCase):
    """§1 #4: m.iomio.io → ??? (apex landing).

    Three apex actions: NONE (no rule), REDIRECT (path_redirect),
    SERVICE (forward to a cluster), STATIC (inline body)."""

    def test_apex_none_emits_no_apex_route(self) -> None:
        cfg = RoutingConfigV2(gateway_host="m.iomio.io")
        cfg.apex = ApexConfig(action=ApexAction.NONE)
        rc = generate_route_config_v2(cfg)
        # No gateway vhost should exist when there's nothing to route there.
        with self.assertRaises(AssertionError):
            _vhost_for(rc, "m.iomio.io")

    def test_apex_redirect_emits_exact_path_redirect(self) -> None:
        cfg = RoutingConfigV2(gateway_host="m.iomio.io")
        cfg.apex = ApexConfig(action=ApexAction.REDIRECT, target="/apps", code=302)
        rc = generate_route_config_v2(cfg)
        vh = _vhost_for(rc, "m.iomio.io")
        apex_route = next(r for r in vh["routes"]
                           if r["match"].get("path") == "/")
        self.assertEqual(apex_route["redirect"]["path_redirect"], "/apps")
        self.assertEqual(apex_route["redirect"]["response_code"], "FOUND")

    def test_apex_service_action_forwards_to_cluster(self) -> None:
        cfg = RoutingConfigV2(gateway_host="m.iomio.io")
        cfg.apex = ApexConfig(action=ApexAction.SERVICE, target="homepage")
        rc = generate_route_config_v2(cfg)
        vh = _vhost_for(rc, "m.iomio.io")
        apex_route = next(r for r in vh["routes"]
                           if r["match"].get("path") == "/")
        self.assertEqual(apex_route["route"]["cluster"], "service_homepage")

    def test_apex_static_emits_direct_response(self) -> None:
        cfg = RoutingConfigV2(gateway_host="m.iomio.io")
        cfg.apex = ApexConfig(action=ApexAction.STATIC,
                              target="<h1>welcome</h1>")
        rc = generate_route_config_v2(cfg)
        vh = _vhost_for(rc, "m.iomio.io")
        apex_route = next(r for r in vh["routes"]
                           if r["match"].get("path") == "/")
        self.assertEqual(apex_route["direct_response"]["status"], 200)
        self.assertIn("welcome", apex_route["direct_response"]["body"]["inline_string"])


class TestScenario5CatchAll(unittest.TestCase):
    """§1 #5: m.iomio.io/wrong-url → ??? (catch-all)."""

    def test_default_404_emits_no_route(self) -> None:
        # Default catch-all = 404 with no custom body. Envoy returns
        # 404 implicitly when no route matches, so the explicit rule
        # is omitted.
        cfg = RoutingConfigV2(gateway_host="m.iomio.io")
        cfg.apex = ApexConfig(action=ApexAction.NONE)
        cfg.catch_all = CatchAllConfig(action=CatchAllAction.NOT_FOUND)
        rc = generate_route_config_v2(cfg)
        # No gateway vhost at all — apex is NONE, catch_all is implicit 404.
        with self.assertRaises(AssertionError):
            _vhost_for(rc, "m.iomio.io")

    def test_custom_404_body_emits_direct_response(self) -> None:
        cfg = RoutingConfigV2(gateway_host="m.iomio.io")
        cfg.catch_all = CatchAllConfig(
            action=CatchAllAction.NOT_FOUND,
            custom_404_body="<h1>not found</h1>",
        )
        rc = generate_route_config_v2(cfg)
        vh = _vhost_for(rc, "m.iomio.io")
        ca = vh["routes"][-1]
        self.assertEqual(ca["direct_response"]["status"], 404)
        self.assertIn("not found", ca["direct_response"]["body"]["inline_string"])

    def test_redirect_action_emits_path_redirect(self) -> None:
        cfg = RoutingConfigV2(gateway_host="m.iomio.io")
        cfg.catch_all = CatchAllConfig(action=CatchAllAction.REDIRECT,
                                        target="/apps", code=302)
        rc = generate_route_config_v2(cfg)
        vh = _vhost_for(rc, "m.iomio.io")
        ca = vh["routes"][-1]
        self.assertEqual(ca["match"], {"prefix": "/"})
        self.assertEqual(ca["redirect"]["path_redirect"], "/apps")

    def test_block_action_emits_444_direct_response(self) -> None:
        cfg = RoutingConfigV2(gateway_host="m.iomio.io")
        cfg.catch_all = CatchAllConfig(action=CatchAllAction.BLOCK)
        rc = generate_route_config_v2(cfg)
        vh = _vhost_for(rc, "m.iomio.io")
        ca = vh["routes"][-1]
        self.assertEqual(ca["direct_response"]["status"], 444)


class TestScenario6PathAlias(unittest.TestCase):
    """§1 #6: m.iomio.io/app/jellyfin → /app/jf.

    Path aliases must preserve the suffix: /app/jellyfin/movies must
    redirect to /app/jf/movies, not bare /app/jf.
    """

    def test_path_alias_uses_path_separated_prefix_match(self) -> None:
        cfg = RoutingConfigV2(gateway_host="m.iomio.io")
        cfg.path_aliases.append(PathAlias(from_path="/app/jellyfin",
                                          to_path="/app/jf", code=301))
        rc = generate_route_config_v2(cfg)
        vh = _vhost_for(rc, "m.iomio.io")
        alias_route = next(r for r in vh["routes"]
                            if r.get("redirect", {}).get("prefix_rewrite") == "/app/jf")
        self.assertEqual(alias_route["match"], {"path_separated_prefix": "/app/jellyfin"})
        self.assertEqual(alias_route["redirect"]["response_code"], "MOVED_PERMANENTLY")

    def test_redirect_uses_prefix_rewrite_to_preserve_suffix(self) -> None:
        # The whole point of using prefix_rewrite over path_redirect:
        # /app/jellyfin/movies/123 → /app/jf/movies/123 (suffix kept).
        cfg = RoutingConfigV2(gateway_host="m.iomio.io")
        cfg.path_aliases.append(PathAlias(from_path="/app/jellyfin",
                                          to_path="/app/jf", code=301))
        rc = generate_route_config_v2(cfg)
        vh = _vhost_for(rc, "m.iomio.io")
        alias_route = vh["routes"][0]  # path_aliases come first
        # Must be prefix_rewrite (preserves suffix), NOT path_redirect.
        self.assertIn("prefix_rewrite", alias_route["redirect"])
        self.assertNotIn("path_redirect", alias_route["redirect"])


class TestScenario7MultiplePathAliases(unittest.TestCase):
    """§1 #7: multiple path aliases coexisting."""

    def test_multiple_aliases_emitted_in_order(self) -> None:
        cfg = RoutingConfigV2(gateway_host="m.iomio.io")
        cfg.path_aliases.append(PathAlias(from_path="/app/media-stack-ui",
                                          to_path="/app/ui", code=301))
        cfg.path_aliases.append(PathAlias(from_path="/app/jellyfin",
                                          to_path="/app/jf", code=301))
        rc = generate_route_config_v2(cfg)
        vh = _vhost_for(rc, "m.iomio.io")
        # Aliases sort by from_path for stable diffs.
        alias_prefixes = [
            r["match"].get("path_separated_prefix")
            for r in vh["routes"]
            if "path_separated_prefix" in r.get("match", {})
        ]
        self.assertEqual(alias_prefixes,
                          ["/app/jellyfin", "/app/media-stack-ui"])


class TestHostnameAliases(unittest.TestCase):
    """Hostname aliases (HostEntry.aliases) emit a separate vhost
    that redirects to the canonical."""

    def test_alias_redirects_to_canonical(self) -> None:
        cfg = RoutingConfigV2(gateway_host="m.iomio.io")
        cfg.hosts.append(HostEntry(role="media_server", service_id="jellyfin",
                                    canonical="jf.iomio.io",
                                    aliases=["jellyfin.iomio.io"]))
        rc = generate_route_config_v2(cfg)
        # Two vhosts for the media_server: canonical + alias-redirect.
        vh_alias = _vhost_for(rc, "jellyfin.iomio.io")
        self.assertEqual(vh_alias["routes"][0]["redirect"]["host_redirect"],
                          "jf.iomio.io")
        self.assertEqual(vh_alias["routes"][0]["redirect"]["response_code"],
                          "MOVED_PERMANENTLY")

    def test_canonical_vhost_not_a_redirect(self) -> None:
        cfg = RoutingConfigV2(gateway_host="m.iomio.io")
        cfg.hosts.append(HostEntry(role="media_server", service_id="jellyfin",
                                    canonical="jf.iomio.io",
                                    aliases=["jellyfin.iomio.io"]))
        rc = generate_route_config_v2(cfg)
        vh_canon = _vhost_for(rc, "jf.iomio.io")
        # Canonical forwards, not redirects.
        self.assertIn("route", vh_canon["routes"][0])
        self.assertNotIn("redirect", vh_canon["routes"][0])


class TestRouteOrderingPrecedence(unittest.TestCase):
    """The whole-file ordering invariant — locked by R-4 ratchet but
    asserted here too: path_aliases > path_prefix > apex > catch_all."""

    def test_ordering_matches_design_doc(self) -> None:
        cfg = RoutingConfigV2(gateway_host="m.iomio.io")
        cfg.hosts.append(HostEntry(role="dashboard", service_id="homepage",
                                    canonical="m.iomio.io",
                                    path_prefix="/apps"))
        cfg.path_aliases.append(PathAlias(from_path="/app/jellyfin",
                                          to_path="/app/jf"))
        cfg.apex = ApexConfig(action=ApexAction.REDIRECT, target="/apps")
        cfg.catch_all = CatchAllConfig(action=CatchAllAction.REDIRECT, target="/apps")
        rc = generate_route_config_v2(cfg)
        vh = _vhost_for(rc, "m.iomio.io")
        # Indices of each route class:
        kinds: list[str] = []
        for r in vh["routes"]:
            m = r.get("match", {})
            if "path_separated_prefix" in m:
                kinds.append("alias")
            elif "path" in m and m["path"] == "/":
                kinds.append("apex")
            elif m.get("prefix") == "/":
                kinds.append("catch_all")
            elif m.get("prefix"):
                kinds.append("path_prefix")
            else:
                kinds.append("unknown")
        # alias < path_prefix < apex < catch_all
        order_value = {"alias": 0, "path_prefix": 1, "apex": 2, "catch_all": 3}
        for i in range(len(kinds) - 1):
            self.assertLessEqual(
                order_value[kinds[i]], order_value[kinds[i + 1]],
                f"Route at index {i} ({kinds[i]}) cannot precede {kinds[i+1]}"
                f" — full kinds: {kinds}",
            )


class TestDeterministicOutput(unittest.TestCase):
    """Same input → same dict. Locks byte-stable behaviour for R-5."""

    def test_repeat_calls_match(self) -> None:
        cfg = RoutingConfigV2(gateway_host="m.iomio.io")
        cfg.hosts.append(HostEntry(role="r1", service_id="s1", canonical="a.x"))
        cfg.hosts.append(HostEntry(role="r2", service_id="s2", canonical="b.x"))
        a = generate_route_config_v2(cfg)
        b = generate_route_config_v2(cfg)
        self.assertEqual(a, b)

    def test_host_order_in_input_doesnt_change_output(self) -> None:
        # Hosts get sorted by canonical inside the emitter, so
        # operator-side reordering doesn't churn the route_config.
        cfg1 = RoutingConfigV2(gateway_host="m.iomio.io")
        cfg1.hosts.append(HostEntry(role="r1", service_id="s1", canonical="a.x"))
        cfg1.hosts.append(HostEntry(role="r2", service_id="s2", canonical="b.x"))
        cfg2 = RoutingConfigV2(gateway_host="m.iomio.io")
        cfg2.hosts.append(HostEntry(role="r2", service_id="s2", canonical="b.x"))
        cfg2.hosts.append(HostEntry(role="r1", service_id="s1", canonical="a.x"))
        self.assertEqual(generate_route_config_v2(cfg1),
                          generate_route_config_v2(cfg2))


class TestRedirectCodes(unittest.TestCase):
    """Map HTTP redirect codes to Envoy's enum."""

    def test_301_maps_to_moved_permanently(self) -> None:
        cfg = RoutingConfigV2(gateway_host="m.iomio.io")
        cfg.path_aliases.append(PathAlias(from_path="/a", to_path="/b", code=301))
        rc = generate_route_config_v2(cfg)
        vh = _vhost_for(rc, "m.iomio.io")
        self.assertEqual(vh["routes"][0]["redirect"]["response_code"],
                          "MOVED_PERMANENTLY")

    def test_302_maps_to_found(self) -> None:
        cfg = RoutingConfigV2(gateway_host="m.iomio.io")
        cfg.path_aliases.append(PathAlias(from_path="/a", to_path="/b", code=302))
        rc = generate_route_config_v2(cfg)
        vh = _vhost_for(rc, "m.iomio.io")
        self.assertEqual(vh["routes"][0]["redirect"]["response_code"], "FOUND")

    def test_308_maps_to_permanent_redirect(self) -> None:
        cfg = RoutingConfigV2(gateway_host="m.iomio.io")
        cfg.path_aliases.append(PathAlias(from_path="/a", to_path="/b", code=308))
        rc = generate_route_config_v2(cfg)
        vh = _vhost_for(rc, "m.iomio.io")
        self.assertEqual(vh["routes"][0]["redirect"]["response_code"],
                          "PERMANENT_REDIRECT")

    def test_unknown_code_falls_back_to_found(self) -> None:
        cfg = RoutingConfigV2(gateway_host="m.iomio.io")
        cfg.path_aliases.append(PathAlias(from_path="/a", to_path="/b", code=999))
        rc = generate_route_config_v2(cfg)
        vh = _vhost_for(rc, "m.iomio.io")
        self.assertEqual(vh["routes"][0]["redirect"]["response_code"], "FOUND")


class TestTopLevelStructure(unittest.TestCase):
    """The dict shape Envoy expects."""

    def test_top_level_keys(self) -> None:
        cfg = RoutingConfigV2(gateway_host="m.iomio.io")
        cfg.hosts.append(HostEntry(role="r", service_id="s", canonical="x.y"))
        rc = generate_route_config_v2(cfg)
        self.assertEqual(rc["name"], "main")
        self.assertIn("virtual_hosts", rc)
        self.assertIsInstance(rc["virtual_hosts"], list)

    def test_empty_config_emits_no_vhosts(self) -> None:
        cfg = RoutingConfigV2(gateway_host="")  # no gateway, no hosts
        rc = generate_route_config_v2(cfg)
        self.assertEqual(rc["virtual_hosts"], [])

    def test_cluster_name_format(self) -> None:
        # Locked: clusters are named ``service_<id>`` to match the
        # legacy generator. Changing this would require coordinated
        # cluster-builder updates.
        cfg = RoutingConfigV2(gateway_host="m.iomio.io")
        cfg.hosts.append(HostEntry(role="r", service_id="my-service", canonical="x.y"))
        rc = generate_route_config_v2(cfg)
        vh = _vhost_for(rc, "x.y")
        self.assertEqual(vh["routes"][0]["route"]["cluster"], "service_my-service")


if __name__ == "__main__":
    unittest.main()
