"""Built-in edge provider spec for Traefik label-based routing."""

PROVIDER_KEY = "traefik"

ROUTER_SERVICE_NAMES: tuple[str, ...] = ("traefik",)

COMPOSE_LABEL_SPEC: dict[str, str] = {
    "enable_label_key": "traefik.enable",
    "router_label_prefix": "traefik.http.routers.",
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
