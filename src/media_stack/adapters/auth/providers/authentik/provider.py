"""Built-in auth provider spec for Authentik."""

PROVIDER_KEY = "authentik"
DEFAULT_MIDDLEWARE = "authentik@docker"
COMPOSE_SERVICE_NAMES: tuple[str, ...] = ("authentik", "authentik-worker")
