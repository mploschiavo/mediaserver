"""Tests for ``api/routes/content_lists.py`` (ADR-0007 Phase 2 wave 4).

One test class per route plus a ``LibrariesRepository`` class
covering the merge logic in isolation, plus a routing-integration
sanity check that pins auto-discovery + spec-parity for all five
paths through the production ``DefaultDispatcher``.

Mocking strategy:

* The four single-call routes delegate to ``content_svc`` — we
  patch the module-level reference on the route module so the
  assertions are about the route's contract (calls the right
  function, threads the response shape) rather than the service
  internals.
* ``/api/libraries`` flows through ``LibrariesRepository``. We
  test the route + the repo separately:
    - Route tests inject a stub repo via the ``ContentListsGetRoutes``
      constructor through a Router-level wiring patch — but the
      simplest route-level coverage is a patch on the module's
      ``content_svc`` + ``config_svc`` references, which the
      production repo defaults walk.
    - Repo tests construct ``LibrariesRepository`` directly with
      stub services — pure unit, no Router involvement. These
      cover the four ``source`` decision branches (live wins;
      configured-defaults; configured-profile; configured-unknown).
"""

from __future__ import annotations

import json
from unittest.mock import patch

from tests.unit.api.routes._helpers import RouteDispatchHarness


class TestLibrariesRoute:
    """``GET /api/libraries`` — merged live + configured library
    payload. The route delegates to ``LibrariesRepository`` whose
    default-arg constructor walks the route module's
    ``content_svc`` + ``config_svc`` module-level references; we
    patch at those import sites.
    """

    def test_returns_live_payload_when_jellyfin_has_libraries(
        self,
    ) -> None:
        live_libs = [
            {
                "name": "Movies",
                "collection_type": "movies",
                "paths": ["/media/movies"],
                "item_count": 1234,
            },
        ]
        configured_libs = [
            {
                "name": "Movies",
                "collection_type": "movies",
                "paths": ["/media/movies"],
            },
        ]
        with patch(
            "media_stack.api.routes.content_lists.content_svc"
        ) as mock_content, patch(
            "media_stack.api.routes.content_lists.config_svc"
        ) as mock_config:
            mock_content.get_jellyfin_libraries.return_value = {
                "libraries": live_libs,
            }
            mock_config.get_libraries.return_value = {
                "libraries": configured_libs,
                "source": "defaults",
                "media_server": "jellyfin",
            }
            harness = RouteDispatchHarness.with_default_router()
            response = harness.dispatch("GET", "/api/libraries")

        assert response.status == 200
        body = json.loads(response.body)
        assert body == {
            "live": live_libs,
            "configured": configured_libs,
            "source": "live",
            "media_server": "jellyfin",
        }

    def test_falls_back_to_configured_source_when_live_empty(
        self,
    ) -> None:
        configured_libs = [
            {
                "name": "TV Shows",
                "collection_type": "tvshows",
                "paths": ["/media/tv"],
            },
        ]
        with patch(
            "media_stack.api.routes.content_lists.content_svc"
        ) as mock_content, patch(
            "media_stack.api.routes.content_lists.config_svc"
        ) as mock_config:
            mock_content.get_jellyfin_libraries.return_value = {
                "libraries": [],
            }
            mock_config.get_libraries.return_value = {
                "libraries": configured_libs,
                "source": "profile",
                "media_server": "jellyfin",
            }
            harness = RouteDispatchHarness.with_default_router()
            response = harness.dispatch("GET", "/api/libraries")

        assert response.status == 200
        body = json.loads(response.body)
        assert body == {
            "live": [],
            "configured": configured_libs,
            "source": "profile",
            "media_server": "jellyfin",
        }

    def test_unknown_source_when_configured_omits_source(self) -> None:
        with patch(
            "media_stack.api.routes.content_lists.content_svc"
        ) as mock_content, patch(
            "media_stack.api.routes.content_lists.config_svc"
        ) as mock_config:
            mock_content.get_jellyfin_libraries.return_value = {
                "libraries": [],
            }
            mock_config.get_libraries.return_value = {
                "libraries": [],
                "media_server": "",
            }
            harness = RouteDispatchHarness.with_default_router()
            response = harness.dispatch("GET", "/api/libraries")

        assert response.status == 200
        body = json.loads(response.body)
        assert body["source"] == "unknown"
        assert body["live"] == []
        assert body["configured"] == []
        assert body["media_server"] == ""


class TestLibrariesRepository:
    """``LibrariesRepository.aggregate`` in isolation.

    Pure unit — we construct the repo with stub services so the
    merge rule is exercised without Router involvement. Covers
    the four ``source`` decision branches:

    1. live present -> ``source == "live"``
    2. live empty + configured ``defaults`` -> ``source == "defaults"``
    3. live empty + configured ``persisted`` -> ``source == "persisted"``
    4. live empty + configured missing source -> ``source == "unknown"``
    """

    class _StubContent:
        def __init__(self, libraries: list) -> None:
            self._libraries = libraries

        def get_jellyfin_libraries(self) -> dict:
            return {"libraries": self._libraries}

    class _StubConfig:
        def __init__(self, payload: dict) -> None:
            self._payload = payload

        def get_libraries(self) -> dict:
            return dict(self._payload)

    def test_live_branch_wins_when_libraries_present(self) -> None:
        from media_stack.api.routes.content_lists import (
            LibrariesRepository,
        )
        repo = LibrariesRepository(
            content_service=self._StubContent([{"name": "Movies"}]),
            config_service=self._StubConfig({
                "libraries": [{"name": "Movies"}],
                "source": "defaults",
                "media_server": "jellyfin",
            }),
        )
        result = repo.aggregate()
        assert result["source"] == "live"
        assert result["live"] == [{"name": "Movies"}]

    def test_defaults_passthrough_when_live_empty(self) -> None:
        from media_stack.api.routes.content_lists import (
            LibrariesRepository,
        )
        repo = LibrariesRepository(
            content_service=self._StubContent([]),
            config_service=self._StubConfig({
                "libraries": [],
                "source": "defaults",
                "media_server": "jellyfin",
            }),
        )
        assert repo.aggregate()["source"] == "defaults"

    def test_persisted_passthrough_when_live_empty(self) -> None:
        from media_stack.api.routes.content_lists import (
            LibrariesRepository,
        )
        repo = LibrariesRepository(
            content_service=self._StubContent([]),
            config_service=self._StubConfig({
                "libraries": [{"name": "Movies"}],
                "source": "persisted",
                "media_server": "jellyfin",
            }),
        )
        result = repo.aggregate()
        assert result["source"] == "persisted"
        assert result["configured"] == [{"name": "Movies"}]

    def test_unknown_when_configured_omits_source(self) -> None:
        from media_stack.api.routes.content_lists import (
            LibrariesRepository,
        )
        repo = LibrariesRepository(
            content_service=self._StubContent([]),
            config_service=self._StubConfig({"libraries": []}),
        )
        result = repo.aggregate()
        assert result["source"] == "unknown"
        assert result["media_server"] == ""


class TestRecentRoute:
    """``GET /api/recent`` — recently-added items per *arr service."""

    @patch("media_stack.api.routes.content_lists.content_svc")
    def test_returns_recent_payload(self, mock_content) -> None:
        mock_content.get_recent.return_value = {
            "recent": {
                "radarr": [{"title": "Movie A", "added": "2026-05-01"}],
                "sonarr": [],
            },
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/recent")

        assert response.status == 200
        body = json.loads(response.body)
        assert body == {
            "recent": {
                "radarr": [{"title": "Movie A", "added": "2026-05-01"}],
                "sonarr": [],
            },
        }
        mock_content.get_recent.assert_called_once_with()

    @patch("media_stack.api.routes.content_lists.content_svc")
    def test_returns_empty_when_no_services_configured(
        self, mock_content,
    ) -> None:
        mock_content.get_recent.return_value = {"recent": {}}
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/recent")

        assert response.status == 200
        assert json.loads(response.body) == {"recent": {}}


class TestImportListsRoute:
    """``GET /api/import-lists`` — per-*arr import-list catalogue."""

    @patch("media_stack.api.routes.content_lists.content_svc")
    def test_returns_lists_per_service(self, mock_content) -> None:
        mock_content.get_import_lists.return_value = {
            "lists": {
                "sonarr": [
                    {
                        "id": 1,
                        "name": "Trakt Popular",
                        "enabled": True,
                        "listType": "trakt",
                    },
                ],
                "radarr": [
                    {
                        "id": 1,
                        "name": "IMDb Top 250",
                        "enabled": True,
                        "listType": "imdb",
                    },
                ],
            },
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/import-lists")

        assert response.status == 200
        body = json.loads(response.body)
        assert set(body["lists"]) == {"sonarr", "radarr"}
        mock_content.get_import_lists.assert_called_once_with()

    @patch("media_stack.api.routes.content_lists.content_svc")
    def test_preserves_nullable_enabled_field(
        self, mock_content,
    ) -> None:
        """``enabled`` is null in arr APIs when the list exists but
        the operator hasn't toggled it either way. Round-trip
        intact — UI maps null -> indeterminate tri-state."""
        mock_content.get_import_lists.return_value = {
            "lists": {
                "sonarr": [
                    {
                        "id": 7,
                        "name": "Untoggled",
                        "enabled": None,
                        "listType": "trakt",
                    },
                ],
            },
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/import-lists")

        body = json.loads(response.body)
        assert body["lists"]["sonarr"][0]["enabled"] is None


class TestImportListsAllRoute:
    """``GET /api/import-lists-all`` — aggregated across every *arr."""

    @patch("media_stack.api.routes.content_lists.content_svc")
    def test_returns_aggregated_payload(self, mock_content) -> None:
        mock_content.get_all_import_lists.return_value = {
            "lists": {
                "radarr": [
                    {
                        "id": 1,
                        "name": "TMDb Trending Movies",
                        "listType": "tmdb",
                        "enabled": False,
                    },
                ],
                "sonarr": [
                    {
                        "id": 1,
                        "name": "TMDb Popular TV",
                        "listType": "tmdb",
                        "enabled": True,
                    },
                ],
            },
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/import-lists-all")

        assert response.status == 200
        body = json.loads(response.body)
        assert body == {
            "lists": {
                "radarr": [
                    {
                        "id": 1,
                        "name": "TMDb Trending Movies",
                        "listType": "tmdb",
                        "enabled": False,
                    },
                ],
                "sonarr": [
                    {
                        "id": 1,
                        "name": "TMDb Popular TV",
                        "listType": "tmdb",
                        "enabled": True,
                    },
                ],
            },
        }
        mock_content.get_all_import_lists.assert_called_once_with()


class TestQualityProfilesRoute:
    """``GET /api/quality-profiles`` — bare-path profile catalogue.

    The parameterized sibling ``/api/quality-profiles/{service}``
    is registered by ``routes/indexers_quality.py``; this test
    module asserts the BARE path lives here.
    """

    @patch("media_stack.api.routes.content_lists.content_svc")
    def test_returns_profile_catalogue(self, mock_content) -> None:
        mock_content.get_quality_profiles.return_value = {
            "profiles": {
                "sonarr": [
                    {"id": 1, "name": "HD-1080p"},
                    {"id": 4, "name": "Ultra-HD"},
                ],
                "radarr": [
                    {"id": 1, "name": "HD-1080p"},
                ],
            },
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/quality-profiles")

        assert response.status == 200
        body = json.loads(response.body)
        assert body == {
            "profiles": {
                "sonarr": [
                    {"id": 1, "name": "HD-1080p"},
                    {"id": 4, "name": "Ultra-HD"},
                ],
                "radarr": [
                    {"id": 1, "name": "HD-1080p"},
                ],
            },
        }
        mock_content.get_quality_profiles.assert_called_once_with()

    @patch("media_stack.api.routes.content_lists.content_svc")
    def test_returns_empty_payload_when_no_services(
        self, mock_content,
    ) -> None:
        mock_content.get_quality_profiles.return_value = {"profiles": {}}
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/quality-profiles")

        assert response.status == 200
        assert json.loads(response.body) == {"profiles": {}}


class TestRoutingIntegration:
    """Pin auto-discovery + spec-parity for the content-lists
    domain. If a future change accidentally drops a handler from
    the registry, this fires before any per-route test does.
    """

    def test_all_content_lists_routes_registered(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        expected = {
            "/api/libraries",
            "/api/recent",
            "/api/import-lists",
            "/api/import-lists-all",
            "/api/quality-profiles",
        }
        registered = {
            r.path
            for r in harness._dispatcher._router.registered_routes()
            if r.path in expected
        }
        assert registered == expected, (
            f"Missing content-lists routes: {expected - registered}"
        )

    def test_parameterized_quality_profiles_stays_in_indexers_quality(
        self,
    ) -> None:
        """Wave 4 explicitly migrates ONLY the bare
        ``/api/quality-profiles`` path. The
        ``/api/quality-profiles/{service}`` parameterized sibling
        stays in ``indexers_quality.py``. Both must coexist —
        guard against either disappearing.
        """
        harness = RouteDispatchHarness.with_default_router()
        registered = {
            r.path
            for r in harness._dispatcher._router.registered_routes()
        }
        assert "/api/quality-profiles" in registered
        assert "/api/quality-profiles/{service}" in registered

    def test_post_to_libraries_falls_through_to_legacy_chain(
        self,
    ) -> None:
        """``/api/libraries`` has a POST counterpart in the
        OpenAPI spec, but Phase 2 wave 4 only migrates the GET.
        POST is a spec-declared verb, so the Router returns
        ``NO_MATCH`` (NOT 405) so the legacy ``handlers_post``
        chain can serve it. This guards the migration boundary —
        if we accidentally start advertising POST as 405,
        anything trying to update libraries breaks.
        """
        from media_stack.api.routing import DispatchOutcome

        harness = RouteDispatchHarness.with_default_router()
        outcome, _ = harness.try_dispatch("POST", "/api/libraries")
        assert outcome == DispatchOutcome.NO_MATCH

    def test_unsupported_verb_on_recent_returns_method_not_allowed(
        self,
    ) -> None:
        """``/api/recent`` has only GET in the spec, so any other
        verb is a real 405 (the spec declares the path but not
        the verb). Counterpart to the libraries POST test —
        proves the Router still emits 405 when it can answer
        authoritatively.
        """
        from media_stack.api.routing import DispatchOutcome

        harness = RouteDispatchHarness.with_default_router()
        outcome, _ = harness.try_dispatch("POST", "/api/recent")
        assert outcome == DispatchOutcome.METHOD_NOT_ALLOWED
