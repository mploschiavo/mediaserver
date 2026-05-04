"""Tests for ``api/routes/post_config_writes.py`` (ADR-0007 Phase 2 wave 5).

One test class per route + per writer/repository/service collaborator,
plus a routing-integration sanity check that pins auto-discovery for
all eight POST paths through the production ``DefaultDispatcher``.

Mocking strategy:

* Each writer/repo/service is constructed-by-hand for the unit
  tests so the route methods exercise real instance methods (not
  Mocks) wherever possible — keeps the assertions about the
  collaboration shape, not the Mock plumbing.
* Cross-cutting concerns (CSRF, audit log) are enforced upstream
  by the server.py wrappers and aren't re-tested here; we DO pin
  that ``handler.action_trigger`` is threaded through correctly,
  because that's the load-bearing seam between the route module
  and the action queue.
* The routing-integration test asserts the eight paths land in
  the registry — guard against any future refactor accidentally
  dropping a handler.

The ``MockControllerHandler`` in ``_helpers.py`` covers the GET
side (no ``_read_json_body`` / ``action_trigger``); we extend it
here with ``_PostHandler`` so the POST routes have the surface they
need.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

from media_stack.api.routing import DispatchOutcome
from media_stack.api.routes.post_config_writes import (
    BazarrLanguagesService,
    ConfigWritesPostRoutes,
    DiscoveryListsRepository,
    DisplayPreferenceWriter,
    DownloadCategoryWriter,
    LibraryConfigWriter,
    MetadataConfigWriter,
    RoutingConfigWriter,
)
from tests.unit.api.routes._helpers import (
    MockControllerHandler,
    RouteDispatchHarness,
)


class _PostHandler(MockControllerHandler):
    """``MockControllerHandler`` extended with the POST-side surface
    the route module reads: ``_read_json_body`` and the
    ``action_trigger`` callable.

    ``_read_json_body`` returns the constructor-supplied body dict
    directly; mirrors the production helper which decodes JSON off
    the socket but returns ``{}`` on missing/empty input. Tests pass
    ``body_json={}`` explicitly to exercise the
    ``"JSON body required"`` validation branch.
    """

    def __init__(
        self,
        *,
        path: str = "/",
        body_json: Any = None,
        action_trigger: Any = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(path=path, **kwargs)
        self._body_json = body_json
        self.action_trigger = action_trigger

    def _read_json_body(self) -> Any:
        # Mirror the production helper: empty/missing body -> {}.
        return {} if self._body_json is None else self._body_json


def _dispatch_post(
    routes: ConfigWritesPostRoutes,
    method_name: str,
    *,
    body_json: Any = None,
    action_trigger: Any = None,
) -> _PostHandler:
    """Invoke a route method directly on a constructed
    ``ConfigWritesPostRoutes`` instance with a configured handler.

    Bypasses the Router so each unit test exercises just the route
    method + its collaborators, with no auto-discovery noise.
    """
    handler = _PostHandler(
        path=f"/dummy/{method_name}",
        body_json=body_json,
        action_trigger=action_trigger,
    )
    getattr(routes, method_name)(handler)
    return handler


# ---------------------------------------------------------------------------
# /api/routing — v1 thin write
# ---------------------------------------------------------------------------


class TestRoutingV1Route:
    """``POST /api/routing`` — thin wrapper over
    ``config_svc.update_routing``.
    """

    def test_persists_v1_body_and_threads_action_trigger(self) -> None:
        config_stub = MagicMock()
        config_stub.update_routing.return_value = {
            "status": "updated",
            "changed": ["base_domain"],
            "routing": {"base_domain": "new.lan"},
        }
        routes = ConfigWritesPostRoutes(
            routing_writer=RoutingConfigWriter(config_service=config_stub),
        )
        trigger = MagicMock()
        handler = _dispatch_post(
            routes,
            "handle_routing",
            body_json={"base_domain": "new.lan"},
            action_trigger=trigger,
        )

        assert handler.captured.status == 200
        body = json.loads(handler.captured.body)
        assert body["status"] == "updated"
        config_stub.update_routing.assert_called_once_with(
            {"base_domain": "new.lan"}, trigger,
        )

    def test_empty_body_returns_400(self) -> None:
        routes = ConfigWritesPostRoutes()
        handler = _dispatch_post(routes, "handle_routing", body_json={})
        assert handler.captured.status == 400
        assert json.loads(handler.captured.body) == {
            "error": "JSON body required",
        }


# ---------------------------------------------------------------------------
# /api/routing/v2 — partial v2 update
# ---------------------------------------------------------------------------


_V1_ROUTING_FIXTURE = {
    "base_domain": "test.lan",
    "stack_subdomain": "media-stack",
    "gateway_host": "apps.media-stack.test.lan",
    "gateway_port": 80,
    "app_path_prefix": "/app",
    "strategy": "hybrid",
    "scheme": "",
    "internet_exposed": False,
    "direct_hosts": {"media_server": "jf.test.lan"},
}


def _make_config_svc_mock(
    *, v1: dict | None = None, media_server_id: str = "jellyfin",
) -> MagicMock:
    """Build a ``config_svc`` mock matching the surface the v2
    writer touches: ``get_routing`` + ``_profile.media_server_id`` +
    ``update_routing``."""
    mock = MagicMock()
    mock.get_routing.return_value = (
        v1 if v1 is not None else dict(_V1_ROUTING_FIXTURE)
    )
    mock._profile.media_server_id.return_value = media_server_id
    mock.update_routing.return_value = {"status": "updated"}
    return mock


class TestRoutingV2Route:
    """``POST /api/routing/v2`` — deep-merge + validate + split-persist.

    The v2 pipeline runs through the *real* migrator + validator so
    behaviour stays pinned to the production code path. We patch
    ``_persist_v2_overrides`` so the test doesn't write YAML to
    disk.
    """

    def test_returns_400_on_empty_body(self) -> None:
        routes = ConfigWritesPostRoutes()
        handler = _dispatch_post(routes, "handle_routing_v2", body_json={})
        assert handler.captured.status == 400

    def test_applies_partial_update_and_persists_v2_overrides(
        self,
    ) -> None:
        config_mock = _make_config_svc_mock()
        writer = RoutingConfigWriter(config_service=config_mock)
        with patch.object(writer, "_persist_v2_overrides") as mock_persist:
            routes = ConfigWritesPostRoutes(routing_writer=writer)
            trigger = MagicMock()
            handler = _dispatch_post(
                routes,
                "handle_routing_v2",
                body_json={"strategy": "path"},
                action_trigger=trigger,
            )

        assert handler.captured.status == 200
        body = json.loads(handler.captured.body)
        assert body["status"] == "ok"
        assert body["config"]["strategy"] == "path"
        # Both writes happened: v1-compat update_routing + the
        # additive v2 overrides write.
        config_mock.update_routing.assert_called_once()
        mock_persist.assert_called_once()
        # The v1-compat dict got threaded through with the merged
        # strategy.
        v1_compat_arg, trigger_arg = (
            config_mock.update_routing.call_args.args
        )
        assert v1_compat_arg["strategy"] == "path"
        assert trigger_arg is trigger

    def test_returns_422_when_validator_emits_errors(self) -> None:
        config_mock = _make_config_svc_mock()
        writer = RoutingConfigWriter(config_service=config_mock)
        fake_error = MagicMock()
        fake_error.code = "VR-CUSTOM"
        fake_error.field = "hosts[0]"
        fake_error.message = "test failure"
        fake_error.hint = "fix it"
        with patch(
            "media_stack.api.services.config.routing.validate_routing_config",
            return_value=[fake_error],
        ), patch.object(writer, "_persist_v2_overrides") as mock_persist:
            routes = ConfigWritesPostRoutes(routing_writer=writer)
            handler = _dispatch_post(
                routes,
                "handle_routing_v2",
                body_json={"strategy": "path"},
                action_trigger=MagicMock(),
            )

        assert handler.captured.status == 422
        body = json.loads(handler.captured.body)
        assert body["status"] == "validation_failed"
        assert body["validation"][0]["code"] == "VR-CUSTOM"
        # CRITICAL: validation failure must NOT touch the overrides
        # file or call update_routing.
        mock_persist.assert_not_called()
        config_mock.update_routing.assert_not_called()

    def test_pipeline_drift_returns_500(self) -> None:
        config_mock = _make_config_svc_mock()
        # Make get_routing raise a TypeError — sits inside the v2
        # pipeline narrowed-exception set, so the route should
        # convert to a 500 envelope rather than propagate.
        config_mock.get_routing.side_effect = TypeError("drift")
        writer = RoutingConfigWriter(config_service=config_mock)
        routes = ConfigWritesPostRoutes(routing_writer=writer)
        handler = _dispatch_post(
            routes,
            "handle_routing_v2",
            body_json={"strategy": "path"},
            action_trigger=MagicMock(),
        )
        assert handler.captured.status == 500
        assert "drift" in json.loads(handler.captured.body)["error"]


# ---------------------------------------------------------------------------
# /api/libraries — overwrite + queue configure-libraries
# ---------------------------------------------------------------------------


class TestLibrariesRoute:

    def test_persists_libraries_and_queues_configure_action(self) -> None:
        config_stub = MagicMock()
        config_stub.update_libraries.return_value = {"status": "updated"}
        routes = ConfigWritesPostRoutes(
            library_writer=LibraryConfigWriter(config_service=config_stub),
        )
        trigger = MagicMock()
        libs = [{"name": "Movies", "collection_type": "movies"}]
        handler = _dispatch_post(
            routes,
            "handle_libraries",
            body_json={"libraries": libs},
            action_trigger=trigger,
        )

        assert handler.captured.status == 200
        body = json.loads(handler.captured.body)
        assert body["action"] == "configure-libraries queued"
        config_stub.update_libraries.assert_called_once_with(libs)
        trigger.assert_called_once_with("configure-libraries", {})

    def test_libraries_must_be_array(self) -> None:
        routes = ConfigWritesPostRoutes()
        handler = _dispatch_post(
            routes,
            "handle_libraries",
            body_json={"libraries": "not-a-list"},
        )
        assert handler.captured.status == 400

    def test_no_action_queued_when_writer_returns_error(self) -> None:
        config_stub = MagicMock()
        config_stub.update_libraries.return_value = {
            "error": "disk full",
        }
        routes = ConfigWritesPostRoutes(
            library_writer=LibraryConfigWriter(config_service=config_stub),
        )
        trigger = MagicMock()
        handler = _dispatch_post(
            routes,
            "handle_libraries",
            body_json={"libraries": []},
            action_trigger=trigger,
        )
        assert handler.captured.status == 200
        body = json.loads(handler.captured.body)
        assert "action" not in body
        trigger.assert_not_called()


# ---------------------------------------------------------------------------
# /api/download-categories
# ---------------------------------------------------------------------------


class TestDownloadCategoriesRoute:

    def test_persists_categories_dict(self) -> None:
        config_stub = MagicMock()
        config_stub.update_download_categories.return_value = {
            "status": "updated",
        }
        routes = ConfigWritesPostRoutes(
            download_writer=DownloadCategoryWriter(
                config_service=config_stub,
            ),
        )
        cats = {"tv-sonarr": "/downloads/tv"}
        handler = _dispatch_post(
            routes,
            "handle_download_categories",
            body_json={"categories": cats},
        )
        assert handler.captured.status == 200
        config_stub.update_download_categories.assert_called_once_with(cats)

    def test_categories_must_be_object(self) -> None:
        routes = ConfigWritesPostRoutes()
        handler = _dispatch_post(
            routes,
            "handle_download_categories",
            body_json={"categories": ["not", "an", "object"]},
        )
        assert handler.captured.status == 400


# ---------------------------------------------------------------------------
# /api/metadata-settings
# ---------------------------------------------------------------------------


class TestMetadataSettingsRoute:

    def test_threads_language_and_country(self) -> None:
        config_stub = MagicMock()
        config_stub.update_metadata_settings.return_value = {
            "language": "en", "country": "US",
        }
        routes = ConfigWritesPostRoutes(
            metadata_writer=MetadataConfigWriter(
                config_service=config_stub,
            ),
        )
        handler = _dispatch_post(
            routes,
            "handle_metadata_settings",
            body_json={"language": "en", "country": "US"},
        )
        assert handler.captured.status == 200
        config_stub.update_metadata_settings.assert_called_once_with(
            "en", "US",
        )

    def test_missing_keys_default_to_empty_string(self) -> None:
        """The legacy chain passed empty strings when the body
        omitted the keys; preserve that contract so the writer's
        empty-string handling stays the operator-visible default.
        """
        config_stub = MagicMock()
        config_stub.update_metadata_settings.return_value = {}
        routes = ConfigWritesPostRoutes(
            metadata_writer=MetadataConfigWriter(
                config_service=config_stub,
            ),
        )
        handler = _dispatch_post(
            routes, "handle_metadata_settings", body_json={},
        )
        assert handler.captured.status == 200
        config_stub.update_metadata_settings.assert_called_once_with(
            "", "",
        )


# ---------------------------------------------------------------------------
# /api/discovery-lists
# ---------------------------------------------------------------------------


class TestDiscoveryListsRoute:

    def test_persists_lists_and_queues_bootstrap(self) -> None:
        config_stub = MagicMock()
        config_stub.update_discovery_lists.return_value = {
            "status": "updated",
        }
        routes = ConfigWritesPostRoutes(
            discovery_repository=DiscoveryListsRepository(
                config_service=config_stub,
            ),
        )
        trigger = MagicMock()
        lists = [{"id": "tmdb-popular", "enabled": True}]
        handler = _dispatch_post(
            routes,
            "handle_discovery_lists",
            body_json={"lists": lists},
            action_trigger=trigger,
        )
        assert handler.captured.status == 200
        body = json.loads(handler.captured.body)
        assert body["action"] == "bootstrap queued"
        config_stub.update_discovery_lists.assert_called_once_with(lists)
        trigger.assert_called_once_with("bootstrap", {})

    def test_lists_must_be_array(self) -> None:
        routes = ConfigWritesPostRoutes()
        handler = _dispatch_post(
            routes,
            "handle_discovery_lists",
            body_json={"lists": "not-a-list"},
        )
        assert handler.captured.status == 400


# ---------------------------------------------------------------------------
# /api/display-preferences
# ---------------------------------------------------------------------------


class TestDisplayPreferencesRoute:

    def _make_writer(
        self,
        *,
        media_server_id: str = "jellyfin",
        loaded_cfg: dict | None = None,
        save_result: dict | None = None,
    ) -> tuple[DisplayPreferenceWriter, MagicMock, MagicMock]:
        config_stub = MagicMock()
        config_stub._media_server_id.return_value = media_server_id
        loader = MagicMock(
            return_value=dict(loaded_cfg) if loaded_cfg else {},
        )
        saver = MagicMock(
            return_value=save_result if save_result is not None
            else {"status": "updated"},
        )
        writer = DisplayPreferenceWriter(
            config_service=config_stub,
            load_app_config=loader,
            save_app_config=saver,
        )
        return writer, loader, saver

    def test_returns_400_when_no_media_server(self) -> None:
        writer, _, saver = self._make_writer(media_server_id="")
        routes = ConfigWritesPostRoutes(display_preferences_writer=writer)
        handler = _dispatch_post(
            routes,
            "handle_display_preferences",
            body_json={"show_backdrop": True},
        )
        assert handler.captured.status == 400
        saver.assert_not_called()

    def test_merges_partial_body_into_playback_block(self) -> None:
        writer, loader, saver = self._make_writer(
            loaded_cfg={"playback": {"display_preferences": {
                "show_backdrop": False,
                "custom_prefs": {"existing": True},
            }}},
        )
        routes = ConfigWritesPostRoutes(display_preferences_writer=writer)
        trigger = MagicMock()
        handler = _dispatch_post(
            routes,
            "handle_display_preferences",
            body_json={
                "show_backdrop": True,
                "custom_prefs": {"new": "value"},
                "per_library_prefs": {"movies": {"SortBy": "DateCreated"}},
            },
            action_trigger=trigger,
        )

        assert handler.captured.status == 200
        body = json.loads(handler.captured.body)
        assert body["action"] == "configure-playback queued"
        loader.assert_called_once_with("jellyfin")
        # Saved cfg picked up the merge.
        saved_cfg = saver.call_args.args[1]
        dp = saved_cfg["playback"]["display_preferences"]
        assert dp["show_backdrop"] is True
        assert dp["custom_prefs"] == {"new": "value"}
        assert dp["per_library_prefs"] == {
            "movies": {"SortBy": "DateCreated"},
        }
        trigger.assert_called_once_with("configure-playback", {})

    def test_no_action_queued_when_save_returns_error(self) -> None:
        writer, _, _ = self._make_writer(
            save_result={"error": "disk full"},
        )
        routes = ConfigWritesPostRoutes(display_preferences_writer=writer)
        trigger = MagicMock()
        handler = _dispatch_post(
            routes,
            "handle_display_preferences",
            body_json={"show_backdrop": True},
            action_trigger=trigger,
        )
        body = json.loads(handler.captured.body)
        assert "action" not in body
        trigger.assert_not_called()


# ---------------------------------------------------------------------------
# /api/bazarr/subtitle-languages
# ---------------------------------------------------------------------------


class TestBazarrSubtitleLanguagesRoute:

    def _make_service(
        self, *, return_value: Any = None, side_effect: Any = None,
    ) -> tuple[BazarrLanguagesService, MagicMock]:
        proxy = MagicMock()
        if side_effect is not None:
            proxy.update_subtitle_languages.side_effect = side_effect
        else:
            proxy.update_subtitle_languages.return_value = (
                return_value if return_value is not None else {"ok": True}
            )
        return BazarrLanguagesService(proxy_module=proxy), proxy

    def test_missing_profile_id_returns_400(self) -> None:
        service, proxy = self._make_service()
        routes = ConfigWritesPostRoutes(bazarr_service=service)
        handler = _dispatch_post(
            routes,
            "handle_bazarr_subtitle_languages",
            body_json={"language_codes": ["en"]},
        )
        assert handler.captured.status == 400
        proxy.update_subtitle_languages.assert_not_called()

    def test_empty_language_codes_returns_400(self) -> None:
        service, proxy = self._make_service()
        routes = ConfigWritesPostRoutes(bazarr_service=service)
        handler = _dispatch_post(
            routes,
            "handle_bazarr_subtitle_languages",
            body_json={"profile_id": 1, "language_codes": []},
        )
        assert handler.captured.status == 400
        proxy.update_subtitle_languages.assert_not_called()

    def test_proxy_success_returns_200(self) -> None:
        service, proxy = self._make_service(
            return_value={"ok": True, "items": 2},
        )
        routes = ConfigWritesPostRoutes(bazarr_service=service)
        handler = _dispatch_post(
            routes,
            "handle_bazarr_subtitle_languages",
            body_json={
                "profile_id": 7,
                "language_codes": ["en", "es"],
                "forced": True,
                "hi": False,
            },
        )
        assert handler.captured.status == 200
        proxy.update_subtitle_languages.assert_called_once_with(
            7, ["en", "es"], forced=True, hi=False,
        )

    def test_proxy_returns_error_field_yields_502(self) -> None:
        service, _ = self._make_service(
            return_value={"error": "Bazarr unreachable"},
        )
        routes = ConfigWritesPostRoutes(bazarr_service=service)
        handler = _dispatch_post(
            routes,
            "handle_bazarr_subtitle_languages",
            body_json={
                "profile_id": 1, "language_codes": ["en"],
            },
        )
        assert handler.captured.status == 502

    def test_proxy_connection_error_yields_500(self) -> None:
        service, _ = self._make_service(
            side_effect=ConnectionError("bazarr down"),
        )
        routes = ConfigWritesPostRoutes(bazarr_service=service)
        handler = _dispatch_post(
            routes,
            "handle_bazarr_subtitle_languages",
            body_json={
                "profile_id": 1, "language_codes": ["en"],
            },
        )
        assert handler.captured.status == 500
        body = json.loads(handler.captured.body)
        assert "bazarr down" in body["error"]


# ---------------------------------------------------------------------------
# Auto-discovery + spec-parity sanity check
# ---------------------------------------------------------------------------


class TestRouterIntegration:
    """Pin auto-discovery + spec-parity for the wave-5 paths.

    Wires the *real* Router so a regression that drops a handler
    from the registry fires here before any per-route test does.
    """

    _EXPECTED = {
        "/api/routing",
        "/api/routing/v2",
        "/api/libraries",
        "/api/download-categories",
        "/api/metadata-settings",
        "/api/discovery-lists",
        "/api/display-preferences",
        "/api/bazarr/subtitle-languages",
    }

    def test_all_post_paths_registered(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        registered = {
            r.path
            for r in harness._dispatcher._router.registered_routes()
            if r.verb == "POST" and r.path in self._EXPECTED
        }
        assert registered == self._EXPECTED, (
            f"Missing POST routes: {self._EXPECTED - registered}"
        )

    def test_unsupported_verb_on_routing_returns_method_not_allowed(
        self,
    ) -> None:
        """Both GET (wave 4) + POST (wave 5) are now spec-declared
        for ``/api/routing``; a DELETE should hit ``METHOD_NOT_ALLOWED``
        because the spec authoritatively says no DELETE.
        """
        harness = RouteDispatchHarness.with_default_router()
        outcome, _ = harness.try_dispatch("DELETE", "/api/routing")
        assert outcome == DispatchOutcome.METHOD_NOT_ALLOWED


# ---------------------------------------------------------------------------
# Lazy-cache resolver pattern — must NOT exist on this module's collaborators
# ---------------------------------------------------------------------------


class TestNoLazyCacheResolverPattern:
    """Anti-pattern guard: the writer / repository / service classes
    must NOT cache a resolved-once attribute and reuse it across
    calls when no constructor-injected dep is set.

    Each collaborator either:
      a) holds a real constructor-injected dep, OR
      b) re-resolves the module-level reference on every call.

    No ``self._cached_<svc>`` attribute that gets set lazily and
    reused — that pattern shadows test patches applied AFTER the
    instance was constructed (which is the typical shape because
    the Router instantiates the route module at startup, before
    test patches run).
    """

    def test_writers_have_no_lazy_cache_attribute(self) -> None:
        for cls in (
            RoutingConfigWriter,
            LibraryConfigWriter,
            DownloadCategoryWriter,
            MetadataConfigWriter,
            DiscoveryListsRepository,
        ):
            instance = cls()
            # The constructor-stored dep is named ``_config_service``;
            # no ``_resolve_*`` / ``_cached_*`` attribute should exist
            # because the dep is set directly in __init__ to a real
            # reference (or override).
            attrs = {a for a in dir(instance) if a.startswith("_cached_")}
            assert attrs == set(), (
                f"{cls.__name__} grew a lazy-cache attribute: {attrs}"
            )
