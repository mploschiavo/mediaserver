"""Tests for ``api/routes/system_diag.py`` (ADR-0007 Phase 2 wave 4).

One test class per route plus a routing-integration sanity check
that verifies the Router auto-discovered + registered all six
paths through the production ``DefaultDispatcher``.

Each route is a one-line delegation to ``ops_svc`` / ``disk_svc``;
we patch the module-level reference on the route module so the
assertion is "this route delegates to the right adapter method"
without re-testing the service's host-probe internals (which
need actual ``kubectl`` / ``docker`` / ``/proc/mounts`` to
exercise honestly).
"""

from __future__ import annotations

import json
from unittest.mock import patch

from tests.unit.api.routes._helpers import RouteDispatchHarness


class TestGpuRoute:
    """``GET /api/gpu`` — host GPU detection for transcoding."""

    @patch("media_stack.api.routes.system_diag.ops_svc")
    def test_returns_gpu_payload(self, mock_ops) -> None:
        mock_ops.get_gpu_info.return_value = {
            "vendor": "intel",
            "device": "Intel Arc A380",
            "hwaccel": "vaapi",
            "supported": True,
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/gpu")

        assert response.status == 200
        assert json.loads(response.body) == {
            "vendor": "intel",
            "device": "Intel Arc A380",
            "hwaccel": "vaapi",
            "supported": True,
        }
        mock_ops.get_gpu_info.assert_called_once_with()

    @patch("media_stack.api.routes.system_diag.ops_svc")
    def test_returns_unsupported_payload_when_no_gpu(
        self, mock_ops,
    ) -> None:
        mock_ops.get_gpu_info.return_value = {
            "vendor": "",
            "supported": False,
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/gpu")

        assert response.status == 200
        assert json.loads(response.body) == {
            "vendor": "",
            "supported": False,
        }


class TestNamespacesRoute:
    """``GET /api/namespaces`` — k8s namespace OR compose
    container roll-up depending on runtime."""

    @patch("media_stack.api.routes.system_diag.ops_svc")
    def test_returns_k8s_namespace_payload(self, mock_ops) -> None:
        mock_ops.get_namespaces.return_value = {
            "namespaces": [
                {
                    "namespace": "media-stack",
                    "current": True,
                    "pods": 12,
                    "running": 11,
                    "problems": [
                        {"name": "sonarr-0", "phase": "Pending",
                         "reason": "ImagePullBackOff"},
                    ],
                },
            ],
            "services": [],
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/namespaces")

        assert response.status == 200
        body = json.loads(response.body)
        assert body["namespaces"][0]["namespace"] == "media-stack"
        assert body["namespaces"][0]["pods"] == 12
        mock_ops.get_namespaces.assert_called_once_with()

    @patch("media_stack.api.routes.system_diag.ops_svc")
    def test_returns_compose_container_payload(self, mock_ops) -> None:
        """Compose mode returns a different shape than k8s mode;
        the route is runtime-agnostic and just forwards whatever
        ``OpsService.get_namespaces`` decided to emit."""
        mock_ops.get_namespaces.return_value = {
            "containers": [
                {"name": "sonarr", "status": "running", "image": "x:1"},
            ],
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/namespaces")

        assert response.status == 200
        assert json.loads(response.body) == {
            "containers": [
                {"name": "sonarr", "status": "running", "image": "x:1"},
            ],
        }


class TestSnapshotsRoute:
    """``GET /api/snapshots`` — config-snapshot inventory."""

    @patch("media_stack.api.routes.system_diag.ops_svc")
    def test_returns_snapshot_inventory(self, mock_ops) -> None:
        mock_ops.get_config_snapshots.return_value = {
            "snapshots": [
                {"filename": "snap-2026-04-25.tgz", "size": 12345,
                 "created_at": "2026-04-25T10:00:00Z"},
                {"filename": "snap-2026-04-24.tgz", "size": 11000,
                 "created_at": "2026-04-24T10:00:00Z"},
            ],
            "count": 2,
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/snapshots")

        assert response.status == 200
        body = json.loads(response.body)
        assert body["count"] == 2
        assert len(body["snapshots"]) == 2
        mock_ops.get_config_snapshots.assert_called_once_with()

    @patch("media_stack.api.routes.system_diag.ops_svc")
    def test_returns_empty_inventory_when_no_snapshots(
        self, mock_ops,
    ) -> None:
        mock_ops.get_config_snapshots.return_value = {
            "snapshots": [], "count": 0,
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/snapshots")

        assert response.status == 200
        assert json.loads(response.body) == {
            "snapshots": [], "count": 0,
        }


class TestStorageBreakdownRoute:
    """``GET /api/storage-breakdown`` — per-folder media usage.

    Pinned to the ``{breakdown, total_bytes, total_display,
    media_root}`` shape per the ``storage-breakdown live shape``
    memo. The OpenAPI spec previously declared a looser
    ``additionalProperties: true`` shape; this test asserts the
    LIVE shape (Python handler is the source of truth)."""

    @patch("media_stack.api.routes.system_diag.disk_svc")
    def test_returns_canonical_breakdown_shape(self, mock_disk) -> None:
        mock_disk.get_storage_breakdown.return_value = {
            "breakdown": [
                {"name": "movies", "path": "/srv-stack/media/movies",
                 "bytes": 5_368_709_120, "display": "5.0 GB"},
                {"name": "tv", "path": "/srv-stack/media/tv",
                 "bytes": 2_147_483_648, "display": "2.0 GB"},
            ],
            "total_bytes": 7_516_192_768,
            "total_display": "7.0 GB",
            "media_root": "/srv-stack/media",
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/storage-breakdown")

        assert response.status == 200
        body = json.loads(response.body)
        # Pin all four top-level keys — operators would lose visible
        # usage numbers if any went missing on the wire.
        assert set(body) == {
            "breakdown", "total_bytes", "total_display", "media_root",
        }
        # The shape is NOT keyed-by-library; ``breakdown`` is a
        # list, not a dict.
        assert isinstance(body["breakdown"], list)
        assert body["media_root"] == "/srv-stack/media"
        assert body["total_bytes"] == 7_516_192_768
        mock_disk.get_storage_breakdown.assert_called_once_with()

    @patch("media_stack.api.routes.system_diag.disk_svc")
    def test_returns_empty_breakdown_with_error_when_root_missing(
        self, mock_disk,
    ) -> None:
        """When ``MEDIA_ROOT`` does not resolve,
        ``DiskService.get_storage_breakdown`` returns an error
        envelope with ``breakdown: []``, ``total_bytes: 0``,
        and an ``error`` key. Route forwards that verbatim."""
        mock_disk.get_storage_breakdown.return_value = {
            "breakdown": [],
            "error": "Media root not found",
            "total_bytes": 0,
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/storage-breakdown")

        assert response.status == 200
        body = json.loads(response.body)
        assert body["breakdown"] == []
        assert body["total_bytes"] == 0
        assert body["error"] == "Media root not found"


class TestImageUpdatesRoute:
    """``GET /api/image-updates`` — container-image staleness."""

    @patch("media_stack.api.routes.system_diag.ops_svc")
    def test_returns_image_update_summary(self, mock_ops) -> None:
        mock_ops.check_image_updates.return_value = {
            "services": [
                {"name": "sonarr", "current": "v4.0.0",
                 "latest": "v4.0.5", "update_available": True},
                {"name": "radarr", "current": "v5.2.0",
                 "latest": "v5.2.0", "update_available": False},
            ],
            "updates_available": 1,
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/image-updates")

        assert response.status == 200
        body = json.loads(response.body)
        assert body["updates_available"] == 1
        assert len(body["services"]) == 2
        mock_ops.check_image_updates.assert_called_once_with()

    @patch("media_stack.api.routes.system_diag.ops_svc")
    def test_returns_zero_updates_when_all_current(
        self, mock_ops,
    ) -> None:
        mock_ops.check_image_updates.return_value = {
            "services": [], "updates_available": 0,
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/image-updates")

        assert response.status == 200
        assert json.loads(response.body) == {
            "services": [], "updates_available": 0,
        }


class TestMountsRoute:
    """``GET /api/mounts`` — host mount-point usage."""

    @patch("media_stack.api.routes.system_diag.ops_svc")
    def test_returns_mount_inventory(self, mock_ops) -> None:
        mock_ops.get_mount_info.return_value = {
            "mounts": [
                {"path": "/srv-stack/media",
                 "fstype": "ext4",
                 "total_bytes": 1_000_000_000_000,
                 "used_bytes": 800_000_000_000,
                 "free_bytes": 200_000_000_000},
                {"path": "/srv-config",
                 "fstype": "ext4",
                 "total_bytes": 100_000_000_000,
                 "used_bytes": 25_000_000_000,
                 "free_bytes": 75_000_000_000},
            ],
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/mounts")

        assert response.status == 200
        body = json.loads(response.body)
        assert len(body["mounts"]) == 2
        assert body["mounts"][0]["path"] == "/srv-stack/media"
        mock_ops.get_mount_info.assert_called_once_with()

    @patch("media_stack.api.routes.system_diag.ops_svc")
    def test_returns_empty_mounts_when_probe_fails(
        self, mock_ops,
    ) -> None:
        """``OpsService.get_mount_info`` returns
        ``{"mounts": [], "error": ...}`` when ``/proc/mounts`` is
        unreadable (containers without host-mount visibility).
        Route forwards that envelope so the UI can render an
        explicit "no mount info" empty state."""
        mock_ops.get_mount_info.return_value = {
            "mounts": [], "error": "permission denied",
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/mounts")

        assert response.status == 200
        assert json.loads(response.body) == {
            "mounts": [], "error": "permission denied",
        }


class TestRoutingIntegration:
    """Pin auto-discovery + spec-parity behaviour for the
    system_diag domain. If a future change accidentally drops a
    handler from the registry, this test fires before any
    per-route test does."""

    def test_all_system_diag_routes_registered(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        expected = {
            "/api/gpu",
            "/api/namespaces",
            "/api/snapshots",
            "/api/storage-breakdown",
            "/api/image-updates",
            "/api/mounts",
        }
        registered = {
            r.path
            for r in harness._dispatcher._router.registered_routes()
            if r.path in expected
        }
        assert registered == expected, (
            f"Missing system_diag routes: {expected - registered}"
        )

    def test_post_to_gpu_returns_method_not_allowed(self) -> None:
        from media_stack.api.routing import DispatchOutcome
        harness = RouteDispatchHarness.with_default_router()
        outcome, _ = harness.try_dispatch("POST", "/api/gpu")
        assert outcome == DispatchOutcome.METHOD_NOT_ALLOWED

    def test_post_to_storage_breakdown_returns_method_not_allowed(
        self,
    ) -> None:
        from media_stack.api.routing import DispatchOutcome
        harness = RouteDispatchHarness.with_default_router()
        outcome, _ = harness.try_dispatch(
            "POST", "/api/storage-breakdown",
        )
        assert outcome == DispatchOutcome.METHOD_NOT_ALLOWED

    def test_post_to_namespaces_returns_method_not_allowed(self) -> None:
        from media_stack.api.routing import DispatchOutcome
        harness = RouteDispatchHarness.with_default_router()
        outcome, _ = harness.try_dispatch("POST", "/api/namespaces")
        assert outcome == DispatchOutcome.METHOD_NOT_ALLOWED
