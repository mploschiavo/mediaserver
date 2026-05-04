"""Content-lists GET routes (ADR-0007 Phase 2 wave 4).

Five routes migrated off the ``handlers_get.handle()`` elif chain,
all sharing the ``Content`` OpenAPI tag:

* ``GET /api/libraries`` — merge of live Jellyfin virtual folders
  with the controller's configured library set. The dashboard's
  ``LibraryDataSourceBanner`` keys on the ``source`` field, so the
  shape MUST round-trip ``{live, configured, source, media_server}``
  intact — see ``contracts/api/openapi.yaml`` line 2314.
* ``GET /api/recent`` — recently-added items per *arr service.
* ``GET /api/import-lists`` — import-list catalogue per *arr.
* ``GET /api/import-lists-all`` — import-list catalogue aggregated
  across every *arr (different shape from the per-service flavour).
* ``GET /api/quality-profiles`` — bare-path profile catalogue.
  The parameterized sibling ``/api/quality-profiles/{service}`` is
  registered by ``routes/indexers_quality.py`` and stays there.

Implementation choices, per Phase 2's "lift the body OR call the
helper — agent's choice based on what's cleanest" rule:

* The four single-call routes (``/api/recent``,
  ``/api/import-lists``, ``/api/import-lists-all``,
  ``/api/quality-profiles``) are one-line delegations to
  ``content_svc`` already-public functions — same shape as
  ``indexers_quality.py``'s non-parameterized routes.
* ``/api/libraries`` LIFTS its legacy body into a dedicated
  ``LibrariesRepository`` collaborator. The merge logic is the
  only non-trivial transform in this module — six lines that
  decide whether the dashboard sees ``source="live"`` or falls
  back to ``configured.source``. Keeping it in a class with a
  single ``aggregate()`` method (1) names what the merge does, (2)
  isolates the ``configured.source`` passthrough rule from the
  route handler, and (3) gives the unit tests a stable seam.
  Operator background: when the merge gets ``source`` wrong, the
  ``LibraryDataSourceBanner`` falsely claims ``JELLYFIN_API_KEY``
  is missing — see the docstring on ``aggregate()`` for the
  bug-class details.

OO discipline (ADR-0007 + project-wide rule):

* ``ContentListsGetRoutes`` is a ``RouteModule`` subclass with
  instance methods only — no ``@staticmethod``, no loose
  top-level handler functions.
* Service modules (``content_svc``, ``config_svc``) are injected
  via the constructor with module-level defaults. The constructor
  defaults preserve auto-discovery (the Router instantiates the
  class with no args) while making the dependencies explicit and
  swap-able for tests that need a stub.
* ``LibrariesRepository`` is also constructor-injected for the
  same reason: tests can pass a fake repo without monkeypatching,
  and the production wiring is a default arg.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

from media_stack.api.routing import RouteModule, get
from media_stack.api.services import config as config_svc
from media_stack.api.services import content as content_svc


class LibrariesRepository:
    """Aggregates the live Jellyfin virtual-folder list with the
    controller's configured library set into the
    ``/api/libraries`` response shape.

    Constructor-injected with the two service-module shims it
    needs:

    * ``content_service`` — adapter onto the live Jellyfin REST
      client (``content_svc.get_jellyfin_libraries``). Returns the
      virtual-folder list when Jellyfin is reachable, else an
      empty ``libraries=[]``.
    * ``config_service`` — adapter onto the configured-libraries
      store (``config_svc.get_libraries``). Always returns a
      payload — bootstrap defaults flow through if the operator
      hasn't customised anything.

    Both arguments default to ``None`` and are resolved lazily
    against this module's globals on each ``aggregate()`` call.
    Lazy resolution is deliberate: route-module-level patches
    (``patch("media_stack.api.routes.content_lists.content_svc")``)
    take effect even when the repository was constructed BEFORE
    the patch — which is the typical shape because the Router
    instantiates the route module (and therefore the default
    repo) at startup, well before any test patches run.
    """

    def __init__(
        self,
        content_service: Any = None,
        config_service: Any = None,
    ) -> None:
        self._content_override = content_service
        self._config_override = config_service

    def _resolve_content(self) -> Any:
        if self._content_override is not None:
            return self._content_override
        return content_svc

    def _resolve_config(self) -> Any:
        if self._config_override is not None:
            return self._config_override
        return config_svc

    def aggregate(self) -> dict[str, Any]:
        """Merge live + configured libraries into the
        dashboard-facing payload.

        ``source`` is the load-bearing field: the UI's
        ``LibraryDataSourceBanner`` predicate keys on it. When the
        merge picks ``source="live"`` the banner stays hidden;
        otherwise it shows ``"showing bootstrap defaults"`` (which
        operators read as "Jellyfin is misconfigured"). The rule:
        prefer ``"live"`` if the live Jellyfin call returned ANY
        libraries, else passthrough whatever ``source`` the
        configured store reports (typically ``"defaults"`` /
        ``"profile"`` / ``"persisted"``).

        Bug-class (legacy v1.0.150-era): without the live-branch
        passthrough, the banner stayed up even after live counts
        populated, falsely blaming a missing
        ``JELLYFIN_API_KEY``. The lift preserves that exact
        decision tree.
        """
        live = self._resolve_content().get_jellyfin_libraries()
        configured = self._resolve_config().get_libraries()
        live_libs = live.get("libraries", [])
        if live_libs:
            source = "live"
        else:
            source = configured.get("source", "unknown")
        return {
            "live": live_libs,
            "configured": configured.get("libraries", []),
            "source": source,
            "media_server": configured.get("media_server", ""),
        }


class ContentListsGetRoutes(RouteModule):
    """Content-tag GET routes covering library aggregation,
    recently-added feeds, import-list catalogues, and the
    bare-path quality-profile listing. The Router auto-discovers
    + instantiates this class + walks its tagged methods at
    startup.

    Constructor accepts the service-module shims and the
    libraries repository. Defaults wire up the production
    collaborators so auto-discovery (which calls ``__init__``
    with no args) just works.
    """

    def __init__(
        self,
        content_service: Any = None,
        libraries_repository: LibrariesRepository | None = None,
    ) -> None:
        self._content_override = content_service
        self._libraries = (
            libraries_repository
            if libraries_repository is not None
            else LibrariesRepository()
        )

    def _resolve_content(self) -> Any:
        """Resolve the content service shim lazily so route-module
        level ``patch(...content_svc)`` calls are honoured even
        when the Router instantiated this class at startup
        (before the patch). Returns the constructor override when
        a test passed one explicitly.
        """
        if self._content_override is not None:
            return self._content_override
        return content_svc

    @get("/api/libraries")
    def handle_libraries(self, handler: Any) -> None:
        """Return the merged ``{live, configured, source,
        media_server}`` library payload.

        Body delegated to ``LibrariesRepository.aggregate`` —
        keeps the merge rule (which is the only non-trivial
        transform on this route) testable without touching the
        Router. See the repository's ``aggregate`` docstring for
        the ``source`` tri-state rule.
        """
        handler._json_response(
            HTTPStatus.OK, self._libraries.aggregate(),
        )

    @get("/api/recent")
    def handle_recent(self, handler: Any) -> None:
        """Return the recently-added items per *arr service.

        ``content_svc.get_recent`` returns
        ``{"recent": {<service>: [...]}}`` where the service key
        identifies which *arr the entries came from. Empty arrays
        appear for services that are configured but currently
        quiet; missing services were never configured.
        """
        handler._json_response(
            HTTPStatus.OK, self._resolve_content().get_recent(),
        )

    @get("/api/import-lists")
    def handle_import_lists(self, handler: Any) -> None:
        """Return the per-*arr import-list catalogue.

        Shape: ``{"lists": {<service>: [{id, name, enabled,
        listType}, ...]}}``. The ``enabled`` field is nullable —
        ``null`` means the list exists but the operator hasn't
        toggled it either way (factory default).
        """
        handler._json_response(
            HTTPStatus.OK, self._resolve_content().get_import_lists(),
        )

    @get("/api/import-lists-all")
    def handle_import_lists_all(self, handler: Any) -> None:
        """Return the import-list catalogue aggregated across
        every *arr.

        Different shape from ``/api/import-lists`` — see
        ``contracts/api/openapi.yaml`` line 8541. The aggregate
        view is what the Lists page renders; the per-service
        view drives the per-*arr drill-down.
        """
        handler._json_response(
            HTTPStatus.OK,
            self._resolve_content().get_all_import_lists(),
        )

    @get("/api/quality-profiles")
    def handle_quality_profiles(self, handler: Any) -> None:
        """Return the per-*arr quality-profile catalogue.

        BARE PATH only. The parameterized sibling
        ``/api/quality-profiles/{service}`` is registered by
        ``routes/indexers_quality.py`` and stays there — Phase 2
        wave 4 explicitly migrates only the bare path.
        """
        handler._json_response(
            HTTPStatus.OK,
            self._resolve_content().get_quality_profiles(),
        )


__all__ = ["ContentListsGetRoutes", "LibrariesRepository"]
