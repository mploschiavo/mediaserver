"""Snapshot-domain GET routes (ADR-0007 Phase 2 wave 7).

One route migrated off the ``handlers_get.handle()`` elif chain:

* ``GET /api/snapshots/{filename}`` — read the full content of a
  specific config snapshot file. Always returns 200 even when the
  snapshot is not found (legacy behaviour preserved — the service
  layer returns ``{"error": "..."}`` at 200 for invalid filenames
  and path traversal; the route echoes that payload unchanged).

The OpenAPI spec already declares ``/api/snapshots/{filename}``
at line 3837. No spec edit needed.

OO discipline (ADR-0007 + project-wide rule):

* ``SnapshotsGetRoutes`` is a ``RouteModule`` subclass with
  instance methods only — no ``@staticmethod``, no loose
  top-level handler functions.
* ``SnapshotsRepository`` is a thin adapter onto
  ``OpsService.get_snapshot_detail``; constructor-injected so
  tests can swap behaviour without touching the real filesystem.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

from media_stack.api.routing import RouteModule, get


class SnapshotsRepository:
    """Adapter onto ``OpsService.get_snapshot_detail``.

    The service owns basename validation + safety checks (path
    traversal, prefix guard, JSON load). This adapter exists purely
    to give tests a seam without touching the real filesystem.
    Constructor-injects the callable; the default does a fresh
    attribute lookup per call so ``mock.patch`` on the canonical
    symbol takes effect.
    """

    def __init__(self, get_detail_fn: Any = None) -> None:
        self._get_detail = get_detail_fn

    def get_detail(self, filename: str) -> dict[str, Any]:
        """Return the snapshot detail dict for ``filename``.

        Delegates straight to the service; the service handles all
        error cases by returning ``{"error": ...}`` rather than
        raising (legacy 200-on-error behaviour preserved).
        """
        if self._get_detail is not None:
            return self._get_detail(filename)
        from media_stack.api.services import ops as ops_svc
        return ops_svc.get_snapshot_detail(filename)


class SnapshotsGetRoutes(RouteModule):
    """Snapshot GET route.

    The Router auto-discovers + instantiates this class at startup.
    Constructor defaults keep auto-discovery zero-arg; tests pass a
    stub ``SnapshotsRepository`` to avoid touching the real
    ``$CONFIG_ROOT/.snapshots/`` directory.
    """

    def __init__(
        self,
        *,
        snapshots_repository: SnapshotsRepository | None = None,
    ) -> None:
        self._snapshots = snapshots_repository or SnapshotsRepository()

    @get("/api/snapshots/{filename}")
    def handle_snapshot_detail(
        self, handler: Any, *, filename: str,
    ) -> None:
        """Return the full content of a config snapshot file.

        Always responds 200 — the service layer returns
        ``{"error": "..."}`` for invalid filenames, path traversal,
        and missing files so the UI displays the error in-band without
        special-casing the status code. Matches the legacy behaviour
        at ``handlers_get.py:1216-1218``.
        """
        body = self._snapshots.get_detail(filename)
        handler._json_response(HTTPStatus.OK, body)


__all__ = [
    "SnapshotsGetRoutes",
    "SnapshotsRepository",
]
