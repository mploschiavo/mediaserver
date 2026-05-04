"""Tests for ``api/routes/epg.py`` (ADR-0007 Phase 2 wave 3).

Each test class owns one route. Each test invokes the production
Router via ``RouteDispatchHarness.with_default_router()`` — same
auto-discovery, same spec-parity check, same dispatch path used
in production.

Patch targets:

* ``livetv-sources`` + ``iptv-countries`` route through
  ``config_svc`` shim functions imported at module top — patch
  the ``config_svc`` reference on the route module.
* ``epg-providers`` + ``epg-health`` route through
  ``media_stack.services.epg_provider_service`` symbols imported
  lazily inside the method — patch the original module's
  attribute (the module-level shim that the legacy chain also
  uses) so the lazy ``from … import …`` inside the method
  resolves to the mock.
* ``feed.xml`` calls ``metrics_svc.get_rss_feed`` and emits the
  result via ``_raw_response``. The test asserts content-type +
  parses the body to confirm valid XML.
"""

from __future__ import annotations

import json
from unittest.mock import patch
from xml.etree import ElementTree as ET

from tests.unit.api.routes._helpers import RouteDispatchHarness


class TestLivetvSourcesRoute:
    """``GET /api/livetv-sources`` — M3U tuner + EPG guide URL list."""

    @patch("media_stack.api.routes.epg.config_svc")
    def test_returns_livetv_sources(self, mock_config) -> None:
        mock_config.get_livetv_sources.return_value = {
            "tuners": [
                {"name": "US",
                 "url": "https://iptv-org.github.io/iptv/countries/us.m3u"},
            ],
            "guides": [
                {"name": "US",
                 "url": "https://iptv-epg.org/files/epg-us.xml"},
            ],
            "tuner_url":
                "https://iptv-org.github.io/iptv/countries/us.m3u",
            "guide_url": "https://iptv-epg.org/files/epg-us.xml",
            "load_all_tuners": False,
            "source": "defaults",
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/livetv-sources")

        assert response.status == 200
        body = json.loads(response.body)
        assert body["tuner_url"].endswith("us.m3u")
        assert body["guide_url"].endswith("epg-us.xml")
        assert body["load_all_tuners"] is False
        mock_config.get_livetv_sources.assert_called_once_with()

    @patch("media_stack.api.routes.epg.config_svc")
    def test_returns_empty_payload_when_unconfigured(
        self, mock_config,
    ) -> None:
        mock_config.get_livetv_sources.return_value = {
            "tuners": [], "guides": [],
            "tuner_url": "", "guide_url": "",
            "load_all_tuners": False, "source": "defaults",
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/livetv-sources")

        assert response.status == 200
        body = json.loads(response.body)
        assert body["tuners"] == []
        assert body["guides"] == []


class TestIptvCountriesRoute:
    """``GET /api/iptv-countries`` — IPTV country presets."""

    @patch("media_stack.api.routes.epg.config_svc")
    def test_returns_country_presets(self, mock_config) -> None:
        mock_config.get_iptv_countries.return_value = {
            "countries": [
                {"code": "us", "name": "United States",
                 "guide_url": "https://iptv-epg.org/files/epg-us.xml",
                 "tuner_url": ""},
                {"code": "gb", "name": "United Kingdom",
                 "guide_url": "https://iptv-epg.org/files/epg-gb.xml",
                 "tuner_url": ""},
            ],
            "source": "defaults",
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/iptv-countries")

        assert response.status == 200
        body = json.loads(response.body)
        assert body["source"] == "defaults"
        assert len(body["countries"]) == 2
        assert body["countries"][0]["code"] == "us"
        mock_config.get_iptv_countries.assert_called_once_with()

    @patch("media_stack.api.routes.epg.config_svc")
    def test_profile_source_flagged(self, mock_config) -> None:
        mock_config.get_iptv_countries.return_value = {
            "countries": [], "source": "profile",
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/iptv-countries")

        assert response.status == 200
        body = json.loads(response.body)
        assert body["source"] == "profile"


class TestEpgProvidersRoute:
    """``GET /api/epg-providers`` — enabled guide-provider list +
    cached health snapshot. The two symbols
    (``get_guide_providers`` + ``_load_health_cache``) are imported
    lazily inside the method out of
    ``media_stack.services.epg_provider_service``; we patch them
    on that module so the lazy import resolves to the mocks.
    """

    @patch("media_stack.services.epg_provider_service._load_health_cache")
    @patch("media_stack.services.epg_provider_service.get_guide_providers")
    def test_returns_providers_and_health(
        self, mock_get_providers, mock_load_health,
    ) -> None:
        mock_get_providers.return_value = [
            {"id": "iptv-epg", "name": "IPTV-EPG.org",
             "format": "xml", "priority": 1, "enabled": True,
             "url_template":
                 "https://iptv-epg.org/files/epg-{code}.xml"},
        ]
        mock_load_health.return_value = {
            "epg-pw:au": {
                "ok": True, "ts": 1777137742.6841,
                "url": "https://epg.pw/xmltv/epg_AU.xml.gz",
            },
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/epg-providers")

        assert response.status == 200
        body = json.loads(response.body)
        assert body["providers"][0]["id"] == "iptv-epg"
        assert "epg-pw:au" in body["health"]
        assert body["health"]["epg-pw:au"]["ok"] is True
        mock_get_providers.assert_called_once_with()
        mock_load_health.assert_called_once_with()

    @patch("media_stack.services.epg_provider_service._load_health_cache")
    @patch("media_stack.services.epg_provider_service.get_guide_providers")
    def test_returns_empty_health_when_no_probe_yet(
        self, mock_get_providers, mock_load_health,
    ) -> None:
        mock_get_providers.return_value = []
        mock_load_health.return_value = {}
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/epg-providers")

        assert response.status == 200
        body = json.loads(response.body)
        assert body == {"providers": [], "health": {}}


class TestEpgHealthRoute:
    """``GET /api/epg-health`` — synchronous probe of every
    enabled provider for every configured country.
    """

    @patch("media_stack.services.epg_provider_service.run_health_check")
    def test_returns_aggregate_counts_and_details(
        self, mock_run,
    ) -> None:
        mock_run.return_value = {
            "providers": 5, "countries": 34,
            "healthy": 81, "unhealthy": 25,
            "details": {
                "us": {"iptv-epg": True, "epg-pw": True,
                       "epgshare01": False},
                "gb": {"iptv-epg": True, "freeview-epg": True},
            },
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/epg-health")

        assert response.status == 200
        body = json.loads(response.body)
        assert body["healthy"] == 81
        assert body["unhealthy"] == 25
        assert body["providers"] == 5
        assert body["countries"] == 34
        assert body["details"]["us"]["iptv-epg"] is True
        mock_run.assert_called_once_with()


class TestFeedXmlRoute:
    """``GET /api/feed.xml`` — RSS 2.0 feed.

    Returns ``application/rss+xml`` (NOT JSON) via
    ``_raw_response``. Test asserts content-type and that the
    body parses as valid XML.
    """

    @patch("media_stack.api.routes.epg.metrics_svc")
    def test_returns_rss_xml(self, mock_metrics) -> None:
        mock_metrics.get_rss_feed.return_value = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<rss version="2.0"><channel>'
            '<title>Media Stack Controller</title>'
            '<description>Action events</description>'
            '<link>/</link>'
            '<item><title>Action: probe — complete</title>'
            '<description>Duration: 0.5s</description>'
            '<category>complete</category></item>'
            '</channel></rss>'
        )
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/feed.xml")

        assert response.status == 200
        assert response.content_type == (
            "application/rss+xml; charset=utf-8"
        )
        # Body is bytes, NOT JSON. Parse it as XML to confirm
        # validity. ``ET.fromstring`` accepts bytes directly.
        root = ET.fromstring(response.body)
        assert root.tag == "rss"
        assert root.attrib.get("version") == "2.0"
        channel = root.find("channel")
        assert channel is not None
        assert channel.findtext("title") == "Media Stack Controller"

    @patch("media_stack.api.routes.epg.metrics_svc")
    def test_passes_state_and_cache_to_service(
        self, mock_metrics,
    ) -> None:
        mock_metrics.get_rss_feed.return_value = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<rss version="2.0"><channel>'
            '<title>x</title><description>x</description>'
            '<link>/</link></channel></rss>'
        )
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/feed.xml")

        assert response.status == 200
        # First positional is the handler.state object; second is
        # the api_cache module-level singleton. We assert the call
        # happened and shape-check the args without binding to a
        # specific MockState instance.
        mock_metrics.get_rss_feed.assert_called_once()
        args, _ = mock_metrics.get_rss_feed.call_args
        assert len(args) == 2
        # state stand-in has ``to_dict`` per _MockState.
        assert hasattr(args[0], "to_dict")
        # cache has a ``get`` method (api_cache).
        assert hasattr(args[1], "get")


class TestRoutingIntegration:
    """Pin auto-discovery + spec-parity behaviour for the EPG
    domain. If a future change drops a handler from the registry,
    this fires before any per-route test does.
    """

    def test_all_epg_routes_registered(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        expected = {
            "/api/livetv-sources",
            "/api/iptv-countries",
            "/api/epg-providers",
            "/api/epg-health",
            "/api/feed.xml",
        }
        registered = {
            r.path
            for r in harness._dispatcher._router.registered_routes()
            if r.path in expected
        }
        assert registered == expected, (
            f"Missing EPG routes: {expected - registered}"
        )

    def test_post_to_epg_health_get_path_returns_method_not_allowed(
        self,
    ) -> None:
        harness = RouteDispatchHarness.with_default_router()
        outcome, _ = harness.try_dispatch("POST", "/api/epg-health")
        from media_stack.api.routing import DispatchOutcome
        assert outcome == DispatchOutcome.METHOD_NOT_ALLOWED

    def test_post_to_feed_xml_get_path_returns_method_not_allowed(
        self,
    ) -> None:
        harness = RouteDispatchHarness.with_default_router()
        outcome, _ = harness.try_dispatch("POST", "/api/feed.xml")
        from media_stack.api.routing import DispatchOutcome
        assert outcome == DispatchOutcome.METHOD_NOT_ALLOWED
