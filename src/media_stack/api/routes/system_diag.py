"""System-diagnostics GET routes (ADR-0007 Phase 2 wave 4).

Six read-only diagnostics routes lifted off the legacy
``handlers_get.GetRequestHandler.handle()`` ``elif`` chain. The
domain spans three orthogonal infrastructure surfaces — but every
route is a thin one-line read against an underlying adapter, so
co-locating them keeps the ``api/routes/`` directory honest about
"diagnostics belong together":

* ``GET /api/gpu``               — host GPU detection (hwaccel
  candidates) for transcoding.
* ``GET /api/namespaces``        — k8s namespace + pod state, OR
  the equivalent compose container roll-up depending on runtime.
* ``GET /api/snapshots``         — config snapshot inventory used
  by the Snapshots page.
* ``GET /api/storage-breakdown`` — per-top-level-folder size +
  totals under ``MEDIA_ROOT``.
* ``GET /api/image-updates``     — container-image staleness vs
  the upstream registry.
* ``GET /api/mounts``            — host mount-point usage for the
  Storage page.

Spec parity (every path is declared in ``contracts/api/openapi.yaml``):

* ``getGpu`` / ``getNamespaces`` / ``getSnapshots`` /
  ``getStorageBreakdown`` / ``getImageUpdates`` / ``getMounts`` —
  all share the ``Operations`` (or per-domain) tag verbatim.

Implementation choices (per Phase 2's "lift the body OR call the
helper — agent's choice based on what's cleanest" rule):

* All six routes are ALREADY one-line delegations to a single
  service method in the legacy chain — the ``OpsService`` and
  ``DiskService`` instance methods are exposed module-level on
  ``services.ops`` and ``services.disk`` for callers. The route
  methods here do the same delegation, mirroring ``disk_keys.py``
  (which migrated ``/api/disk`` from the same domain space).
* The two adapter shapes co-locating here:

  - ``OpsService`` wraps subprocess + kubernetes-client + docker
    APIs (``get_namespaces`` / ``check_image_updates`` /
    ``get_gpu_info`` / ``get_config_snapshots`` /
    ``get_mount_info``). Each method is a thin
    ``Adapter`` over a host-runtime probe.
  - ``DiskService`` wraps filesystem traversal under
    ``MEDIA_ROOT``. ``get_storage_breakdown`` is a
    ``Repository``-shaped read returning the canonical
    ``{breakdown, total_bytes, total_display, media_root}`` shape
    (see ``DiskService.get_storage_breakdown`` in
    ``services/disk.py`` — the live shape is NOT keyed-by-library).

Both services are imported as module-level aliases (``ops_svc`` /
``disk_svc``) — same convention ``disk_keys.py`` uses for
``disk_svc``. Tests can patch these names at the route module's
import site without touching the underlying service singletons.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

from media_stack.api.routing import RouteModule, get
from media_stack.api.services import disk as disk_svc
from media_stack.api.services import ops as ops_svc


class SystemDiagGetRoutes(RouteModule):
    """All system-diagnostics GET routes. Auto-discovered +
    instantiated by the Router at startup; the Router walks tagged
    methods for registration. Stateless — every method delegates
    to an injected service module (``ops_svc`` / ``disk_svc``)
    without holding any per-request state on the instance.
    """

    @get("/api/gpu")
    def handle_gpu(self, handler: Any) -> None:
        """Return host GPU detection used by the Jellyfin
        hwaccel-config card. ``ops_svc.get_gpu_info`` shells out to
        the Intel/NVIDIA/AMD probe binaries and returns a
        normalized payload — body lifted as a one-line delegation
        from the legacy chain.
        """
        handler._json_response(HTTPStatus.OK, ops_svc.get_gpu_info())

    @get("/api/namespaces")
    def handle_namespaces(self, handler: Any) -> None:
        """Return k8s namespace + pod state when ``K8S_NAMESPACE``
        is set, OR the compose container roll-up otherwise.
        ``OpsService.get_namespaces`` picks the right adapter
        internally — the route stays runtime-agnostic.
        """
        handler._json_response(HTTPStatus.OK, ops_svc.get_namespaces())

    @get("/api/snapshots")
    def handle_snapshots(self, handler: Any) -> None:
        """Return the config-snapshot inventory used by the
        Snapshots page. Body shape is ``{snapshots: [...], count}``
        per ``OpsService.get_config_snapshots``. The
        per-snapshot detail route (``/api/snapshots/{filename}``)
        is intentionally NOT migrated here — it is a parameterized
        route handled by the legacy chain still and falls outside
        wave 4's scope.
        """
        handler._json_response(
            HTTPStatus.OK, ops_svc.get_config_snapshots(),
        )

    @get("/api/storage-breakdown")
    def handle_storage_breakdown(self, handler: Any) -> None:
        """Return per-top-level-folder size + totals under
        ``MEDIA_ROOT``. Live shape is
        ``{breakdown: [{name, path, bytes, display}, ...],
        total_bytes, total_display, media_root}`` —
        NOT keyed-by-library (the OpenAPI spec previously declared
        the looser ``additionalProperties: true`` shape; trust the
        Python handler over the spec per the ``OpenAPI vs live
        shape`` bug-class memo).
        """
        handler._json_response(
            HTTPStatus.OK, disk_svc.get_storage_breakdown(),
        )

    @get("/api/image-updates")
    def handle_image_updates(self, handler: Any) -> None:
        """Return container-image staleness vs the upstream
        registry. Drives the Update Center's "X services have
        newer images" banner. ``OpsService.check_image_updates``
        consults the registry adapter; the route is a one-line
        delegation.
        """
        handler._json_response(
            HTTPStatus.OK, ops_svc.check_image_updates(),
        )

    @get("/api/mounts")
    def handle_mounts(self, handler: Any) -> None:
        """Return host mount-point usage for the Storage page's
        secondary mount table. ``OpsService.get_mount_info``
        reads ``/proc/mounts`` + ``shutil.disk_usage`` per mount
        — adapter shape, route is a one-line delegation.
        """
        handler._json_response(HTTPStatus.OK, ops_svc.get_mount_info())


__all__ = ["SystemDiagGetRoutes"]
