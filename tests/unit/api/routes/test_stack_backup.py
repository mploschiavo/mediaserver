"""Tests for ``api/routes/stack_backup.py`` (ADR-0007 Phase 2 wave 4).

Covers the 3 migrated routes plus a routing-integration sanity
check that the Router auto-discovered + registered them all.

The wave-4 brief listed ``/api/stack/update`` as a fourth path —
that route is owned by ``routes/stack_update.py`` (wave 2) and
SKIPPED here. ``test_stack_update.py`` continues to cover it.
"""

from __future__ import annotations

import json
import time
from typing import Any
from unittest.mock import patch

from media_stack.api.routes.stack_backup import (
    BackupCatalogRepository,
    BackupFilenameStrategy,
    ServiceWorkerConfigSource,
    StackBackupGetRoutes,
)
from tests.unit.api.routes._helpers import RouteDispatchHarness


_FROZEN_TIME = time.strptime("2026-04-25 14:30:22", "%Y-%m-%d %H:%M:%S")


class TestApiBackupRoute:
    """``GET /api/backup`` — backup-download with ``Content-Disposition``."""

    @patch("media_stack.api.services.config.get_backup")
    def test_returns_backup_payload_with_attachment_header(
        self, mock_get_backup,
    ) -> None:
        mock_get_backup.return_value = b'{"timestamp": "2026-04-25T14:30:22Z", "version": "2"}'
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/backup")

        assert response.status == 200
        assert response.content_type == "application/json"
        assert b'"timestamp"' in response.body
        assert "Content-Disposition" in response.extra_headers
        disposition = response.extra_headers["Content-Disposition"]
        assert disposition.startswith("attachment; filename=")
        assert "media-stack-backup-" in disposition
        assert disposition.endswith('.json"')

    @patch("media_stack.api.services.config.get_backup")
    def test_forwards_handler_state_to_service(
        self, mock_get_backup,
    ) -> None:
        """Pin the state-passthrough — the diagnostics service
        embeds ``state.to_dict()`` in the backup envelope, so the
        route MUST forward ``handler.state`` (not ``None``)."""
        mock_get_backup.return_value = b"{}"
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/backup")

        assert response.status == 200
        # The service was called once, with the mock handler's
        # ``state`` attribute as its only positional arg.
        assert mock_get_backup.call_count == 1
        passed_state = mock_get_backup.call_args.args[0]
        # ``MockControllerHandler`` wires ``_MockState`` by default;
        # it has ``initial_bootstrap_done`` + a ``to_dict`` method.
        assert hasattr(passed_state, "to_dict")


class TestApiSwConfigRoute:
    """``GET /api/sw-config`` — alias under ``/api/`` for ext_authz."""

    @patch("media_stack.api.services.sw_config.get_sw_config")
    def test_returns_sw_config_payload(self, mock_get) -> None:
        mock_get.return_value = {
            "version": 1,
            "basepath": "/app/media-stack-ui",
            "denylist_patterns": ["^/api/"],
            "allowed_app_prefixes": ["/app/media-stack-ui"],
            "sister_app_prefixes": ["/app/sonarr", "/app/jellyfin"],
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/sw-config")

        assert response.status == 200
        body = json.loads(response.body)
        assert body["version"] == 1
        assert body["basepath"] == "/app/media-stack-ui"
        assert "^/api/" in body["denylist_patterns"]
        assert "/app/sonarr" in body["sister_app_prefixes"]
        mock_get.assert_called_once_with()


class TestSwConfigJsonRoute:
    """``GET /sw-config.json`` — top-level path the PWA SW fetches."""

    @patch("media_stack.api.services.sw_config.get_sw_config")
    def test_returns_sw_config_payload(self, mock_get) -> None:
        mock_get.return_value = {
            "version": 1,
            "basepath": "/app/media-stack-ui",
            "denylist_patterns": [
                "^/api/",
                r"^/app/(?!media-stack-ui(?:/|$))",
            ],
            "allowed_app_prefixes": ["/app/media-stack-ui"],
            "sister_app_prefixes": [],
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/sw-config.json")

        assert response.status == 200
        body = json.loads(response.body)
        assert body["basepath"] == "/app/media-stack-ui"
        assert len(body["denylist_patterns"]) == 2

    @patch("media_stack.api.services.sw_config.get_sw_config")
    def test_alias_paths_return_byte_identical_bodies(
        self, mock_get,
    ) -> None:
        """Pin the alias contract: ``/api/sw-config`` and
        ``/sw-config.json`` must return the same payload — the SW
        contract documents them as aliases, and the
        ``ServiceWorkerConfigSource`` strategy enforces that
        single-source-of-truth shape."""
        mock_get.return_value = {
            "version": 1,
            "basepath": "/app/media-stack-ui",
            "denylist_patterns": ["^/api/"],
            "allowed_app_prefixes": ["/app/media-stack-ui"],
            "sister_app_prefixes": [],
        }
        harness = RouteDispatchHarness.with_default_router()
        api_response = harness.dispatch("GET", "/api/sw-config")
        json_response = harness.dispatch("GET", "/sw-config.json")

        assert api_response.body == json_response.body


class TestBackupFilenameStrategy:
    """Direct unit tests on the filename strategy — pinning the
    format string here means a future change has one named site
    that test pressure can spot."""

    def test_filename_uses_compact_timestamp_format(self) -> None:
        strategy = BackupFilenameStrategy(time_provider=lambda: _FROZEN_TIME)
        assert strategy.filename() == "media-stack-backup-20260425-143022.json"

    def test_content_disposition_wraps_filename_in_attachment(
        self,
    ) -> None:
        strategy = BackupFilenameStrategy(time_provider=lambda: _FROZEN_TIME)
        assert strategy.content_disposition() == (
            'attachment; filename="media-stack-backup-20260425-143022.json"'
        )


class TestBackupCatalogRepository:
    """Direct unit tests on the repository — confirms the loader
    seam works without going through the harness."""

    def test_fetch_calls_injected_loader_with_state(self) -> None:
        captured: list[Any] = []

        def fake_loader(state: Any) -> bytes:
            captured.append(state)
            return b'{"ok": true}'

        repo = BackupCatalogRepository(loader=fake_loader)
        sentinel_state = object()
        result = repo.fetch(sentinel_state)

        assert result == b'{"ok": true}'
        assert captured == [sentinel_state]


class TestServiceWorkerConfigSource:
    """Direct unit tests on the SW-config strategy."""

    def test_build_calls_injected_builder(self) -> None:
        payload = {"version": 1, "basepath": "/app/media-stack-ui"}
        source = ServiceWorkerConfigSource(builder=lambda: payload)
        assert source.build() == payload


class TestStackBackupRouteModuleConstructorInjection:
    """Pin the constructor-injection contract on
    ``StackBackupGetRoutes`` so a future refactor that broke it
    would fail loudly here."""

    def test_constructor_accepts_injected_collaborators(self) -> None:
        repo = BackupCatalogRepository(loader=lambda _state: b"{}")
        filename = BackupFilenameStrategy(time_provider=lambda: _FROZEN_TIME)
        sw_source = ServiceWorkerConfigSource(builder=lambda: {"version": 1})
        module = StackBackupGetRoutes(
            backup_repo=repo,
            filename_strategy=filename,
            sw_config_source=sw_source,
        )
        # The collaborators are stored on the instance, not pulled
        # from module-global state.
        assert module._backup_repo is repo
        assert module._filename is filename
        assert module._sw_config is sw_source


class TestRoutingIntegration:
    """Pin auto-discovery + spec-parity behaviour for the
    stack-backup domain. If a future change accidentally drops
    a handler from the registry, this test fires before any
    per-route test does."""

    def test_all_stack_backup_routes_registered(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        expected = {
            "/api/backup",
            "/api/sw-config",
            "/sw-config.json",
        }
        registered = {
            r.path
            for r in harness._dispatcher._router.registered_routes()
            if r.path in expected
        }
        assert registered == expected, (
            f"Missing stack-backup routes: {expected - registered}"
        )

    def test_stack_update_route_still_owned_by_stack_update_module(
        self,
    ) -> None:
        """The wave-4 brief listed ``/api/stack/update`` but it's
        owned by ``routes/stack_update.py`` (wave 2). Pin that
        ownership so a future re-migration mistake here would
        fail loudly instead of silently double-registering and
        tripping the Router's duplicate-route check at startup."""
        harness = RouteDispatchHarness.with_default_router()
        owners = [
            type(r.handler.__self__).__module__
            for r in harness._dispatcher._router.registered_routes()
            if r.path == "/api/stack/update"
        ]
        assert owners == ["media_stack.api.routes.stack_update"], (
            f"/api/stack/update owners: {owners}"
        )
