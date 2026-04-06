"""Built-in auth provider spec for Authelia."""

PROVIDER_KEY = "authelia"
DEFAULT_MIDDLEWARE = "authelia@docker"
COMPOSE_SERVICE_NAMES: tuple[str, ...] = ("authelia",)
