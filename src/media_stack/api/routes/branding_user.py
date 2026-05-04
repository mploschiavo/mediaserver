"""Branding/user-preference GET routes (ADR-0007 Phase 2 wave 4).

Domain: per-user + per-deployment preferences that drive the
dashboard's first-run + Settings surfaces. Four routes migrated
off the ``handlers_get.handle()`` elif chain:

* ``GET /api/profile`` — bootstrap profile YAML + ``moved_to_app_config``
  list. Read-only mirror of the operator's deployment profile.
* ``GET /api/discovery-lists`` — TMDB / Trakt discovery feed config.
* ``GET /api/display-preferences`` — Jellyfin client display knobs
  (backdrops, home sections, per-library sort). Resolved from the
  contracts-loaded config, NOT browser-localStorage.
* ``GET /api/onboarding`` — first-run progress (services-running,
  api-keys-discovered, libraries-configured, etc.) for the
  Onboarding wizard.

Note — ``/api/branding`` and ``/api/discovery/popular-tv`` already
live in ``routes/brand_discovery.py``; this wave adds the four
above only.

Skipped — ``/api/bazarr/subtitle-config`` is in the legacy elif
chain at handlers_get.py:952 but has NO entry in
``contracts/api/openapi.yaml``. Per the Router's strict-on-mismatch
check (``_RouteCompiler._check_in_spec``), registering a path
absent from the spec raises ``RouterMisconfigured`` at startup.
The brief here is a routes-only migration ("DO NOT modify
routing/, handlers_*.py, server.py, other route modules"); the
bazarr-subtitle path needs an OpenAPI spec entry first, then a
follow-up wave can register it. Tracked as a follow-up — until
then the legacy elif chain continues to serve it.

Implementation choices, per Phase 2's "lift the body OR call the
helper — agent's choice based on what's cleanest" rule:

* ``/api/profile``, ``/api/discovery-lists``, ``/api/onboarding``
  delegate to one-line ``config_svc`` calls — no helper wrapper
  exists for them in ``handlers_get`` so the route methods invoke
  the service directly.
* ``/api/display-preferences`` LIFTS the legacy body. The legacy
  version reaches into ``_load_cfg_from_contracts`` and unpacks a
  jellyfin_playback.display_preferences sub-tree with explicit
  defaults; lifting (a) keeps the resolution logic visible at the
  route boundary, and (b) lets us name the per-key default
  resolution as a Strategy without dragging that into a free
  function. The Strategy is held as an instance attribute on the
  route module so test code can swap it (constructor-injection
  shape; not yet wired through the Router but documented for
  Phase 3 DI work).

Patterns named (per the repo's OO discipline):

* ``DisplayPreferenceResolver`` — Strategy. Resolves the per-key
  display-preferences config from a contracts-loaded mapping with
  documented defaults. The route method asks the resolver for the
  payload; the resolver isolates the "where defaults come from"
  logic from the route's "shape an HTTP response" logic.
* ``ConfigServiceAdapter`` — Adapter. Thin wrapper around the
  module-level ``config`` shim package. Lets the route module
  treat ``get_profile`` / ``get_discovery_lists`` /
  ``get_onboarding_status`` as injected methods on a service
  object rather than top-level imports — matches the project's
  "constructor-inject deps" rule.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any, Mapping, Protocol

from media_stack.api.routing import RouteModule, get
from media_stack.api.services import config as config_svc


class _ConfigService(Protocol):
    """Structural type for the Adapter target — names just the
    methods this route module needs from ``api.services.config``.
    Keeps the test surface small (mocks only fill in these three)
    and pins the contract this module relies on so a future split
    of ``config`` doesn't silently break the route bindings.
    """

    def get_profile(self) -> dict[str, Any]: ...
    def get_discovery_lists(self) -> dict[str, Any]: ...
    def get_onboarding_status(self) -> dict[str, Any]: ...


class ConfigServiceAdapter:
    """Adapter over the module-level ``api.services.config`` shim.

    Holds a reference to whichever object exposes the
    ``_ConfigService`` protocol — production passes the
    ``config_svc`` module itself; tests can pass a mock without
    monkeypatching imports. Constructor-injected so the
    route-module class doesn't reach into a global at call time.
    """

    def __init__(self, service: _ConfigService) -> None:
        self._service = service

    def get_profile(self) -> dict[str, Any]:
        return self._service.get_profile()

    def get_discovery_lists(self) -> dict[str, Any]:
        return self._service.get_discovery_lists()

    def get_onboarding_status(self) -> dict[str, Any]:
        return self._service.get_onboarding_status()


class DisplayPreferenceResolver:
    """Strategy that resolves the Jellyfin display-preferences
    payload from a contracts-loaded config mapping.

    The legacy chain inlined this:

        cfg = _load_cfg_from_contracts()
        playback = cfg.get("jellyfin_playback", {})
        dp = playback.get("display_preferences", {})
        # ... .get("enabled", True), .get("show_backdrop", True), ...

    Lifted into a class so:

      * The defaults are pinned in named class-level constants
        instead of being magic literals at the call site.
      * Tests can construct a resolver with a hand-rolled cfg dict
        instead of mocking ``_load_cfg_from_contracts``.
      * A future per-user preference strategy slots in as a
        sibling class without changing the route method.
    """

    # Pinned defaults — match the legacy chain's literals at
    # handlers_get.py:973-977. Renamed in one place if Jellyfin
    # client defaults shift.
    _DEFAULT_ENABLED = True
    _DEFAULT_SHOW_BACKDROP = True
    _DEFAULT_CLIENTS: tuple[str, ...] = ("emby",)
    _DEFAULT_CUSTOM_PREFS: Mapping[str, Any] = {}
    _DEFAULT_PER_LIBRARY_PREFS: Mapping[str, Any] = {}

    def resolve(self, cfg: Mapping[str, Any]) -> dict[str, Any]:
        playback = cfg.get("jellyfin_playback") or {}
        dp = playback.get("display_preferences") or {}
        return {
            "enabled": dp.get("enabled", self._DEFAULT_ENABLED),
            "show_backdrop": dp.get(
                "show_backdrop", self._DEFAULT_SHOW_BACKDROP,
            ),
            "custom_prefs": dict(
                dp.get("custom_prefs", self._DEFAULT_CUSTOM_PREFS),
            ),
            "per_library_prefs": dict(
                dp.get(
                    "per_library_prefs", self._DEFAULT_PER_LIBRARY_PREFS,
                ),
            ),
            "clients": list(
                dp.get("clients", self._DEFAULT_CLIENTS),
            ),
        }


class _ContractsConfigLoader:
    """Tiny indirection over ``_load_cfg_from_contracts``.

    The legacy chain calls the function inline; pulling it behind
    an instance method lets the route class accept a different
    loader at construction time (tests pass a closure, production
    uses the contracts-on-disk loader). The deferred import keeps
    the route module's import graph minimal at startup — same
    shape ``brand_discovery`` uses for its lazy dependencies.
    """

    def load(self) -> Mapping[str, Any]:
        from media_stack.services.jobs.framework import (
            _load_cfg_from_contracts,
        )
        return _load_cfg_from_contracts() or {}


class BrandingUserGetRoutes(RouteModule):
    """Branding + user-preference GET routes. The Router
    auto-discovers + instantiates this class + walks its tagged
    methods at startup.

    Constructor accepts optional dependency overrides so tests can
    swap the Adapter / Strategy / loader without monkeypatching;
    production construction (zero-arg, by the Router's auto-
    instantiation) wires the defaults.
    """

    def __init__(
        self,
        *,
        config_service: _ConfigService | None = None,
        display_resolver: DisplayPreferenceResolver | None = None,
        contracts_loader: _ContractsConfigLoader | None = None,
    ) -> None:
        self._config = ConfigServiceAdapter(
            config_service if config_service is not None else config_svc,
        )
        self._display_resolver = (
            display_resolver
            if display_resolver is not None
            else DisplayPreferenceResolver()
        )
        self._contracts_loader = (
            contracts_loader
            if contracts_loader is not None
            else _ContractsConfigLoader()
        )

    @get("/api/profile")
    def handle_profile(self, handler: Any) -> None:
        """Return the parsed bootstrap profile YAML +
        ``moved_to_app_config`` list. Operator-facing — drives the
        Settings → Profile editor.
        """
        handler._json_response(
            HTTPStatus.OK, self._config.get_profile(),
        )

    @get("/api/discovery-lists")
    def handle_discovery_lists(self, handler: Any) -> None:
        """Return the configured discovery-list catalogue (TMDB /
        Trakt feeds with per-list enabled flags).
        """
        handler._json_response(
            HTTPStatus.OK, self._config.get_discovery_lists(),
        )

    @get("/api/onboarding")
    def handle_onboarding(self, handler: Any) -> None:
        """Return first-run onboarding progress (services-running,
        api-keys-discovered, libraries-configured, etc.). Powers
        the Onboarding wizard's progress strip.
        """
        handler._json_response(
            HTTPStatus.OK, self._config.get_onboarding_status(),
        )

    @get("/api/display-preferences")
    def handle_display_preferences(self, handler: Any) -> None:
        """Return the Jellyfin display-preferences config the
        controller pushes to clients (backdrops, home sections,
        per-library sort).

        Body lifted from the legacy chain; defaults + key list
        live on ``DisplayPreferenceResolver`` so the route only
        wires "load → resolve → respond".
        """
        cfg = self._contracts_loader.load()
        payload = self._display_resolver.resolve(cfg)
        handler._json_response(HTTPStatus.OK, payload)


__all__ = [
    "BrandingUserGetRoutes",
    "ConfigServiceAdapter",
    "DisplayPreferenceResolver",
]
