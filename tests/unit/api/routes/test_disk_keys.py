"""Tests for ``api/routes/disk_keys.py`` (ADR-0007 Phase 2).

One test class per route plus a routing-integration sanity check
that verifies the Router auto-discovered + registered all three
paths through the production ``DefaultDispatcher``.

The route bodies in this module are lifted (not delegated) — every
collaborator is reached via a module-level import in
``disk_keys.py``. We mock at the import site so the assertions are
about the route's contract (calls the right collaborator, threads
the response shape correctly) rather than re-testing the
collaborator's internals.
"""

from __future__ import annotations

import json
from unittest.mock import patch

from tests.unit.api.routes._helpers import RouteDispatchHarness


class TestKeysRoute:
    """``GET /api/keys`` — REDACTED keys + admin username +
    password-set flag.

    The route lifts its body, so we mock the three collaborators
    it imported: ``health_svc.discover_api_keys`` (raw keys),
    ``redact_api_key_map`` (the redactor), and ``os.environ``
    (admin creds).
    """

    def test_returns_redacted_keys_plus_admin_metadata(self) -> None:
        raw_keys = {"sonarr": "abcd1234", "radarr": "wxyz5678"}
        redacted = {
            "sonarr": {
                "has_key": True,
                "fingerprint": "abcd...1234",
                "source": "discovered",
            },
            "radarr": {
                "has_key": True,
                "fingerprint": "wxyz...5678",
                "source": "discovered",
            },
        }
        with patch(
            "media_stack.api.routes.disk_keys.health_svc.discover_api_keys",
            return_value=raw_keys,
        ), patch(
            "media_stack.api.routes.disk_keys.redact_api_key_map",
            return_value=redacted,
        ), patch.dict(
            "os.environ",
            {
                "STACK_ADMIN_USERNAME": "operator",
                "STACK_ADMIN_PASSWORD": "hunter2",
            },
            clear=False,
        ):
            harness = RouteDispatchHarness.with_default_router()
            response = harness.dispatch("GET", "/api/keys")

        assert response.status == 200
        body = json.loads(response.body)
        assert body == {
            "keys": redacted,
            "admin": {"username": "operator", "password_set": True},
            "count": 2,
        }

    def test_admin_password_unset_reports_password_set_false(
        self,
    ) -> None:
        with patch(
            "media_stack.api.routes.disk_keys.health_svc.discover_api_keys",
            return_value={},
        ), patch(
            "media_stack.api.routes.disk_keys.redact_api_key_map",
            return_value={},
        ), patch.dict(
            "os.environ",
            {"STACK_ADMIN_USERNAME": "admin", "STACK_ADMIN_PASSWORD": ""},
            clear=False,
        ):
            harness = RouteDispatchHarness.with_default_router()
            response = harness.dispatch("GET", "/api/keys")

        assert response.status == 200
        body = json.loads(response.body)
        assert body["admin"] == {
            "username": "admin",
            "password_set": False,
        }
        assert body["count"] == 0

    def test_admin_username_defaults_to_admin_when_env_missing(
        self,
    ) -> None:
        with patch(
            "media_stack.api.routes.disk_keys.health_svc.discover_api_keys",
            return_value={},
        ), patch(
            "media_stack.api.routes.disk_keys.redact_api_key_map",
            return_value={},
        ), patch.dict(
            "os.environ",
            {},
            clear=True,
        ):
            harness = RouteDispatchHarness.with_default_router()
            response = harness.dispatch("GET", "/api/keys")

        assert response.status == 200
        body = json.loads(response.body)
        assert body["admin"]["username"] == "admin"
        assert body["admin"]["password_set"] is False

    def test_redactor_is_invoked_with_discovered_source(self) -> None:
        """The redactor takes a ``source=`` kwarg that becomes the
        ``source`` field on every redacted entry. The route pins
        this to ``"discovered"`` per the security-audit shape."""
        with patch(
            "media_stack.api.routes.disk_keys.health_svc.discover_api_keys",
            return_value={"sonarr": "k"},
        ), patch(
            "media_stack.api.routes.disk_keys.redact_api_key_map",
            return_value={},
        ) as mock_redact, patch.dict(
            "os.environ", {}, clear=False,
        ):
            harness = RouteDispatchHarness.with_default_router()
            harness.dispatch("GET", "/api/keys")

        mock_redact.assert_called_once_with(
            {"sonarr": "k"}, source="discovered",
        )


class TestDiskRoute:
    """``GET /api/disk`` — per-volume usage + guardrail config.
    One-line delegation; we mock ``disk_svc.get_disk`` and assert
    the response carries the dict verbatim.
    """

    def test_returns_disk_payload_verbatim(self) -> None:
        payload = {
            "disk": {
                "config": {
                    "path": "/srv-config",
                    "total_bytes": 100,
                    "used_bytes": 25,
                    "free_bytes": 75,
                    "percent_used": 25.0,
                },
                "media": {
                    "path": "/srv-stack/media",
                    "total_bytes": 200,
                    "used_bytes": 130,
                    "free_bytes": 70,
                    "percent_used": 65.0,
                },
            },
            "guardrails": {
                "enabled": True,
                "max_used_percent": 65,
                "target_used_percent": 58,
            },
        }
        with patch(
            "media_stack.api.routes.disk_keys.disk_svc.get_disk",
            return_value=payload,
        ):
            harness = RouteDispatchHarness.with_default_router()
            response = harness.dispatch("GET", "/api/disk")

        assert response.status == 200
        assert json.loads(response.body) == payload

    def test_returns_empty_payload_when_service_returns_empty(
        self,
    ) -> None:
        """If ``DiskService`` can't resolve any volumes (rare —
        disk-usage permission denied) it still returns an empty
        ``{"disk": {}}`` envelope. Route must not paper over that;
        the dashboard renders an empty state."""
        with patch(
            "media_stack.api.routes.disk_keys.disk_svc.get_disk",
            return_value={"disk": {}, "guardrails": {}},
        ):
            harness = RouteDispatchHarness.with_default_router()
            response = harness.dispatch("GET", "/api/disk")

        assert response.status == 200
        assert json.loads(response.body) == {
            "disk": {}, "guardrails": {},
        }


class TestCleanupPreviewRoute:
    """``GET /api/cleanup-preview`` — dry-run cleanup candidate
    list. One-line delegation; we mock
    ``disk_svc.preview_cleanup`` and assert the dict flows through.
    """

    def test_returns_preview_payload_verbatim(self) -> None:
        payload = {
            "candidates": [
                {
                    "name": "Movie.A.2024.mkv",
                    "size": 4_500_000_000,
                    "category": "movies",
                    "age_hours": 72.0,
                    "ratio": 1.5,
                },
            ],
            "over_threshold": True,
        }
        with patch(
            "media_stack.api.routes.disk_keys.disk_svc.preview_cleanup",
            return_value=payload,
        ):
            harness = RouteDispatchHarness.with_default_router()
            response = harness.dispatch("GET", "/api/cleanup-preview")

        assert response.status == 200
        assert json.loads(response.body) == payload

    def test_returns_empty_candidates_when_nothing_to_delete(
        self,
    ) -> None:
        with patch(
            "media_stack.api.routes.disk_keys.disk_svc.preview_cleanup",
            return_value={"candidates": [], "over_threshold": False},
        ):
            harness = RouteDispatchHarness.with_default_router()
            response = harness.dispatch("GET", "/api/cleanup-preview")

        assert response.status == 200
        assert json.loads(response.body) == {
            "candidates": [], "over_threshold": False,
        }


class TestRoutingIntegration:
    """Pin auto-discovery + spec-parity behaviour for the
    disk+keys domain. If a future change accidentally drops a
    handler from the registry, this test fires before any
    per-route test does."""

    def test_all_disk_keys_routes_registered(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        expected = {
            "/api/keys",
            "/api/disk",
            "/api/cleanup-preview",
        }
        registered = {
            r.path
            for r in harness._dispatcher._router.registered_routes()
            if r.path in expected
        }
        assert registered == expected, (
            f"Missing disk_keys routes: {expected - registered}"
        )

    def test_post_to_keys_returns_method_not_allowed(self) -> None:
        from media_stack.api.routing import DispatchOutcome
        harness = RouteDispatchHarness.with_default_router()
        outcome, _ = harness.try_dispatch("POST", "/api/keys")
        assert outcome == DispatchOutcome.METHOD_NOT_ALLOWED

    def test_post_to_disk_returns_method_not_allowed(self) -> None:
        from media_stack.api.routing import DispatchOutcome
        harness = RouteDispatchHarness.with_default_router()
        outcome, _ = harness.try_dispatch("POST", "/api/disk")
        assert outcome == DispatchOutcome.METHOD_NOT_ALLOWED
