"""Built-in edge provider spec for Traefik label-based routing."""

PROVIDER_KEY = "traefik"

ROUTER_SERVICE_NAMES: tuple[str, ...] = ("traefik",)

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
    "redirect_regex_key_template": (
        "traefik.http.middlewares.{middleware_name}.redirectregex.regex"
    ),
    "redirect_replacement_key_template": (
        "traefik.http.middlewares.{middleware_name}.redirectregex.replacement"
    ),
    "redirect_permanent_key_template": (
        "traefik.http.middlewares.{middleware_name}.redirectregex.permanent"
    ),
    "path_rule_template": "Host(`{gateway_host}`) && PathPrefix(`{path_prefix}`)",
    "path_redirect_regex_template": r"^https?://[^/:]+(:[0-9]+)?{path_prefix_regex}/?(.*)",
    "path_redirect_replacement_template": "{scheme}://{redirect_host}$1/$2",
    "media_server_rule_key_template": "traefik.http.routers.{service_name}.rule",
    "direct_host_rule_template": "Host(`{direct_host}`)",
}
