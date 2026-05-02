import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

# Underscore-prefixed helpers aren't re-exported through the
# core/platforms star-shim; import from the canonical adapter
# module directly.
from media_stack.adapters.compose.services.edge_route_graph import (  # noqa: E402
    ComposeEdgeRouteGraphRender,
    ComposeEdgeRouteGraphService,
    _coerce_scalar,
    _normalize_token,
    _set_nested,
    _split_csv,
    _truthy,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TRAEFIK_PROVIDER_SPEC = {
    "enable_label_key": "traefik.enable",
    "router_label_prefix": "traefik.http.routers.",
    "service_label_prefix": "traefik.http.services.",
    "middleware_label_prefix": "traefik.http.middlewares.",
}


def _make_service(
    *,
    label_service: mock.Mock | None = None,
    spec_resolver: mock.Mock | None = None,
    provider_spec: dict | None = None,
) -> ComposeEdgeRouteGraphService:
    if label_service is None:
        label_service = mock.Mock()
        label_service.provider_spec.return_value = dict(
            provider_spec or _TRAEFIK_PROVIDER_SPEC
        )
        label_service.normalize_labels.side_effect = lambda svc_name, spec: dict(
            spec.get("labels") or {}
        )
    if spec_resolver is None:
        spec_resolver = mock.Mock()
        spec_resolver.container_name.side_effect = lambda name, spec: (
            spec.get("container_name") or f"project_{name}_1"
        )
    return ComposeEdgeRouteGraphService(
        label_service=label_service, spec_resolver=spec_resolver
    )


def _labels_for_service(
    name: str, host: str, port: int, *, enabled: bool = True, scheme: str = ""
) -> dict:
    labels = {
        "traefik.enable": "true" if enabled else "false",
        f"traefik.http.routers.{name}.rule": f"Host(`{host}`)",
        f"traefik.http.services.{name}.loadbalancer.server.port": str(port),
    }
    if scheme:
        labels[f"traefik.http.services.{name}.loadbalancer.server.scheme"] = scheme
    return labels


# ---------------------------------------------------------------------------
# Pure-function tests
# ---------------------------------------------------------------------------


class TruthyTests(unittest.TestCase):
    def test_true_values(self):
        for value in ("1", "true", "True", "TRUE", "yes", "Yes", "on", "ON", "y", "Y"):
            self.assertTrue(_truthy(value), f"Expected truthy: {value!r}")

    def test_false_values(self):
        for value in ("0", "false", "no", "off", "", None, "nope", "maybe"):
            self.assertFalse(_truthy(value), f"Expected falsy: {value!r}")

    def test_truthy_whitespace(self):
        self.assertTrue(_truthy("  true  "))
        self.assertFalse(_truthy("  false  "))


class SplitCsvTests(unittest.TestCase):
    def test_single_value(self):
        self.assertEqual(_split_csv("websecure"), ["websecure"])

    def test_multiple_values(self):
        self.assertEqual(_split_csv("web,websecure"), ["web", "websecure"])

    def test_whitespace_handling(self):
        self.assertEqual(_split_csv(" a , b , c "), ["a", "b", "c"])

    def test_empty_string(self):
        self.assertEqual(_split_csv(""), [])

    def test_none_value(self):
        self.assertEqual(_split_csv(None), [])


class CoerceScalarTests(unittest.TestCase):
    def test_integer(self):
        self.assertEqual(_coerce_scalar("42"), 42)

    def test_true(self):
        self.assertIs(_coerce_scalar("true"), True)

    def test_false(self):
        self.assertIs(_coerce_scalar("false"), False)

    def test_string(self):
        self.assertEqual(_coerce_scalar("hello"), "hello")

    def test_empty(self):
        self.assertEqual(_coerce_scalar(""), "")

    def test_none(self):
        self.assertEqual(_coerce_scalar(None), "")


class NormalizeTokenTests(unittest.TestCase):
    def test_known_renames(self):
        self.assertEqual(_normalize_token("certresolver"), "certResolver")
        self.assertEqual(_normalize_token("entrypoints"), "entryPoints")
        self.assertEqual(_normalize_token("insecureskipverify"), "insecureSkipVerify")
        self.assertEqual(_normalize_token("loadbalancer"), "loadBalancer")
        self.assertEqual(_normalize_token("passhostheader"), "passHostHeader")
        self.assertEqual(_normalize_token("redirectregex"), "redirectRegex")
        self.assertEqual(_normalize_token("stripprefix"), "stripPrefix")

    def test_case_insensitive_lookup(self):
        self.assertEqual(_normalize_token("CertResolver"), "certResolver")
        self.assertEqual(_normalize_token("ENTRYPOINTS"), "entryPoints")

    def test_passthrough(self):
        self.assertEqual(_normalize_token("rule"), "rule")
        self.assertEqual(_normalize_token("service"), "service")

    def test_empty(self):
        self.assertEqual(_normalize_token(""), "")

    def test_none(self):
        self.assertEqual(_normalize_token(None), "")


class SetNestedTests(unittest.TestCase):
    def test_simple_key(self):
        d: dict = {}
        _set_nested(d, "rule", "Host(`foo`)")
        self.assertEqual(d, {"rule": "Host(`foo`)"})

    def test_two_level(self):
        d: dict = {}
        _set_nested(d, "tls.certresolver", "letsencrypt")
        self.assertEqual(d, {"tls": {"certResolver": "letsencrypt"}})

    def test_three_level(self):
        d: dict = {}
        _set_nested(d, "loadbalancer.server.port", "8080")
        self.assertEqual(d, {"loadBalancer": {"server": {"port": "8080"}}})

    def test_empty_path(self):
        d: dict = {"existing": True}
        _set_nested(d, "", "value")
        self.assertEqual(d, {"existing": True})


# ---------------------------------------------------------------------------
# Static method tests
# ---------------------------------------------------------------------------


class DefaultServicePrefixTests(unittest.TestCase):
    def test_replaces_routers_with_services(self):
        result = ComposeEdgeRouteGraphService._default_service_prefix(
            "traefik.http.routers."
        )
        self.assertEqual(result, "traefik.http.services.")

    def test_no_routers_token(self):
        result = ComposeEdgeRouteGraphService._default_service_prefix(
            "custom.prefix."
        )
        self.assertEqual(result, "")

    def test_empty(self):
        result = ComposeEdgeRouteGraphService._default_service_prefix("")
        self.assertEqual(result, "")


class DefaultMiddlewarePrefixTests(unittest.TestCase):
    def test_replaces_routers_with_middlewares(self):
        result = ComposeEdgeRouteGraphService._default_middleware_prefix(
            "traefik.http.routers."
        )
        self.assertEqual(result, "traefik.http.middlewares.")

    def test_empty(self):
        result = ComposeEdgeRouteGraphService._default_middleware_prefix("")
        self.assertEqual(result, "")


class ParseLabelGroupTests(unittest.TestCase):
    def test_basic_grouping(self):
        labels = {
            "traefik.http.routers.sonarr.rule": "Host(`sonarr.local`)",
            "traefik.http.routers.sonarr.service": "sonarr",
            "traefik.http.routers.radarr.rule": "Host(`radarr.local`)",
        }
        result = ComposeEdgeRouteGraphService._parse_label_group(
            labels, "traefik.http.routers."
        )
        self.assertIn("sonarr", result)
        self.assertIn("radarr", result)
        self.assertEqual(result["sonarr"]["rule"], "Host(`sonarr.local`)")
        self.assertEqual(result["sonarr"]["service"], "sonarr")

    def test_no_matching_prefix(self):
        labels = {"other.label": "value"}
        result = ComposeEdgeRouteGraphService._parse_label_group(
            labels, "traefik.http.routers."
        )
        self.assertEqual(result, {})

    def test_empty_prefix(self):
        labels = {"foo.bar.baz": "value"}
        result = ComposeEdgeRouteGraphService._parse_label_group(labels, "")
        self.assertEqual(result, {})

    def test_key_without_dot_suffix(self):
        labels = {"traefik.http.routers.sonarr": "value"}
        result = ComposeEdgeRouteGraphService._parse_label_group(
            labels, "traefik.http.routers."
        )
        self.assertEqual(result, {})


# ---------------------------------------------------------------------------
# Render tests
# ---------------------------------------------------------------------------


class RenderEmptyTests(unittest.TestCase):
    def test_no_services(self):
        svc = _make_service()
        result = svc.render({})
        self.assertEqual(result.router_count, 0)
        self.assertEqual(result.service_count, 0)
        self.assertEqual(result.middleware_count, 0)

    def test_missing_enable_key_returns_empty(self):
        svc = _make_service(provider_spec={"router_label_prefix": "traefik.http.routers."})
        result = svc.render({"sonarr": {"labels": {"traefik.enable": "true"}}})
        self.assertEqual(result.router_count, 0)

    def test_missing_router_prefix_returns_empty(self):
        svc = _make_service(provider_spec={"enable_label_key": "traefik.enable"})
        result = svc.render({"sonarr": {"labels": {"traefik.enable": "true"}}})
        self.assertEqual(result.router_count, 0)

    def test_disabled_service_skipped(self):
        svc = _make_service()
        services = {
            "sonarr": {"labels": _labels_for_service("sonarr", "sonarr.local", 8989, enabled=False)}
        }
        result = svc.render(services)
        self.assertEqual(result.router_count, 0)
        self.assertEqual(result.service_count, 0)


class RenderSingleServiceTests(unittest.TestCase):
    def test_single_router_and_service(self):
        svc = _make_service()
        services = {
            "sonarr": {"labels": _labels_for_service("sonarr", "sonarr.local", 8989)}
        }
        result = svc.render(services)
        self.assertEqual(result.router_count, 1)
        self.assertEqual(result.service_count, 1)
        routers = result.payload["http"]["routers"]
        self.assertIn("sonarr", routers)
        self.assertEqual(routers["sonarr"]["rule"], "Host(`sonarr.local`)")

    def test_service_url_format(self):
        svc = _make_service()
        services = {
            "sonarr": {"labels": _labels_for_service("sonarr", "sonarr.local", 8989)}
        }
        result = svc.render(services)
        http_services = result.payload["http"]["services"]
        self.assertIn("sonarr", http_services)
        servers = http_services["sonarr"]["loadBalancer"]["servers"]
        self.assertEqual(len(servers), 1)
        self.assertEqual(servers[0]["url"], "http://project_sonarr_1:8989")

    def test_custom_scheme(self):
        svc = _make_service()
        services = {
            "sonarr": {
                "labels": _labels_for_service("sonarr", "sonarr.local", 8989, scheme="https")
            }
        }
        result = svc.render(services)
        url = result.payload["http"]["services"]["sonarr"]["loadBalancer"]["servers"][0]["url"]
        self.assertEqual(url, "https://project_sonarr_1:8989")

    def test_explicit_container_name(self):
        svc = _make_service()
        services = {
            "sonarr": {
                "container_name": "my-sonarr",
                "labels": _labels_for_service("sonarr", "sonarr.local", 8989),
            }
        }
        result = svc.render(services)
        url = result.payload["http"]["services"]["sonarr"]["loadBalancer"]["servers"][0]["url"]
        self.assertEqual(url, "http://my-sonarr:8989")

    def test_router_default_service_is_service_key(self):
        svc = _make_service()
        services = {
            "sonarr": {"labels": _labels_for_service("sonarr", "sonarr.local", 8989)}
        }
        result = svc.render(services)
        router = result.payload["http"]["routers"]["sonarr"]
        self.assertEqual(router["service"], "sonarr")


class RenderMultipleServicesTests(unittest.TestCase):
    def test_two_services(self):
        svc = _make_service()
        services = {
            "sonarr": {"labels": _labels_for_service("sonarr", "sonarr.local", 8989)},
            "radarr": {"labels": _labels_for_service("radarr", "radarr.local", 7878)},
        }
        result = svc.render(services)
        self.assertEqual(result.router_count, 2)
        self.assertEqual(result.service_count, 2)

    def test_sorted_output_keys(self):
        svc = _make_service()
        services = {
            "zzz": {"labels": _labels_for_service("zzz", "zzz.local", 9999)},
            "aaa": {"labels": _labels_for_service("aaa", "aaa.local", 1111)},
        }
        result = svc.render(services)
        router_keys = list(result.payload["http"]["routers"].keys())
        self.assertEqual(router_keys, ["aaa", "zzz"])
        service_keys = list(result.payload["http"]["services"].keys())
        self.assertEqual(service_keys, ["aaa", "zzz"])

    def test_mixed_enabled_disabled(self):
        svc = _make_service()
        services = {
            "sonarr": {"labels": _labels_for_service("sonarr", "sonarr.local", 8989, enabled=True)},
            "radarr": {"labels": _labels_for_service("radarr", "radarr.local", 7878, enabled=False)},
        }
        result = svc.render(services)
        self.assertEqual(result.router_count, 1)
        self.assertEqual(result.service_count, 1)


class RenderEntryPointsTests(unittest.TestCase):
    def test_entrypoints_csv_to_list(self):
        svc = _make_service()
        labels = _labels_for_service("sonarr", "sonarr.local", 8989)
        labels["traefik.http.routers.sonarr.entrypoints"] = "web,websecure"
        services = {"sonarr": {"labels": labels}}
        result = svc.render(services)
        router = result.payload["http"]["routers"]["sonarr"]
        self.assertEqual(router["entryPoints"], ["web", "websecure"])


class RenderTlsTests(unittest.TestCase):
    def test_tls_true(self):
        svc = _make_service()
        labels = _labels_for_service("sonarr", "sonarr.local", 8989)
        labels["traefik.http.routers.sonarr.tls"] = "true"
        services = {"sonarr": {"labels": labels}}
        result = svc.render(services)
        router = result.payload["http"]["routers"]["sonarr"]
        self.assertIs(router["tls"], True)

    def test_tls_false(self):
        svc = _make_service()
        labels = _labels_for_service("sonarr", "sonarr.local", 8989)
        labels["traefik.http.routers.sonarr.tls"] = "false"
        services = {"sonarr": {"labels": labels}}
        result = svc.render(services)
        router = result.payload["http"]["routers"]["sonarr"]
        self.assertIs(router["tls"], False)


class RenderMiddlewareTests(unittest.TestCase):
    def test_strip_prefix_middleware(self):
        svc = _make_service()
        labels = _labels_for_service("sonarr", "sonarr.local", 8989)
        labels["traefik.http.middlewares.sonarr-strip.stripprefix.prefixes"] = "/app/sonarr"
        services = {"sonarr": {"labels": labels}}
        result = svc.render(services)
        self.assertEqual(result.middleware_count, 1)
        mw = result.payload["http"]["middlewares"]["sonarr-strip"]
        self.assertEqual(mw["stripPrefix"]["prefixes"], ["/app/sonarr"])

    def test_middleware_scalar_field(self):
        svc = _make_service()
        labels = _labels_for_service("sonarr", "sonarr.local", 8989)
        labels["traefik.http.middlewares.redir.redirectregex.regex"] = "^http://(.*)"
        labels["traefik.http.middlewares.redir.redirectregex.replacement"] = "https://$1"
        labels["traefik.http.middlewares.redir.redirectregex.permanent"] = "true"
        services = {"sonarr": {"labels": labels}}
        result = svc.render(services)
        mw = result.payload["http"]["middlewares"]["redir"]
        self.assertEqual(mw["redirectRegex"]["regex"], "^http://(.*)")
        self.assertEqual(mw["redirectRegex"]["replacement"], "https://$1")
        self.assertIs(mw["redirectRegex"]["permanent"], True)


class RenderPassHostHeaderTests(unittest.TestCase):
    def test_pass_host_header_true(self):
        svc = _make_service()
        labels = _labels_for_service("sonarr", "sonarr.local", 8989)
        labels["traefik.http.services.sonarr.loadbalancer.passhostheader"] = "true"
        services = {"sonarr": {"labels": labels}}
        result = svc.render(services)
        lb = result.payload["http"]["services"]["sonarr"]["loadBalancer"]
        self.assertIs(lb["passHostHeader"], True)

    def test_pass_host_header_false(self):
        svc = _make_service()
        labels = _labels_for_service("sonarr", "sonarr.local", 8989)
        labels["traefik.http.services.sonarr.loadbalancer.passhostheader"] = "false"
        services = {"sonarr": {"labels": labels}}
        result = svc.render(services)
        lb = result.payload["http"]["services"]["sonarr"]["loadBalancer"]
        self.assertIs(lb["passHostHeader"], False)

    def test_no_pass_host_header_by_default(self):
        svc = _make_service()
        labels = _labels_for_service("sonarr", "sonarr.local", 8989)
        services = {"sonarr": {"labels": labels}}
        result = svc.render(services)
        lb = result.payload["http"]["services"]["sonarr"]["loadBalancer"]
        self.assertNotIn("passHostHeader", lb)


class RenderRouterServiceOwnerTests(unittest.TestCase):
    def test_router_explicit_service_name(self):
        svc = _make_service()
        labels = _labels_for_service("sonarr", "sonarr.local", 8989)
        labels["traefik.http.routers.sonarr.service"] = "custom-sonarr"
        labels["traefik.http.services.custom-sonarr.loadbalancer.server.port"] = "8989"
        services = {"sonarr": {"labels": labels}}
        result = svc.render(services)
        router = result.payload["http"]["routers"]["sonarr"]
        self.assertEqual(router["service"], "custom-sonarr")
        self.assertIn("custom-sonarr", result.payload["http"]["services"])

    def test_service_without_port_excluded(self):
        svc = _make_service()
        labels = {
            "traefik.enable": "true",
            "traefik.http.routers.sonarr.rule": "Host(`sonarr.local`)",
        }
        services = {"sonarr": {"labels": labels}}
        result = svc.render(services)
        self.assertEqual(result.router_count, 1)
        self.assertEqual(result.service_count, 0)


class RenderEmptyServiceNameSkipTests(unittest.TestCase):
    def test_empty_service_name_skipped(self):
        svc = _make_service()
        services = {"": {"labels": _labels_for_service("x", "x.local", 9999)}}
        result = svc.render(services)
        self.assertEqual(result.router_count, 0)


class RenderDataclassTests(unittest.TestCase):
    def test_render_result_is_frozen_dataclass(self):
        render = ComposeEdgeRouteGraphRender(
            payload={"http": {}}, router_count=1, service_count=2, middleware_count=3
        )
        self.assertEqual(render.router_count, 1)
        self.assertEqual(render.service_count, 2)
        self.assertEqual(render.middleware_count, 3)


class RenderCustomProviderSpecTests(unittest.TestCase):
    def test_derived_service_prefix_from_router_prefix(self):
        spec = {
            "enable_label_key": "traefik.enable",
            "router_label_prefix": "traefik.http.routers.",
        }
        svc = _make_service(provider_spec=spec)
        labels = _labels_for_service("sonarr", "sonarr.local", 8989)
        services = {"sonarr": {"labels": labels}}
        result = svc.render(services)
        self.assertEqual(result.service_count, 1)

    def test_derived_middleware_prefix_from_router_prefix(self):
        spec = {
            "enable_label_key": "traefik.enable",
            "router_label_prefix": "traefik.http.routers.",
        }
        svc = _make_service(provider_spec=spec)
        labels = _labels_for_service("sonarr", "sonarr.local", 8989)
        labels["traefik.http.middlewares.strip.stripprefix.prefixes"] = "/app"
        services = {"sonarr": {"labels": labels}}
        result = svc.render(services)
        self.assertEqual(result.middleware_count, 1)


class RenderMultipleRoutersPerServiceTests(unittest.TestCase):
    def test_two_routers_on_one_service(self):
        svc = _make_service()
        labels = {
            "traefik.enable": "true",
            "traefik.http.routers.sonarr-web.rule": "Host(`sonarr.local`)",
            "traefik.http.routers.sonarr-api.rule": "Host(`api.sonarr.local`)",
            "traefik.http.services.sonarr.loadbalancer.server.port": "8989",
        }
        services = {"sonarr": {"labels": labels}}
        result = svc.render(services)
        self.assertEqual(result.router_count, 2)
        self.assertIn("sonarr-web", result.payload["http"]["routers"])
        self.assertIn("sonarr-api", result.payload["http"]["routers"])


class RenderMiddlewaresListFieldTests(unittest.TestCase):
    def test_router_middlewares_as_csv(self):
        svc = _make_service()
        labels = _labels_for_service("sonarr", "sonarr.local", 8989)
        labels["traefik.http.routers.sonarr.middlewares"] = "strip,auth"
        services = {"sonarr": {"labels": labels}}
        result = svc.render(services)
        router = result.payload["http"]["routers"]["sonarr"]
        self.assertEqual(router["middlewares"], ["strip", "auth"])


class RenderInvalidPortTests(unittest.TestCase):
    def test_non_numeric_port_skipped(self):
        svc = _make_service()
        labels = {
            "traefik.enable": "true",
            "traefik.http.routers.sonarr.rule": "Host(`sonarr.local`)",
            "traefik.http.services.sonarr.loadbalancer.server.port": "notaport",
        }
        services = {"sonarr": {"labels": labels}}
        result = svc.render(services)
        self.assertEqual(result.service_count, 0)


class RenderNestedTlsCertResolverTests(unittest.TestCase):
    def test_tls_certresolver_nested(self):
        svc = _make_service()
        labels = _labels_for_service("sonarr", "sonarr.local", 8989)
        labels["traefik.http.routers.sonarr.tls.certresolver"] = "letsencrypt"
        services = {"sonarr": {"labels": labels}}
        result = svc.render(services)
        router = result.payload["http"]["routers"]["sonarr"]
        self.assertEqual(router["tls"]["certResolver"], "letsencrypt")


class LabelPrefixesTests(unittest.TestCase):
    def test_label_prefixes_uses_provider_spec(self):
        svc = _make_service()
        enable, router, service, middleware = svc._label_prefixes()
        self.assertEqual(enable, "traefik.enable")
        self.assertEqual(router, "traefik.http.routers.")
        self.assertEqual(service, "traefik.http.services.")
        self.assertEqual(middleware, "traefik.http.middlewares.")


if __name__ == "__main__":
    unittest.main()
