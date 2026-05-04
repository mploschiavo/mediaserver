"""Tests for ``api/routes/branding_user.py`` (ADR-0007 Phase 2 wave 4).

Each test class owns one route. Each test invokes the production
Router via ``RouteDispatchHarness.with_default_router()`` — same
auto-discovery, same spec-parity check, same dispatch path used
in production.

The route module delegates through a constructor-injected
``ConfigServiceAdapter`` (Adapter pattern) and resolves
display-preferences via a ``DisplayPreferenceResolver`` (Strategy
pattern). End-to-end tests patch the underlying ``config_svc``
module-level reference on the route module so the production
auto-discovery path is exercised; pattern-level unit tests
construct the Strategy / Adapter directly with hand-rolled inputs.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

from tests.unit.api.routes._helpers import RouteDispatchHarness


class TestProfileRoute:
    """``GET /api/profile`` — bootstrap profile YAML mirror."""

    @patch("media_stack.api.routes.branding_user.config_svc")
    def test_returns_profile_payload(self, mock_config) -> None:
        mock_config.get_profile.return_value = {
            "profile": {"routing": "k8s"},
            "file": "/srv-config/profile.yaml",
            "moved_to_app_config": ["routing"],
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/profile")

        assert response.status == 200
        body = json.loads(response.body)
        assert body == {
            "profile": {"routing": "k8s"},
            "file": "/srv-config/profile.yaml",
            "moved_to_app_config": ["routing"],
        }
        mock_config.get_profile.assert_called_once_with()

    @patch("media_stack.api.routes.branding_user.config_svc")
    def test_passes_through_error_payload(self, mock_config) -> None:
        # The legacy handler returns whatever ``get_profile`` gives
        # back — including the ``error`` shape when the YAML is
        # missing. The route module shouldn't second-guess it.
        mock_config.get_profile.return_value = {
            "profile": None,
            "error": "profile.yaml not found",
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/profile")

        assert response.status == 200
        body = json.loads(response.body)
        assert body["profile"] is None
        assert body["error"] == "profile.yaml not found"


class TestDiscoveryListsRoute:
    """``GET /api/discovery-lists`` — TMDB/Trakt discovery feeds."""

    @patch("media_stack.api.routes.branding_user.config_svc")
    def test_returns_discovery_list_catalogue(self, mock_config) -> None:
        mock_config.get_discovery_lists.return_value = {
            "count": 2,
            "lists": [
                {"id": "tmdb-popular", "enabled": True},
                {"id": "trakt-trending", "enabled": False},
            ],
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/discovery-lists")

        assert response.status == 200
        body = json.loads(response.body)
        assert body == {
            "count": 2,
            "lists": [
                {"id": "tmdb-popular", "enabled": True},
                {"id": "trakt-trending", "enabled": False},
            ],
        }
        mock_config.get_discovery_lists.assert_called_once_with()

    @patch("media_stack.api.routes.branding_user.config_svc")
    def test_returns_empty_catalogue(self, mock_config) -> None:
        mock_config.get_discovery_lists.return_value = {
            "count": 0, "lists": [],
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/discovery-lists")

        assert response.status == 200
        assert json.loads(response.body) == {"count": 0, "lists": []}


class TestOnboardingRoute:
    """``GET /api/onboarding`` — first-run progress strip."""

    @patch("media_stack.api.routes.branding_user.config_svc")
    def test_returns_onboarding_progress(self, mock_config) -> None:
        mock_config.get_onboarding_status.return_value = {
            "is_first_run": False,
            "completed": 4,
            "total": 6,
            "progress_pct": 67,
            "steps": [
                {"id": "services_running", "status": "warn"},
                {"id": "libraries", "status": "ok"},
            ],
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/onboarding")

        assert response.status == 200
        body = json.loads(response.body)
        assert body["completed"] == 4
        assert body["total"] == 6
        assert body["progress_pct"] == 67
        mock_config.get_onboarding_status.assert_called_once_with()

    @patch("media_stack.api.routes.branding_user.config_svc")
    def test_first_run_state(self, mock_config) -> None:
        mock_config.get_onboarding_status.return_value = {
            "is_first_run": True, "completed": 0, "total": 6,
            "progress_pct": 0, "steps": [],
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/onboarding")

        assert response.status == 200
        body = json.loads(response.body)
        assert body["is_first_run"] is True
        assert body["completed"] == 0


class TestDisplayPreferencesRoute:
    """``GET /api/display-preferences`` — Jellyfin client knobs.

    The route lifts the legacy body and delegates default
    resolution to ``DisplayPreferenceResolver``. Tests mock the
    contracts loader on the route module so the resolver runs
    against deterministic input.
    """

    @patch(
        "media_stack.api.routes.branding_user._ContractsConfigLoader.load",
    )
    def test_returns_resolved_display_preferences(
        self, mock_load,
    ) -> None:
        mock_load.return_value = {
            "jellyfin_playback": {
                "display_preferences": {
                    "enabled": True,
                    "show_backdrop": False,
                    "custom_prefs": {"enableThemeVideos": True},
                    "per_library_prefs": {
                        "movies": {"SortBy": "DateCreated"},
                    },
                    "clients": ["emby", "jellyfin-web"],
                },
            },
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/display-preferences")

        assert response.status == 200
        body = json.loads(response.body)
        assert body == {
            "enabled": True,
            "show_backdrop": False,
            "custom_prefs": {"enableThemeVideos": True},
            "per_library_prefs": {
                "movies": {"SortBy": "DateCreated"},
            },
            "clients": ["emby", "jellyfin-web"],
        }

    @patch(
        "media_stack.api.routes.branding_user._ContractsConfigLoader.load",
    )
    def test_falls_back_to_defaults_when_section_missing(
        self, mock_load,
    ) -> None:
        # No jellyfin_playback section → resolver must hand back
        # the documented defaults instead of crashing on missing
        # keys. Pins the legacy chain's defaults verbatim.
        mock_load.return_value = {}
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/display-preferences")

        assert response.status == 200
        body = json.loads(response.body)
        assert body == {
            "enabled": True,
            "show_backdrop": True,
            "custom_prefs": {},
            "per_library_prefs": {},
            "clients": ["emby"],
        }


class TestDisplayPreferenceResolverPattern:
    """Strategy-level unit tests — construct the resolver directly
    with a hand-rolled cfg dict, no Router/handler scaffolding.
    Pins the resolver's default-resolution contract independently
    of the dispatch path.
    """

    def test_resolves_with_full_config(self) -> None:
        from media_stack.api.routes.branding_user import (
            DisplayPreferenceResolver,
        )
        cfg = {
            "jellyfin_playback": {
                "display_preferences": {
                    "enabled": False,
                    "show_backdrop": False,
                    "custom_prefs": {"a": 1},
                    "per_library_prefs": {"tv": {}},
                    "clients": ["jellyfin-web"],
                },
            },
        }
        out = DisplayPreferenceResolver().resolve(cfg)
        assert out["enabled"] is False
        assert out["show_backdrop"] is False
        assert out["custom_prefs"] == {"a": 1}
        assert out["per_library_prefs"] == {"tv": {}}
        assert out["clients"] == ["jellyfin-web"]

    def test_uses_documented_defaults_for_empty_cfg(self) -> None:
        from media_stack.api.routes.branding_user import (
            DisplayPreferenceResolver,
        )
        out = DisplayPreferenceResolver().resolve({})
        assert out == {
            "enabled": True,
            "show_backdrop": True,
            "custom_prefs": {},
            "per_library_prefs": {},
            "clients": ["emby"],
        }

    def test_partial_config_fills_per_key_defaults(self) -> None:
        from media_stack.api.routes.branding_user import (
            DisplayPreferenceResolver,
        )
        # Only ``enabled`` set; other keys must fall to defaults.
        cfg = {"jellyfin_playback": {"display_preferences": {
            "enabled": False,
        }}}
        out = DisplayPreferenceResolver().resolve(cfg)
        assert out["enabled"] is False
        assert out["show_backdrop"] is True  # default
        assert out["clients"] == ["emby"]    # default

    def test_returned_collections_are_independent_copies(self) -> None:
        # Defensive-copy check — mutating the resolver's output
        # must NOT bleed back into the input cfg or class-level
        # default constants. Catches the classic shared-default
        # foot-gun.
        from media_stack.api.routes.branding_user import (
            DisplayPreferenceResolver,
        )
        out_a = DisplayPreferenceResolver().resolve({})
        out_a["clients"].append("mutated")
        out_b = DisplayPreferenceResolver().resolve({})
        assert out_b["clients"] == ["emby"]


class TestConfigServiceAdapterPattern:
    """Adapter-level unit tests — construct with a mock service
    object and assert each method delegates 1:1.
    """

    def test_delegates_each_method_to_injected_service(self) -> None:
        from media_stack.api.routes.branding_user import (
            ConfigServiceAdapter,
        )
        mock = MagicMock()
        mock.get_profile.return_value = {"profile": "p"}
        mock.get_discovery_lists.return_value = {"lists": []}
        mock.get_onboarding_status.return_value = {"completed": 1}
        adapter = ConfigServiceAdapter(mock)
        assert adapter.get_profile() == {"profile": "p"}
        assert adapter.get_discovery_lists() == {"lists": []}
        assert adapter.get_onboarding_status() == {"completed": 1}
        mock.get_profile.assert_called_once_with()
        mock.get_discovery_lists.assert_called_once_with()
        mock.get_onboarding_status.assert_called_once_with()


class TestRouteModuleDependencyInjection:
    """Constructor-injection check — the route class accepts
    custom Adapter / Strategy / loader and uses them in place of
    the production defaults. Locks in the OO-discipline rule
    ("constructor-inject deps") so a future cleanup that drops a
    kwarg fails this test before any per-route assertion.
    """

    def test_accepts_custom_dependencies(self) -> None:
        from media_stack.api.routes.branding_user import (
            BrandingUserGetRoutes, DisplayPreferenceResolver,
        )

        mock_service = MagicMock()
        mock_service.get_profile.return_value = {"injected": True}
        mock_service.get_discovery_lists.return_value = {"injected": True}
        mock_service.get_onboarding_status.return_value = {"injected": True}

        class _StubLoader:
            def load(self) -> dict[str, Any]:
                return {}

        module = BrandingUserGetRoutes(
            config_service=mock_service,
            display_resolver=DisplayPreferenceResolver(),
            contracts_loader=_StubLoader(),
        )

        # Direct method-level smoke check; no Router involvement.
        captured: list[tuple[int, Any]] = []

        class _Handler:
            def _json_response(self, status: int, body: Any) -> None:
                captured.append((status, body))

        h = _Handler()
        module.handle_profile(h)
        module.handle_discovery_lists(h)
        module.handle_onboarding(h)
        module.handle_display_preferences(h)

        assert [s for s, _ in captured] == [200, 200, 200, 200]
        # First three came from the injected service, last from
        # the resolver running on the empty stub cfg → defaults.
        assert captured[0][1] == {"injected": True}
        assert captured[3][1]["clients"] == ["emby"]


class TestRoutingIntegration:
    """Pin auto-discovery + spec-parity behaviour for the
    branding-user domain. If a future change accidentally drops
    a handler from the registry, this fires before any per-route
    test does.
    """

    _EXPECTED_PATHS = {
        "/api/profile",
        "/api/discovery-lists",
        "/api/display-preferences",
        "/api/onboarding",
    }

    def test_all_branding_user_routes_registered(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        registered = {
            r.path
            for r in harness._dispatcher._router.registered_routes()
            if r.path in self._EXPECTED_PATHS
        }
        assert registered == self._EXPECTED_PATHS, (
            f"Missing branding-user routes: "
            f"{self._EXPECTED_PATHS - registered}"
        )

    def test_profile_post_does_not_fall_to_router_get(self) -> None:
        # The spec declares both GET + POST on /api/profile, but
        # this module only registers the GET. POST should NOT
        # match this module's handler — the dispatcher falls
        # through (NO_MATCH) to the legacy POST chain.
        from media_stack.api.routing import DispatchOutcome
        harness = RouteDispatchHarness.with_default_router()
        outcome, _ = harness.try_dispatch("POST", "/api/profile")
        # POST is in the spec for /api/profile; with no registered
        # POST handler in the router, the dispatcher returns
        # NO_MATCH so the legacy POST chain handles it.
        assert outcome == DispatchOutcome.NO_MATCH

    def test_post_to_discovery_lists_now_handled_by_router(
        self,
    ) -> None:
        # ADR-0007 Phase 2 wave 5 migrated POST /api/discovery-lists +
        # POST /api/display-preferences off the legacy chain into
        # routes/post_config_writes.py. The wave-3/4 boundary
        # (POST falls through to handlers_post) has shifted — the
        # Router now knows about both POST registrations. Asserting
        # the path appears in the route registry (rather than
        # dispatching) is the right boundary check for this GET-side
        # test module: it pins the migration without coupling to
        # wave-5's POST handler internals (_read_json_body etc.).
        harness = RouteDispatchHarness.with_default_router()
        registered = {
            (r.verb, r.path)
            for r in harness._dispatcher._router.registered_routes()
        }
        assert ("POST", "/api/discovery-lists") in registered
        assert ("POST", "/api/display-preferences") in registered

    def test_delete_to_onboarding_returns_method_not_allowed(
        self,
    ) -> None:
        # /api/onboarding is GET-only in the spec; DELETE → 405.
        from media_stack.api.routing import DispatchOutcome
        harness = RouteDispatchHarness.with_default_router()
        outcome, _ = harness.try_dispatch("DELETE", "/api/onboarding")
        assert outcome == DispatchOutcome.METHOD_NOT_ALLOWED
