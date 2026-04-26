"""Media-integrity infrastructure — production wiring.

ADR-0002 Phase 16-E (cross-cutting media-integrity) — the
production factory that wires real adapters from the service
registry, plus any future side-effecty bootstrap helpers, lives
here. The factory is the only place that:

- Reads the live service registry (``api.services.registry``) to
  discover Servarr/Bazarr instances.
- Reads the environment for adapter API keys.
- Constructs concrete ``adapters.media_integrity`` instances.

Application use-cases are agnostic of all of that — they receive
already-constructed adapters via dependency injection.
"""
