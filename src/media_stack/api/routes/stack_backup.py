"""Stack-backup + service-worker-config GET routes (ADR-0007 Phase 2 wave 4).

Three routes migrated off the ``handlers_get.handle()`` elif chain.
The fourth path the wave-4 brief listed (``GET /api/stack/update``)
is already owned by ``routes/stack_update.py`` (wave 2) — that
registration is left untouched and SKIPPED here, per the brief's
pre-flight rule.

Routes:

* ``GET /api/backup`` — emit the full configuration backup as a
  download. Body is JSON bytes; the response sets a
  ``Content-Disposition: attachment`` header so a browser-driven
  GET prompts a save-as dialog with a date-stamped filename. Lifted
  verbatim from the legacy chain — the only structural change is
  pulling the filename builder into a named ``BackupFilenameStrategy``
  so the timestamp format is no longer an inline magic string at
  the call site.
* ``GET /api/sw-config`` — alias under ``/api/`` so ext_authz
  forwards it without authentication (the dashboard SW fetches
  it before the user proves identity).
* ``GET /sw-config.json`` — top-level path for the same payload.
  The PWA service worker hits this on install/update.

Implementation patterns (per the project's "use named design
patterns where they fit" rule):

* **Repository** — ``BackupCatalogRepository`` wraps the
  ``DiagnosticsService.get_backup`` call. The route method calls
  ``repo.fetch(state)`` instead of reaching into the service tree.
  That gives the test seam a stable boundary (mock the repo, not
  the underlying ``config_svc.get_backup`` import path), and lets
  a future Phase-3 refactor swap the backing service without
  touching the route module.
* **Strategy** — ``BackupFilenameStrategy`` builds the
  ``Content-Disposition`` filename. Pulling the format
  (``media-stack-backup-YYYYMMDD-HHMMSS.json``) and the time
  source out of the route body means a test can pin the exact
  filename without monkey-patching ``time.strftime``, and a
  future format change has one named site to edit.
* **Strategy (per-route)** — ``ServiceWorkerConfigSource`` builds
  the SW-config payload once. Both ``/api/sw-config`` and
  ``/sw-config.json`` delegate to this single source so they're
  guaranteed to return byte-identical bodies — the PWA contract
  documents them as aliases.

Constructor injection: each strategy / repository is built once at
``StackBackupGetRoutes.__init__`` time and stored on the instance.
The Router instantiates each ``RouteModule`` subclass once at
startup, so this is a per-process singleton without any
module-level mutable state. Tests construct their own instance
via ``RouteDispatchHarness.with_default_router()`` (which goes
through ``DefaultDispatcher.reset_for_tests()``), so test
isolation is unaffected.
"""

from __future__ import annotations

import time
from http import HTTPStatus
from typing import Any, Callable

from media_stack.api.routing import RouteModule, get


# Compact-timestamp ``strftime`` template used when assembling the
# backup-download filename. Pulled out as a constant so the
# ``magic_strings`` ratchet sees one named source-of-truth.
_BACKUP_FILENAME_TIME_FORMAT = "%Y%m%d-%H%M%S"
_BACKUP_FILENAME_PREFIX = "media-stack-backup-"
_BACKUP_FILENAME_SUFFIX = ".json"
_BACKUP_CONTENT_TYPE = "application/json"


class BackupCatalogRepository:
    """Repository wrapping the diagnostics-service backup builder.

    The route method calls ``fetch(state)`` and gets back the
    JSON-encoded backup envelope as ``bytes``. The repo deliberately
    does NOT cache — each backup snapshot must reflect the current
    on-disk profile + service configs at the moment of download.

    Constructor takes the loader function so a test can inject a
    fake without touching ``config_svc`` import paths. The default
    binds the production ``config_svc.get_backup`` lazily — that
    keeps the import graph small for processes that never hit
    this route (the legacy chain held the same lazy posture).
    """

    def __init__(
        self,
        loader: Callable[[Any], bytes] | None = None,
    ) -> None:
        self._loader = loader

    def fetch(self, state: Any) -> bytes:
        loader = self._loader or self._default_loader
        return loader(state)

    @staticmethod
    def _default_loader(state: Any) -> bytes:
        # Lazy import: ``config_svc`` pulls the diagnostics + profile
        # tree, which the route module shouldn't drag into startup.
        from media_stack.api.services import config as config_svc
        return config_svc.get_backup(state)


class BackupFilenameStrategy:
    """Strategy for building the backup-download filename.

    The format is ``media-stack-backup-<YYYYMMDD-HHMMSS>.json`` to
    match the legacy elif body's emission. Constructor takes a
    ``time_provider`` so a test can pin the timestamp without
    monkey-patching ``time.strftime`` globally.
    """

    def __init__(
        self,
        time_provider: Callable[[], time.struct_time] | None = None,
    ) -> None:
        self._time_provider = time_provider or time.localtime

    def filename(self) -> str:
        stamp = time.strftime(
            _BACKUP_FILENAME_TIME_FORMAT, self._time_provider(),
        )
        return f"{_BACKUP_FILENAME_PREFIX}{stamp}{_BACKUP_FILENAME_SUFFIX}"

    def content_disposition(self) -> str:
        return f'attachment; filename="{self.filename()}"'


class ServiceWorkerConfigSource:
    """Single-source-of-truth strategy for the SW-config payload.

    Both ``/api/sw-config`` and ``/sw-config.json`` delegate here so
    they emit byte-identical bodies. The constructor takes the
    payload builder so tests can swap it; the default binds
    ``services.sw_config.get_sw_config`` lazily for the same
    import-graph reason as the backup repo.
    """

    def __init__(
        self,
        builder: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self._builder = builder

    def build(self) -> dict[str, Any]:
        builder = self._builder or self._default_builder
        return builder()

    @staticmethod
    def _default_builder() -> dict[str, Any]:
        from media_stack.api.services.sw_config import get_sw_config
        return get_sw_config()


class StackBackupGetRoutes(RouteModule):
    """All ``/api/backup`` + service-worker-config GET routes.

    The Router auto-discovers + instantiates this class + walks
    its tagged methods at startup. Constructor wires up the
    Repository + two Strategy instances; tests can subclass +
    override the constructor to inject fakes.
    """

    def __init__(
        self,
        backup_repo: BackupCatalogRepository | None = None,
        filename_strategy: BackupFilenameStrategy | None = None,
        sw_config_source: ServiceWorkerConfigSource | None = None,
    ) -> None:
        self._backup_repo = backup_repo or BackupCatalogRepository()
        self._filename = filename_strategy or BackupFilenameStrategy()
        self._sw_config = sw_config_source or ServiceWorkerConfigSource()

    @get("/api/backup")
    def handle_backup(self, handler: Any) -> None:
        """Emit the full configuration backup as a JSON download.

        Returns a ``Content-Disposition: attachment`` response so a
        browser-driven GET prompts a save-as dialog. The filename
        is timestamp-stamped via ``BackupFilenameStrategy``.
        """
        payload = self._backup_repo.fetch(handler.state)
        handler._raw_response(
            HTTPStatus.OK,
            _BACKUP_CONTENT_TYPE,
            payload,
            {"Content-Disposition": self._filename.content_disposition()},
        )

    @get("/api/sw-config")
    def handle_api_sw_config(self, handler: Any) -> None:
        """Service-worker runtime config under ``/api/`` for
        ext_authz parity. Body-identical to ``/sw-config.json``."""
        handler._json_response(HTTPStatus.OK, self._sw_config.build())

    @get("/sw-config.json")
    def handle_sw_config_json(self, handler: Any) -> None:
        """Top-level service-worker runtime config. The PWA SW
        fetches this on install/update."""
        handler._json_response(HTTPStatus.OK, self._sw_config.build())


__all__ = [
    "BackupCatalogRepository",
    "BackupFilenameStrategy",
    "ServiceWorkerConfigSource",
    "StackBackupGetRoutes",
]
