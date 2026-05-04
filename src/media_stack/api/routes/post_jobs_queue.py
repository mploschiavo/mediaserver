"""Operator job-queue POST routes (ADR-0007 Phase 2 wave 6).

Migrates the four state-changing operator-job-queue endpoints off
the ``handlers_post.handle()`` elif chain onto the OpenAPI Router.
These routes are the operator-facing CRUD on the persisted queue
file (``$CONFIG_ROOT/.controller/queue.json``); the queue itself is
documented at length in ``services/job_queue.py``.

Routes:

* ``POST /api/jobs/queue``                    — enqueue an entry.
* ``POST /api/jobs/queue/clear``              — wipe every entry.
* ``POST /api/jobs/queue/{entry_id}/remove``  — drop one entry.
* ``POST /api/jobs/queue/{entry_id}/reorder`` — move up/down or to
  an absolute position.

The OpenAPI spec at ``contracts/api/openapi.yaml`` already declares
each path (path-param name is ``entry_id`` in the spec — keep
verbatim so the Router's startup spec-drift check passes; the
PR-task description called it ``queue_id`` but the actual spec +
handlers both use ``entry_id``).

OO discipline (ADR-0007 + project-wide rule):

* ``JobsQueuePostRoutes`` is a ``RouteModule`` subclass with
  instance methods only — no ``@staticmethod``, no loose top-level
  handler functions.
* Constructor-injects every collaborator with module-default
  fall-backs that preserve the Router's zero-arg auto-discovery.
  Tests pass stubs to swap behaviour without monkey-patching.
* Three named patterns isolate the concerns inlined into the
  legacy elif chain:

  * ``JobQueueRepository`` — Repository onto ``job_queue.enqueue``
    + ``job_queue.remove_entry`` + ``job_queue.reorder_entry``.
    Each adapter caches ONLY the constructor-injected callable;
    the default path does a fresh module attribute lookup per
    call so ``mock.patch`` of the canonical symbol takes effect
    (avoids the lazy-cache resolver shape from earlier waves).
  * ``QueueClearService`` — Adapter onto ``job_queue.clear_queue``.
    Lifted to its own collaborator because the legacy chain
    treats clear as a separate verb and tests assert the bulk
    semantic distinct from per-entry removal.
  * ``JobIdResolver`` — Strategy that parses + validates the
    integer ``entry_id`` path param. Owns the 400-on-non-int
    branch so the route bodies stay one-liners.

Anti-pattern guard rails (ADR-0007 wave-3+4 retros):

* No lazy-cache resolver shape — every adapter caches ONLY a
  constructor-injected callable. The default path does a fresh
  attribute lookup on the service module each call so
  ``mock.patch`` on the canonical symbol takes effect.
* No ratchet baseline bumps. Every collaborator default keeps the
  legacy class structure intact.

Security preservation (project memory bug-class:
``csrf_double_submit``):

* CSRF is enforced at server.py for every POST that flows through
  the legacy chain (``_global_preflight``). Routes migrated to the
  Router bypass that gate, so this module installs the same
  ``PostMutationGate`` Strategy used by ``post_admin_ops``. The
  gate is invoked at the top of every handler method; tests can
  pass a permissive stub to exercise business logic in isolation.
* Admin-only authz still flows through server.py's
  ``_controller_rbac.allows`` + ``_sudo_gate.allows`` checks,
  which run BEFORE the dispatcher. Audit-log writes stay on
  server.py's ``_audit_mutation`` post-dispatch hook (it fires
  on every HANDLED outcome so queue mutations get the audit row
  for free).
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any, Callable

from media_stack.api.routes.post_admin_ops import PostMutationGate
from media_stack.api.routing import RouteModule, post


# ---------------------------------------------------------------------------
# Constants — each value belongs to a named source-of-truth so
# string ratchets see one canonical site instead of inline magic
# strings scattered through the route bodies.
# ---------------------------------------------------------------------------

# Reorder ``direction`` accepted values. Same enum the legacy elif
# body forwarded to ``job_queue.reorder_entry``; the service layer
# does its own validation, but lifting the values here gives a
# single named site for the contract.
_REORDER_DIRECTION_UP = "up"
_REORDER_DIRECTION_DOWN = "down"

# Default ``source`` token for enqueue when the body omits it.
# Mirrors the legacy chain's literal ``"manual"`` fallback. Pulled
# out as a constant so the source-token enum has one canonical
# named site (``Job.normalize_source`` in the domain layer is the
# downstream consumer).
_DEFAULT_ENQUEUE_SOURCE = "manual"


# ---------------------------------------------------------------------------
# Adapter / Strategy / Repository collaborators
# ---------------------------------------------------------------------------


class JobQueueRepository:
    """Repository onto ``job_queue.enqueue`` + ``remove_entry`` +
    ``reorder_entry``.

    Each adapter caches ONLY a constructor-injected callable. The
    default path does a fresh module attribute lookup per call so
    ``mock.patch`` of the canonical symbol takes effect — avoids
    the lazy-cache resolver shape that earlier ADR-0007 waves had
    to retro-clean.
    """

    def __init__(
        self,
        enqueue_fn: Callable[..., dict[str, Any]] | None = None,
        remove_fn: Callable[[int], dict[str, Any]] | None = None,
        reorder_fn: Callable[..., dict[str, Any]] | None = None,
    ) -> None:
        self._enqueue = enqueue_fn
        self._remove = remove_fn
        self._reorder = reorder_fn

    def enqueue(
        self,
        job_name: str,
        *,
        source: str,
        scheduled_at: float,
        label: str,
    ) -> dict[str, Any]:
        if self._enqueue is not None:
            return self._enqueue(
                job_name,
                source=source,
                scheduled_at=scheduled_at,
                label=label,
            )
        from media_stack.api.services import job_queue as job_queue_svc
        return job_queue_svc.enqueue(
            job_name,
            source=source,
            scheduled_at=scheduled_at,
            label=label,
        )

    def remove(self, entry_id: int) -> dict[str, Any]:
        if self._remove is not None:
            return self._remove(entry_id)
        from media_stack.api.services import job_queue as job_queue_svc
        return job_queue_svc.remove_entry(entry_id)

    def reorder(self, entry_id: int, **kwargs: Any) -> dict[str, Any]:
        if self._reorder is not None:
            return self._reorder(entry_id, **kwargs)
        from media_stack.api.services import job_queue as job_queue_svc
        return job_queue_svc.reorder_entry(entry_id, **kwargs)


class QueueClearService:
    """Adapter onto ``job_queue.clear_queue``.

    Lifted as its own collaborator distinct from
    ``JobQueueRepository`` because the legacy chain treats the
    bulk-clear verb separately and tests assert that semantic
    distinct from per-entry removal. Same fresh-attribute-lookup
    default-path pattern.
    """

    def __init__(
        self,
        clear_fn: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self._clear = clear_fn

    def clear(self) -> dict[str, Any]:
        if self._clear is not None:
            return self._clear()
        from media_stack.api.services import job_queue as job_queue_svc
        return job_queue_svc.clear_queue()


class JobIdResolver:
    """Strategy that parses + validates the integer ``entry_id``
    path param.

    Returns ``(int_value, None)`` on success or
    ``(None, error_body)`` on a non-integer input. Owns the
    400-on-non-int branch so the route bodies stay one-liners.
    Constructor-injects nothing — there's no I/O collaborator
    here, only the parse-rule. The class exists (rather than a
    loose ``def``) to keep this module free of module-level
    functions per the codebase-wide ratchet.
    """

    def parse(
        self, raw: Any,
    ) -> tuple[int | None, dict[str, Any] | None]:
        try:
            return int(raw), None
        except (TypeError, ValueError):
            return None, {"error": "Invalid queue entry ID"}


class ReorderRequestParser:
    """Strategy that lifts ``direction`` + ``position`` off a
    JSON body into a kwargs dict suitable for
    ``JobQueueRepository.reorder``.

    Mirrors the legacy chain's "only forward keys the body
    actually supplied" semantic so a partial request doesn't
    accidentally null out the unmentioned field. The class exists
    rather than an inline-in-handler block so the kwargs-shaping
    rule has one named site + the route body is a one-liner.
    """

    def build(self, body: dict[str, Any]) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        if "direction" in body:
            kwargs["direction"] = str(body["direction"])
        if "position" in body:
            try:
                kwargs["position"] = int(body["position"])
            except (TypeError, ValueError):
                # Forward verbatim — service layer validates +
                # returns the canonical error envelope. Mirrors
                # legacy chain behaviour.
                kwargs["position"] = body["position"]
        return kwargs


# ---------------------------------------------------------------------------
# RouteModule
# ---------------------------------------------------------------------------


class JobsQueuePostRoutes(RouteModule):
    """Operator job-queue POST routes covering enqueue, clear,
    remove, and reorder.

    The Router auto-discovers + instantiates this class + walks
    its tagged methods at startup. Constructor defaults keep
    auto-discovery zero-arg while letting tests swap any
    collaborator.
    """

    def __init__(
        self,
        *,
        mutation_gate: PostMutationGate | None = None,
        queue_repository: JobQueueRepository | None = None,
        clear_service: QueueClearService | None = None,
        id_resolver: JobIdResolver | None = None,
        reorder_parser: ReorderRequestParser | None = None,
    ) -> None:
        self._gate = mutation_gate or PostMutationGate()
        self._repo = queue_repository or JobQueueRepository()
        self._clear = clear_service or QueueClearService()
        self._id_resolver = id_resolver or JobIdResolver()
        self._reorder_parser = reorder_parser or ReorderRequestParser()

    # --- gate helper ---------------------------------------------------

    def _gated(self, handler: Any) -> bool:
        """Run the CSRF gate; emit 403 + return False on rejection."""
        if not self._gate.verify(handler):
            self._gate.reject(handler)
            return False
        return True

    # --- routes --------------------------------------------------------

    @post("/api/jobs/queue")
    def handle_enqueue(self, handler: Any) -> None:
        """Append a new entry to the operator queue.

        Body: ``{job_name: str, source?: str, scheduled_at?: float,
        label?: str}``. ``source`` defaults to ``"manual"``;
        ``scheduled_at=0`` (default) means run ASAP — the field is
        informational only since the dispatcher integration isn't
        wired yet (see ``services/job_queue.py``).

        Validation errors flow through ``job_queue.enqueue``'s
        ``{"error": ...}`` envelope; an empty/missing ``job_name``
        is rejected at the service layer so the wire shape stays
        identical to the legacy chain.
        """
        if not self._gated(handler):
            return
        body = handler._read_json_body() or {}
        result = self._repo.enqueue(
            str(body.get("job_name", "")),
            source=str(body.get("source", _DEFAULT_ENQUEUE_SOURCE)),
            scheduled_at=float(body.get("scheduled_at", 0) or 0),
            label=str(body.get("label", "")),
        )
        handler._json_response(HTTPStatus.OK, result)

    @post("/api/jobs/queue/clear")
    def handle_clear(self, handler: Any) -> None:
        """Wipe every queued entry — admin escape hatch for a stuck
        queue.

        Returns ``{status: "cleared", count: N}`` where ``N`` is
        the count of entries dropped. Idempotent — calling on an
        empty queue returns ``count: 0``.
        """
        if not self._gated(handler):
            return
        handler._json_response(HTTPStatus.OK, self._clear.clear())

    @post("/api/jobs/queue/{entry_id}/remove")
    def handle_remove(
        self, handler: Any, *, entry_id: str,
    ) -> None:
        """Drop a queued entry by id.

        Path param is declared as ``integer`` in the spec but
        arrives as a string from the URL parser — validate +
        coerce here. Unknown ids return the service layer's
        ``{"error": "queue entry N not found"}`` envelope (200,
        not 404, mirroring the legacy chain).
        """
        if not self._gated(handler):
            return
        parsed_id, error = self._id_resolver.parse(entry_id)
        if error is not None:
            handler._json_response(HTTPStatus.BAD_REQUEST, error)
            return
        # ``parsed_id`` is guaranteed non-None here because
        # ``error is None`` is the success path; assert keeps the
        # type-checker happy without the extra runtime branch.
        assert parsed_id is not None
        result = self._repo.remove(parsed_id)
        handler._json_response(HTTPStatus.OK, result)

    @post("/api/jobs/queue/{entry_id}/reorder")
    def handle_reorder(
        self, handler: Any, *, entry_id: str,
    ) -> None:
        """Move a queued entry up/down by one slot or to an
        absolute index.

        Body: ``{direction?: "up"|"down", position?: int}``.
        Exactly one of the two should be supplied — the service
        layer rejects both-missing with ``{"error": ...}``.
        Out-of-bounds moves are clamped at the service layer so
        the UI can wire ``↑/↓`` buttons unconditionally.
        """
        if not self._gated(handler):
            return
        parsed_id, error = self._id_resolver.parse(entry_id)
        if error is not None:
            handler._json_response(HTTPStatus.BAD_REQUEST, error)
            return
        assert parsed_id is not None
        body = handler._read_json_body() or {}
        kwargs = self._reorder_parser.build(body)
        result = self._repo.reorder(parsed_id, **kwargs)
        handler._json_response(HTTPStatus.OK, result)


__all__ = [
    "JobIdResolver",
    "JobQueueRepository",
    "JobsQueuePostRoutes",
    "QueueClearService",
    "ReorderRequestParser",
]
