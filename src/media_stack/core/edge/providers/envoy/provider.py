"""Built-in edge provider spec for Envoy.

Envoy uses the same traefik-style Docker labels as a normalized
intermediate format.  The envoy-config-init service parses these labels
from docker-compose.yml and converts them to Envoy listener/cluster YAML
via EnvoyDynamicConfigService.  Redirect templates are omitted because
Envoy handles path rewriting natively in its route config.
"""

PROVIDER_KEY = "envoy"

ROUTER_SERVICE_NAMES: tuple[str, ...] = ("envoy",)

COMPOSE_LABEL_SPEC: dict[str, str] = {
    "enable_label_key": "traefik.enable",
    "router_label_prefix": "traefik.http.routers.",
    "service_label_prefix": "traefik.http.services.",
    "middleware_label_prefix": "traefik.http.middlewares.",
    "router_rule_key_template": "traefik.http.routers.{router_name}.rule",
    "router_service_key_template": "traefik.http.routers.{router_name}.service",
    "router_middleware_key_template": "traefik.http.routers.{router_name}.middlewares",
    "strip_prefix_key_template": (
        "traefik.http.middlewares.{middleware_name}.stripprefix.prefixes"
    ),
    "path_rule_template": "Host(`{gateway_host}`) && PathPrefix(`{path_prefix}`)",
    "media_server_rule_key_template": "traefik.http.routers.{service_name}.rule",
    "direct_host_rule_template": "Host(`{direct_host}`)",
}
