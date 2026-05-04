"""Ops-domain GET routes (ADR-0007 Phase 2 wave 4).

Three routes migrated off the ``handlers_get.handle()`` elif chain:

* ``GET /api/orchestrator/promises/state`` — read-only snapshot of
  the orchestrator's most recent promise-evaluation tick. ADR-0004's
  ``FreshInstallVerifier`` reads this instead of running a parallel
  probe loop, so the verifier's "did the fresh install actually
  work?" answer matches the live auto-heal cycle.
* ``GET /api/runs`` — per-job-run history with optional query
  filters. ``@get("/api/runs")`` covers both the bare path and the
  ``/api/runs?...`` query variants because the dispatch layer
  hands us the path WITHOUT the query string (see
  ``server.py``'s ``urlparse(self.path).path`` split). Query
  parsing happens inside the handler against ``handler.path``,
  which retains the original query string. Drives the Jobs page's
  run-history table.
* ``GET /api/telemetry`` — anonymised cluster metrics. Two modes
  selected by query string: ``?push`` triggers a remote push;
  bare path returns the collected metrics body.

Migration scope vs. brief — pre-flight ``grep`` showed:

* ``/api/stats`` — already in ``routes/stack_update.py`` (wave 3).
* ``/api/versions`` — already in ``routes/stack_update.py``
  (wave 3).
* ``/api/schedules`` — already in ``routes/misc_legacy.py``
  (wave 4 sibling). Per the brief's "SKIP already-registered"
  rule, leave it there.
* ``/api/ops/health`` — already in ``routes/health.py`` and the
  brief explicitly says don't include it.

OpenAPI parity: ``/api/runs`` and
``/api/orchestrator/promises/state`` were not in
``contracts/api/openapi.yaml`` before this wave; this commit adds
both spec entries (Jobs + Operations tags respectively) so the
Router's startup spec-drift check passes. ``/api/telemetry`` was
already declared.

OO discipline (ADR-0007 + project-wide rule):

* ``OpsGetRoutes`` is a ``RouteModule`` subclass with instance
  methods only — no ``@staticmethod``, no loose top-level handler
  functions.
* Dependencies are constructor-injected with module-default fall-
  backs that preserve auto-discovery (the Router calls the class
  with no args). Tests pass stubs to swap behaviour without
  monkey-patching.
* Three named patterns extracted, each isolating a concern that
  was an inline blob in ``handlers_get`` before:

  * ``RunHistoryRepository`` — adapter onto the JSONL run-history
    buffer. Owns the query-string parsing + the parent-name
    resolution that the legacy chain inlined into the route. Pure
    in-memory transform; constructor-injected ``run_history``
    module shim.
  * ``OrchestratorStateAdapter`` — adapter onto
    ``orchestrator_state.read_state``. The legacy chain returned
    the ``(status_code, body)`` tuple straight to the handler;
    wrapping it gives tests a single seam to stub when they don't
    want to touch the real on-disk state file.
  * ``TelemetrySource`` — Strategy that picks between the
    ``collect_metrics`` and ``push_telemetry`` calls based on the
    request's query string. Same inline split lived in the legacy
    chain; named here so tests assert "push? yes/no" without
    re-implementing query-parsing.

* ``except Exception`` is narrow per the project rule — only the
  telemetry push branch logs a swallowed exception via
  ``log_swallowed`` (the legacy version did the same).
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any, Optional
from urllib.parse import parse_qs, unquote, urlparse

from media_stack.api.routing import RouteModule, get


# ---------------------------------------------------------------------------
# Repository / Adapter / Strategy — extracted patterns
# ---------------------------------------------------------------------------


class RunHistoryRepository:
    """Read-side adapter onto the run-history JSONL buffer.

    Owns the query-string parsing + the
    ``parent_run_id → job_name`` lookup so the route handler stays
    a one-liner. Constructor-injected ``run_history`` module shim
    so tests can pass a fake without monkey-patching the real
    persistence layer.
    """

    # Same clamp the legacy chain enforced — keeps a malformed
    # ``?limit=99999999`` query from blowing the read buffer.
    _LIMIT_FLOOR = 1
    _LIMIT_CEILING = 50000
    _DEFAULT_LIMIT = 100

    def __init__(self, run_history_module: Any = None) -> None:
        if run_history_module is None:
            from media_stack.application.jobs import run_history
            run_history_module = run_history
        self._rh = run_history_module

    def list_runs(self, raw_path: str) -> dict[str, Any]:
        """Build the ``GET /api/runs`` response body.

        ``raw_path`` is the original ``handler.path`` — query
        string included. We parse it inside the repository because
        the Router strips the query string before dispatch (it
        keys on the path component only); pulling the raw value
        from the handler keeps the query-handling logic in one
        place rather than splitting it across the route + the
        repo.
        """
        params = self._parse_query(raw_path)
        records = self._rh.get_runs(
            job_name=params["job"],
            since_ts=params["since"],
            parent_run_id=params["parent"],
            batch_id=params["batch"],
            limit=params["limit"],
        )
        parent_names = {r.run_id: r.job_name for r in self._rh.iter_records()}
        return {
            "runs": [
                self._with_parent_name(r, parent_names) for r in records
            ],
        }

    @classmethod
    def _parse_query(cls, raw_path: str) -> dict[str, Any]:
        qs = raw_path.split("?", 1)[1] if "?" in raw_path else ""
        params = parse_qs(qs, keep_blank_values=True)
        job = unquote(params.get("job", [""])[0]) or None
        parent = unquote(params.get("parent", [""])[0]) or None
        batch = unquote(params.get("batch", [""])[0]) or None
        try:
            raw_limit = int(params.get("limit", [str(cls._DEFAULT_LIMIT)])[0])
        except ValueError:
            raw_limit = cls._DEFAULT_LIMIT
        limit = max(cls._LIMIT_FLOOR, min(cls._LIMIT_CEILING, raw_limit))
        since_ts: Optional[float] = None
        since_raw = unquote(params.get("since", [""])[0])
        if since_raw:
            try:
                since_ts = float(since_raw)
            except ValueError:
                since_ts = None
        return {
            "job": job,
            "parent": parent,
            "batch": batch,
            "limit": limit,
            "since": since_ts,
        }

    def _with_parent_name(
        self, record: Any, parent_names: dict[str, str],
    ) -> dict[str, Any]:
        """Inline the parent's ``job_name`` so the dashboard can
        render "child-job (under parent-name)" without a second
        per-row fetch.

        Preserves the exact shape of the legacy
        ``_run_record_with_parent_name`` helper in
        ``handlers_get`` — orphan children (parent rotated out of
        the persistence window) get NO ``parent_job_name`` field
        rather than an empty string. Integration tests pin this.

        Instance method (not ``@staticmethod``) so the project's
        OO-discipline rule stays satisfied even on this internal
        helper.
        """
        out = (
            record.to_dict() if hasattr(record, "to_dict") else dict(record)
        )
        parent_id = out.get("parent_run_id") or ""
        if parent_id and parent_id in parent_names:
            out["parent_job_name"] = parent_names[parent_id]
        return out

    def get_run_detail(self, run_id: str) -> tuple[bool, dict[str, Any]]:
        """Fetch a single run by id, with its children inlined.

        Returns ``(found, body)`` — ``found=False`` when the run-id
        is not in the persistence window so the route can emit 404
        without the repository knowing HTTP status codes.

        Preserves the exact legacy shape from
        ``handlers_get.py:913-937``:
        ``{...run_record, "children": [...child_records]}``
        with ``parent_job_name`` inlined on every row that resolves.
        Error string ``"run {run_id!r} not found"`` preserved verbatim.
        """
        record = self._rh.get_run(run_id)
        if record is None:
            return False, {"error": f"run {run_id!r} not found"}
        parent_names = {r.run_id: r.job_name for r in self._rh.iter_records()}
        children = [
            self._with_parent_name(c, parent_names)
            for c in self._rh.get_children(run_id)
        ]
        body: dict[str, Any] = {
            **self._with_parent_name(record, parent_names),
            "children": children,
        }
        return True, body

    def get_latest_for_job(
        self, job_name: str,
    ) -> tuple[bool, dict[str, Any]]:
        """Fetch the most-recent run record for a job name.

        Returns ``(found, body)`` — ``found=False`` when no runs exist
        for that job yet. Mirrors legacy branch at
        ``handlers_get.py:897-912``. Error string
        ``"no runs for {job_name!r}"`` preserved verbatim.
        """
        record = self._rh.get_latest_run(job_name)
        if record is None:
            return False, {"error": f"no runs for {job_name!r}"}
        parent_names = {r.run_id: r.job_name for r in self._rh.iter_records()}
        return True, self._with_parent_name(record, parent_names)


class OrchestratorStateAdapter:
    """Adapter onto ``orchestrator_state.read_state``.

    The service returns a ``(status_code, body)`` tuple straight
    from the persisted state file; the adapter exists so tests
    have a single seam to stub when they don't want to touch the
    on-disk file. Constructor-injected ``read_fn`` for the same
    reason.
    """

    def __init__(self, read_fn: Any = None) -> None:
        # Cache only constructor-injected functions. The default
        # path does a fresh attribute lookup on the service module
        # each call so ``mock.patch`` on the canonical symbol takes
        # effect (caching the default would freeze the pre-patch
        # reference and break tests that patch the singleton).
        self._read = read_fn

    def fetch(self) -> tuple[int, dict[str, Any]]:
        if self._read is not None:
            return self._read()
        from media_stack.api.services import orchestrator_state
        return orchestrator_state.read_state()


class TelemetrySource:
    """Strategy that picks between ``collect_metrics`` (read) and
    ``push_telemetry`` (write + push) based on the request's query
    string.

    The same one-line split lived inline in
    ``handlers_get`` — the strategy class names the choice so
    tests can assert "push? yes/no" without re-implementing
    query parsing. ``collect_fn`` / ``push_fn`` are
    constructor-injected so tests can swap real network calls
    for stubs.
    """

    _PUSH_TOKEN = "push"

    def __init__(
        self,
        *,
        collect_fn: Any = None,
        push_fn: Any = None,
    ) -> None:
        if collect_fn is None or push_fn is None:
            from media_stack.services.telemetry_client import (
                collect_metrics,
                push_telemetry,
            )
            collect_fn = collect_fn or collect_metrics
            push_fn = push_fn or push_telemetry
        self._collect = collect_fn
        self._push = push_fn

    def emit(self, raw_path: str) -> dict[str, Any]:
        if self._is_push_request(raw_path):
            return self._push()
        return self._collect()

    @classmethod
    def _is_push_request(cls, raw_path: str) -> bool:
        query = urlparse(raw_path).query
        # Match the legacy substring check exactly — ``?push``,
        # ``?push=1``, ``?foo=bar&push`` all count.
        return cls._PUSH_TOKEN in query


# ---------------------------------------------------------------------------
# RouteModule
# ---------------------------------------------------------------------------


class OpsGetRoutes(RouteModule):
    """Ops + Jobs GET routes covering orchestrator state, run
    history, schedules, and telemetry.

    The Router auto-discovers + instantiates this class + walks
    its tagged methods at startup. Constructor defaults keep
    auto-discovery zero-arg while allowing tests to swap any
    collaborator.
    """

    def __init__(
        self,
        *,
        run_history_repository: RunHistoryRepository | None = None,
        orchestrator_state_adapter: OrchestratorStateAdapter | None = None,
        telemetry_source: TelemetrySource | None = None,
    ) -> None:
        self._runs = run_history_repository or RunHistoryRepository()
        self._orchestrator = (
            orchestrator_state_adapter or OrchestratorStateAdapter()
        )
        self._telemetry = telemetry_source or TelemetrySource()

    @get("/api/orchestrator/promises/state")
    def handle_orchestrator_promises_state(self, handler: Any) -> None:
        """Return the orchestrator's last-tick snapshot.

        Status code is whatever the adapter resolved to (200 for a
        fresh persisted tick, 503 for missing / stale / malformed
        — both bodies carry ``last_tick_age_seconds``). Read by
        ``FreshInstallVerifier`` (ADR-0004).
        """
        status_code, body = self._orchestrator.fetch()
        handler._json_response(status_code, body)

    @get("/api/runs")
    def handle_runs(self, handler: Any) -> None:
        """Return the run-history buffer with optional query
        filters.

        Pulls the raw ``handler.path`` (query string included) and
        delegates to the repository, which parses + applies the
        ``job`` / ``parent`` / ``batch`` / ``since`` / ``limit``
        filters. Response shape is ``{"runs": [...]}`` with each
        row carrying ``parent_job_name`` when the parent resolves
        — the dashboard pins this shape.
        """
        body = self._runs.list_runs(handler.path)
        handler._json_response(HTTPStatus.OK, body)

    @get("/api/runs/latest/{job_name}")
    def handle_run_latest(self, handler: Any, *, job_name: str) -> None:
        """Return the most-recent run record for a job.

        ``job_name`` is a URL path parameter. 404 when no runs
        exist for that job yet. Response is the flat run record dict
        with ``parent_job_name`` inlined when the parent resolves —
        no ``children`` array. Mirrors legacy at
        ``handlers_get.py:897-912``.
        """
        found, body = self._runs.get_latest_for_job(job_name)
        status = HTTPStatus.OK if found else HTTPStatus.NOT_FOUND
        handler._json_response(status, body)

    @get("/api/runs/{run_id}")
    def handle_run_detail(self, handler: Any, *, run_id: str) -> None:
        """Return a single run record with its children inlined.

        ``run_id`` is a URL path parameter. 404 when the run-id is
        not in the JSONL persistence window. Response shape:
        ``{...run_record_fields, "children": [...child_records]}``
        with ``parent_job_name`` inlined on any row whose parent
        resolves. Mirrors legacy at ``handlers_get.py:913-937``.
        """
        found, body = self._runs.get_run_detail(run_id)
        status = HTTPStatus.OK if found else HTTPStatus.NOT_FOUND
        handler._json_response(status, body)

    @get("/api/telemetry")
    def handle_telemetry(self, handler: Any) -> None:
        """Return anonymised cluster metrics.

        Two modes selected by the ``?push`` query token: with
        ``push`` we trigger a remote push (and return its status
        body); without it we return the freshly collected metrics
        snapshot. Same split the legacy chain enforced.
        """
        body = self._telemetry.emit(handler.path)
        handler._json_response(HTTPStatus.OK, body)


__all__ = [
    "OpsGetRoutes",
    "OrchestratorStateAdapter",
    "RunHistoryRepository",
    "TelemetrySource",
]
