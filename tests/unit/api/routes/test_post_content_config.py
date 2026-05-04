"""Tests for ``api/routes/post_content_config.py`` (ADR-0007 Phase 2 wave 6).

One test class per route + per writer / repository / service
collaborator, plus a routing-integration sanity check that pins
auto-discovery for all six POST paths through the production
``DefaultDispatcher``, plus the explicit no-lazy-cache anti-pattern
guard.

Mocking strategy mirrors ``test_post_config_writes.py``:

* Each writer / service is constructed-by-hand for the unit tests
  so the route methods exercise real instance methods (not Mocks)
  wherever possible. Keeps assertions about the collaboration
  shape, not the Mock plumbing.
* Cross-cutting concerns (CSRF, audit log) are enforced upstream
  by ``server.py`` wrappers and aren't re-tested here; we DO pin
  that ``handler.action_trigger`` is threaded through correctly,
  because that's the load-bearing seam between the route module
  and the action queue.
* The routing-integration test asserts the six paths land in the
  registry — guard against any future refactor accidentally
  dropping a handler.

The ``MockControllerHandler`` in ``_helpers.py`` covers the GET
side (no ``_read_json_body`` / ``action_trigger``); we extend it
here with ``_PostHandler`` so the POST routes have the surface
they need — same shape as ``test_post_config_writes.py``.
"""

from __future__ import annotations

import json
import urllib.error as _urllib_error
from typing import Any
from unittest.mock import MagicMock
from urllib.request import Request as _TestRequest

from media_stack.api.routing import DispatchOutcome
from media_stack.api.routes.post_content_config import (
    ContentConfigPostRoutes,
    CustomFormatImporter,
    CustomServiceCreator,
    DownloadClientSettingsWriter,
    LivetvProbeService,
    LivetvSourceWriter,
    QualityProfileToggleService,
)
from tests.unit.api.routes._helpers import (
    MockControllerHandler,
    RouteDispatchHarness,
)


class _PostHandler(MockControllerHandler):
    """``MockControllerHandler`` + POST-side surface (``_read_json_body``
    + ``action_trigger``). Same shape as the wave-5 helper in
    ``test_post_config_writes.py``.
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
    routes: ContentConfigPostRoutes,
    method_name: str,
    *,
    body_json: Any = None,
    action_trigger: Any = None,
) -> _PostHandler:
    """Invoke a route method directly on a constructed
    ``ContentConfigPostRoutes`` instance with a configured handler.
    Bypasses the Router so each unit test exercises just the route
    method + its collaborators.
    """
    handler = _PostHandler(
        path=f"/dummy/{method_name}",
        body_json=body_json,
        action_trigger=action_trigger,
    )
    getattr(routes, method_name)(handler)
    return handler


# ---------------------------------------------------------------------------
# /api/custom-formats/import
# ---------------------------------------------------------------------------


class TestCustomFormatsImportRoute:

    def test_imports_custom_formats_when_body_complete(self) -> None:
        preset_stub = MagicMock()
        preset_stub.import_trash_custom_formats.return_value = {
            "imported": 5, "skipped": 0, "errors": [],
        }
        routes = ContentConfigPostRoutes(
            custom_format_importer=CustomFormatImporter(
                preset_service=preset_stub,
            ),
        )
        handler = _dispatch_post(
            routes,
            "handle_custom_formats_import",
            body_json={
                "service": "sonarr",
                "index_url": "https://example.test/cf.json",
            },
        )
        assert handler.captured.status == 200
        body = json.loads(handler.captured.body)
        assert body["imported"] == 5
        preset_stub.import_trash_custom_formats.assert_called_once_with(
            "sonarr", "https://example.test/cf.json",
        )

    def test_returns_400_when_service_missing(self) -> None:
        preset_stub = MagicMock()
        routes = ContentConfigPostRoutes(
            custom_format_importer=CustomFormatImporter(
                preset_service=preset_stub,
            ),
        )
        handler = _dispatch_post(
            routes,
            "handle_custom_formats_import",
            body_json={"index_url": "https://example.test/cf.json"},
        )
        assert handler.captured.status == 400
        body = json.loads(handler.captured.body)
        assert body == {"error": "service and index_url required"}
        preset_stub.import_trash_custom_formats.assert_not_called()

    def test_returns_400_when_index_url_missing(self) -> None:
        preset_stub = MagicMock()
        routes = ContentConfigPostRoutes(
            custom_format_importer=CustomFormatImporter(
                preset_service=preset_stub,
            ),
        )
        handler = _dispatch_post(
            routes,
            "handle_custom_formats_import",
            body_json={"service": "sonarr"},
        )
        assert handler.captured.status == 400
        preset_stub.import_trash_custom_formats.assert_not_called()


# ---------------------------------------------------------------------------
# /api/custom-service
# ---------------------------------------------------------------------------


class TestCustomServiceRoute:

    def test_registers_custom_service(self) -> None:
        config_stub = MagicMock()
        config_stub.add_custom_service.return_value = {
            "status": "registered", "service_id": "myservice",
        }
        routes = ContentConfigPostRoutes(
            custom_service_creator=CustomServiceCreator(
                config_service=config_stub,
            ),
        )
        body = {
            "service_id": "myservice", "name": "My Service",
            "category": "management", "port": 8080,
        }
        handler = _dispatch_post(
            routes, "handle_custom_service", body_json=body,
        )
        assert handler.captured.status == 200
        config_stub.add_custom_service.assert_called_once_with(body)

    def test_empty_body_returns_400(self) -> None:
        config_stub = MagicMock()
        routes = ContentConfigPostRoutes(
            custom_service_creator=CustomServiceCreator(
                config_service=config_stub,
            ),
        )
        handler = _dispatch_post(
            routes, "handle_custom_service", body_json={},
        )
        assert handler.captured.status == 400
        body = json.loads(handler.captured.body)
        assert body == {"error": "JSON body required"}
        config_stub.add_custom_service.assert_not_called()


# ---------------------------------------------------------------------------
# /api/download-client-settings
# ---------------------------------------------------------------------------


class TestDownloadClientSettingsRoute:

    def test_writes_download_client_settings(self) -> None:
        content_stub = MagicMock()
        content_stub.update_download_client_settings.return_value = {
            "status": "updated",
        }
        routes = ContentConfigPostRoutes(
            download_client_writer=DownloadClientSettingsWriter(
                content_service=content_stub,
            ),
        )
        body = {"torrent": {"max_active_downloads": 5}}
        handler = _dispatch_post(
            routes, "handle_download_client_settings", body_json=body,
        )
        assert handler.captured.status == 200
        content_stub.update_download_client_settings.assert_called_once_with(body)

    def test_empty_body_still_calls_service(self) -> None:
        # Service layer owns the validation; route is a thin
        # adapter. Mirrors the legacy-handler behaviour byte-for-byte.
        content_stub = MagicMock()
        content_stub.update_download_client_settings.return_value = {
            "error": "schema validation failed",
        }
        routes = ContentConfigPostRoutes(
            download_client_writer=DownloadClientSettingsWriter(
                content_service=content_stub,
            ),
        )
        handler = _dispatch_post(
            routes, "handle_download_client_settings", body_json={},
        )
        assert handler.captured.status == 200
        content_stub.update_download_client_settings.assert_called_once_with({})


# ---------------------------------------------------------------------------
# /api/livetv-sources
# ---------------------------------------------------------------------------


class TestLivetvSourcesPostRoute:

    def test_writes_sources_and_queues_configure_livetv(self) -> None:
        config_stub = MagicMock()
        config_stub.update_livetv_sources.return_value = {
            "status": "updated",
        }
        routes = ContentConfigPostRoutes(
            livetv_source_writer=LivetvSourceWriter(
                config_service=config_stub,
            ),
        )
        trigger = MagicMock()
        body = {
            "tuner_url": "https://example.test/iptv.m3u",
            "guide_url": "https://example.test/epg.xml",
            "load_all_tuners": True,
        }
        handler = _dispatch_post(
            routes,
            "handle_livetv_sources",
            body_json=body,
            action_trigger=trigger,
        )
        assert handler.captured.status == 200
        body_resp = json.loads(handler.captured.body)
        assert body_resp["action"] == "configure-livetv queued"
        # The kwargs match the legacy chain's call shape exactly —
        # tuners/guides default to None, scalar URLs default to "",
        # load_all_tuners passes through.
        config_stub.update_livetv_sources.assert_called_once_with(
            tuners=None,
            guides=None,
            tuner_url="https://example.test/iptv.m3u",
            guide_url="https://example.test/epg.xml",
            load_all_tuners=True,
        )
        trigger.assert_called_once_with("configure-livetv", {})

    def test_does_not_queue_when_writer_returns_error(self) -> None:
        config_stub = MagicMock()
        config_stub.update_livetv_sources.return_value = {
            "error": "invalid url",
        }
        routes = ContentConfigPostRoutes(
            livetv_source_writer=LivetvSourceWriter(
                config_service=config_stub,
            ),
        )
        trigger = MagicMock()
        handler = _dispatch_post(
            routes,
            "handle_livetv_sources",
            body_json={"tuner_url": "bogus"},
            action_trigger=trigger,
        )
        # Even on writer error the route returns 200 — the legacy
        # chain did this too; the body's ``error`` field signals the
        # failure to the dashboard.
        assert handler.captured.status == 200
        body = json.loads(handler.captured.body)
        assert body == {"error": "invalid url"}
        trigger.assert_not_called()

    def test_threads_tuners_and_guides_arrays(self) -> None:
        config_stub = MagicMock()
        config_stub.update_livetv_sources.return_value = {"status": "ok"}
        routes = ContentConfigPostRoutes(
            livetv_source_writer=LivetvSourceWriter(
                config_service=config_stub,
            ),
        )
        body = {
            "tuners": [{"url": "u1"}, {"url": "u2"}],
            "guides": [{"url": "g1"}],
        }
        _dispatch_post(
            routes,
            "handle_livetv_sources",
            body_json=body,
            action_trigger=MagicMock(),
        )
        kwargs = config_stub.update_livetv_sources.call_args.kwargs
        assert kwargs["tuners"] == [{"url": "u1"}, {"url": "u2"}]
        assert kwargs["guides"] == [{"url": "g1"}]


# ---------------------------------------------------------------------------
# /api/livetv-sources/probe
# ---------------------------------------------------------------------------


class _FakeProbeResponse:
    """Stand-in for ``http.client.HTTPResponse`` covering only the
    surface ``LivetvProbeService.probe`` reads: ``status``, ``headers``,
    ``read(n)``, plus context-manager ``__enter__``/``__exit__``.
    """

    def __init__(
        self, *, status: int, content_type: str, body: bytes,
    ) -> None:
        self.status = status
        self._body = body
        self.headers = {"Content-Type": content_type}

    def read(self, n: int) -> bytes:
        return self._body[:n]

    def __enter__(self) -> "_FakeProbeResponse":
        return self

    def __exit__(self, *_a: Any) -> None:
        return None


def _make_probe_service(
    *, response: Any | None = None, side_effect: Any = None,
) -> LivetvProbeService:
    """Build a ``LivetvProbeService`` with a fake ``urlopen`` so
    tests don't make real HTTP calls. The real urllib ``Request``
    is fine for tests; we only stub the opener.
    """
    fake_urlopen = MagicMock()
    if side_effect is not None:
        fake_urlopen.side_effect = side_effect
    else:
        fake_urlopen.return_value = response
    return LivetvProbeService(
        urlopen=fake_urlopen, request_factory=_TestRequest,
    )


class TestLivetvSourcesProbeRoute:

    def test_classifies_m3u_body(self) -> None:
        service = _make_probe_service(
            response=_FakeProbeResponse(
                status=206,
                content_type="application/vnd.apple.mpegurl; charset=utf-8",
                body=b"#EXTM3U\n#EXTINF:-1,Channel 1\nhttp://x\n",
            ),
        )
        routes = ContentConfigPostRoutes(livetv_probe_service=service)
        handler = _dispatch_post(
            routes,
            "handle_livetv_sources_probe",
            body_json={"url": "https://example.test/iptv.m3u"},
        )
        assert handler.captured.status == 200
        body = json.loads(handler.captured.body)
        assert body["ok"] is True
        assert body["kind"] == "m3u"
        assert body["status"] == 206
        # Content-Type is split on ``;`` and stripped.
        assert body["content_type"] == "application/vnd.apple.mpegurl"
        assert body["bytes"] > 0
        assert body["sample"].startswith("#EXTM3U")

    def test_classifies_xmltv_body(self) -> None:
        service = _make_probe_service(
            response=_FakeProbeResponse(
                status=200,
                content_type="application/xml",
                body=b"<?xml version=\"1.0\"?><tv><channel/></tv>",
            ),
        )
        routes = ContentConfigPostRoutes(livetv_probe_service=service)
        handler = _dispatch_post(
            routes,
            "handle_livetv_sources_probe",
            body_json={"url": "https://example.test/epg.xml"},
        )
        body = json.loads(handler.captured.body)
        assert body["ok"] is True
        assert body["kind"] == "xmltv"

    def test_unknown_body_marks_not_ok(self) -> None:
        service = _make_probe_service(
            response=_FakeProbeResponse(
                status=200,
                content_type="text/html",
                body=b"<html>Not an M3U or XMLTV</html>",
            ),
        )
        routes = ContentConfigPostRoutes(livetv_probe_service=service)
        handler = _dispatch_post(
            routes,
            "handle_livetv_sources_probe",
            body_json={"url": "https://example.test/page.html"},
        )
        body = json.loads(handler.captured.body)
        assert body["ok"] is False
        assert body["kind"] == "unknown"
        assert "doesn't look like" in body["error"]

    def test_http_error_collapses_to_error_envelope(self) -> None:
        http_err = _urllib_error.HTTPError(
            url="https://example.test/x",
            code=403,
            msg="Forbidden",
            hdrs=None,  # type: ignore[arg-type]
            fp=None,
        )
        service = _make_probe_service(side_effect=http_err)
        routes = ContentConfigPostRoutes(livetv_probe_service=service)
        handler = _dispatch_post(
            routes,
            "handle_livetv_sources_probe",
            body_json={"url": "https://example.test/x"},
        )
        body = json.loads(handler.captured.body)
        assert body["ok"] is False
        assert body["status"] == 403
        assert "HTTP 403" in body["error"]

    def test_url_error_collapses_to_error_envelope(self) -> None:
        service = _make_probe_service(
            side_effect=_urllib_error.URLError("dns timeout"),
        )
        routes = ContentConfigPostRoutes(livetv_probe_service=service)
        handler = _dispatch_post(
            routes,
            "handle_livetv_sources_probe",
            body_json={"url": "https://nope.invalid/"},
        )
        body = json.loads(handler.captured.body)
        assert body["ok"] is False
        assert "dns timeout" in body["error"]

    def test_missing_url_returns_400(self) -> None:
        # Sentinel: probe service should NOT be invoked when the
        # url field is missing — the route validates up front.
        fake_urlopen = MagicMock()
        service = LivetvProbeService(urlopen=fake_urlopen)
        routes = ContentConfigPostRoutes(livetv_probe_service=service)
        handler = _dispatch_post(
            routes, "handle_livetv_sources_probe", body_json={},
        )
        assert handler.captured.status == 400
        body = json.loads(handler.captured.body)
        assert body == {"error": "url required"}
        fake_urlopen.assert_not_called()

    def test_whitespace_only_url_returns_400(self) -> None:
        fake_urlopen = MagicMock()
        service = LivetvProbeService(urlopen=fake_urlopen)
        routes = ContentConfigPostRoutes(livetv_probe_service=service)
        handler = _dispatch_post(
            routes,
            "handle_livetv_sources_probe",
            body_json={"url": "   "},
        )
        assert handler.captured.status == 400
        fake_urlopen.assert_not_called()


# ---------------------------------------------------------------------------
# /api/quality-profiles/toggle
# ---------------------------------------------------------------------------


class TestQualityProfilesToggleRoute:

    def test_quality_branch_calls_toggle_quality(self) -> None:
        preset_stub = MagicMock()
        preset_stub.toggle_quality.return_value = {"status": "ok"}
        routes = ContentConfigPostRoutes(
            quality_toggle_service=QualityProfileToggleService(
                preset_service=preset_stub,
            ),
        )
        handler = _dispatch_post(
            routes,
            "handle_quality_profiles_toggle",
            body_json={
                "service": "sonarr",
                "profile_id": 5,
                "quality": "WEB-DL-1080p",
                "enabled": True,
            },
        )
        assert handler.captured.status == 200
        preset_stub.toggle_quality.assert_called_once_with(
            "sonarr", 5, "WEB-DL-1080p", True,
        )
        preset_stub.toggle_upgrade.assert_not_called()

    def test_upgrade_branch_calls_toggle_upgrade(self) -> None:
        preset_stub = MagicMock()
        preset_stub.toggle_upgrade.return_value = {"status": "ok"}
        routes = ContentConfigPostRoutes(
            quality_toggle_service=QualityProfileToggleService(
                preset_service=preset_stub,
            ),
        )
        handler = _dispatch_post(
            routes,
            "handle_quality_profiles_toggle",
            body_json={
                "service": "sonarr",
                "profile_id": 5,
                "upgradeAllowed": False,
            },
        )
        assert handler.captured.status == 200
        preset_stub.toggle_upgrade.assert_called_once_with(
            "sonarr", 5, False,
        )
        preset_stub.toggle_quality.assert_not_called()

    def test_neither_field_returns_400(self) -> None:
        preset_stub = MagicMock()
        routes = ContentConfigPostRoutes(
            quality_toggle_service=QualityProfileToggleService(
                preset_service=preset_stub,
            ),
        )
        handler = _dispatch_post(
            routes,
            "handle_quality_profiles_toggle",
            body_json={"service": "sonarr", "profile_id": 5},
        )
        assert handler.captured.status == 400
        body = json.loads(handler.captured.body)
        assert body == {"error": "quality or upgradeAllowed required"}
        preset_stub.toggle_quality.assert_not_called()
        preset_stub.toggle_upgrade.assert_not_called()

    def test_profile_id_coerced_to_int(self) -> None:
        # The legacy chain wrapped profile_id in int(...) — body
        # came in as string from form-encoded clients in some
        # legacy paths. Keep that coercion behaviour so we don't
        # regress on ad-hoc dashboard fetches.
        preset_stub = MagicMock()
        preset_stub.toggle_quality.return_value = {"status": "ok"}
        routes = ContentConfigPostRoutes(
            quality_toggle_service=QualityProfileToggleService(
                preset_service=preset_stub,
            ),
        )
        _dispatch_post(
            routes,
            "handle_quality_profiles_toggle",
            body_json={
                "service": "sonarr",
                "profile_id": "5",  # string, coerced
                "quality": "WEB-DL-1080p",
                "enabled": True,
            },
        )
        args = preset_stub.toggle_quality.call_args.args
        assert args[1] == 5  # int, not "5"
        assert isinstance(args[1], int)


# ---------------------------------------------------------------------------
# Auto-discovery + spec-parity sanity check
# ---------------------------------------------------------------------------


class TestRouterIntegration:
    """Pin auto-discovery + spec-parity for the wave-6 paths.
    Wires the *real* Router so a regression that drops a handler
    from the registry fires here before any per-route test does.
    """

    _EXPECTED = {
        "/api/custom-formats/import",
        "/api/custom-service",
        "/api/download-client-settings",
        "/api/livetv-sources",
        "/api/livetv-sources/probe",
        "/api/quality-profiles/toggle",
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

    def test_unsupported_verb_on_custom_service_returns_method_not_allowed(
        self,
    ) -> None:
        """``/api/custom-service`` is POST-only in the spec — a GET
        should hit ``METHOD_NOT_ALLOWED`` because the spec
        authoritatively says no GET.
        """
        harness = RouteDispatchHarness.with_default_router()
        outcome, _ = harness.try_dispatch("GET", "/api/custom-service")
        assert outcome == DispatchOutcome.METHOD_NOT_ALLOWED


# ---------------------------------------------------------------------------
# Lazy-cache resolver pattern — must NOT exist on this module's collaborators
# ---------------------------------------------------------------------------


class TestNoLazyCacheResolverPattern:
    """Anti-pattern guard: the writer / service classes must NOT
    cache a resolved-once attribute and reuse it across calls when
    no constructor-injected dep is set.

    Each collaborator either:
      a) holds a real constructor-injected dep, OR
      b) re-resolves the module-level reference on every call.

    No ``self._cached_<svc>`` attribute that gets set lazily and
    reused — that pattern shadows test patches applied AFTER the
    instance was constructed (which is the typical shape because
    the Router instantiates the route module at startup, before
    test patches run).
    """

    def test_collaborators_have_no_lazy_cache_attribute(self) -> None:
        for cls in (
            CustomFormatImporter,
            CustomServiceCreator,
            DownloadClientSettingsWriter,
            LivetvSourceWriter,
            LivetvProbeService,
            QualityProfileToggleService,
        ):
            instance = cls()
            attrs = {a for a in dir(instance) if a.startswith("_cached_")}
            assert attrs == set(), (
                f"{cls.__name__} grew a lazy-cache attribute: {attrs}"
            )

    def test_route_module_holds_collaborator_instances_directly(
        self,
    ) -> None:
        """The route module's six collaborator slots are stored as
        plain instance attributes (set in ``__init__``). No
        lazy-init / resolve-on-first-use shenanigans.
        """
        routes = ContentConfigPostRoutes()
        assert isinstance(routes._custom_formats, CustomFormatImporter)
        assert isinstance(routes._custom_services, CustomServiceCreator)
        assert isinstance(
            routes._download_settings, DownloadClientSettingsWriter,
        )
        assert isinstance(routes._livetv_writer, LivetvSourceWriter)
        assert isinstance(routes._livetv_probe, LivetvProbeService)
        assert isinstance(
            routes._quality_toggle, QualityProfileToggleService,
        )
