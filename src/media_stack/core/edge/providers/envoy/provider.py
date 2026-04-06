"""Built-in edge provider spec for Envoy (stub wiring)."""

PROVIDER_KEY = "envoy"

# Compose router service alias reserved for Envoy runtime wiring.
ROUTER_SERVICE_NAMES: tuple[str, ...] = ("envoy",)

# Stub provider: Compose label transforms are intentionally empty for now.
COMPOSE_LABEL_SPEC: dict[str, str] = {}
