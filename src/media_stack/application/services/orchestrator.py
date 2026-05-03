"""Promise orchestrator (see ADR-0003 + ADR-0005).

A :class:`PromiseOrchestrator` runs one or more orchestration ticks
of:

  1. Filter to promises applicable on the current platform
  2. Topologically sort by ``depends_on`` (cycles are CI-rejected by
     the schema ratchet but defensively re-checked here)
  3. For each topo-batch (promises whose deps are resolved):
     a. Skip those still in cooldown
     b. Probe in parallel (``ThreadPoolExecutor``, 8 workers)
     c. For each non-ok probe: dispatch the ensurer, then re-probe
     d. Record the per-promise ``PromiseAttempt`` to cooldown +
        emit a ``RunRecord`` with ``promise_id`` and
        ``source=orchestrator_shadow`` so operators can query the
        history per promise
  4. Persist cooldown state to JSON
  5. Emit a tick summary at INFO and return ``TickSummary``

ADR-0005 Phase 1 added :meth:`PromiseOrchestrator.tick_until_done`
(returns :class:`BlockingSummary`) for bootstrap-time use, where the
caller wants to loop ticks until every ``bootstrap_blocking=True``
promise reaches ``ok`` (or one fails permanently / a deadline
elapses).

Logging tiers (so operators can dial verbosity by deployment):

  INFO  — tick start/end, ensurer fired, state transitions
  WARN  — slow probes (>1s), repeated transient failures (>=3)
  ERROR — permanent failures, defensive topo-sort error
  DEBUG — per-promise ok results, cooldown skips

Every probe and ensurer call lands as a ``RunRecord``. The Jobs
page filters out ``source=orchestrator_shadow`` while shadow mode
is active (Phase 4c) so operators see only the legacy pipeline.
Phase 5 flips the filter; orchestrator becomes primary.

The module-level ``satisfy_promises`` and ``satisfy_promises_blocking``
functions are thin shims around the class so contract-resolved
callers (``handler:`` lookups in YAML, downstream importers) keep
working unchanged.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeoutError
from typing import Any, Callable, Iterable, Mapping

from media_stack.domain.services.lifecycle import Outcome, ProbeResult
from media_stack.domain.services.promises import (
    BlockingSummary,
    Promise,
    PromiseAttempt,
    PromiseStatus,
    TickSummary,
)
from media_stack.infrastructure.promises.cooldown import CooldownTracker
from media_stack.infrastructure.promises.dispatcher import (
    LifecycleResolver,
    dispatch_ensurer,
    dispatch_probe,
)
from media_stack.infrastructure.promises.registry import load_registry


logger = logging.getLogger(__name__)


_DEFAULT_PROBE_WORKERS = 8
_DEFAULT_PROBE_BATCH_TIMEOUT_SECONDS = 30.0  # bound for one parallel batch
_SLOW_PROBE_WARN_SECONDS = 1.0
_REPEATED_TRANSIENT_WARN_THRESHOLD = 3
_DEFAULT_BLOCKING_TIMEOUT_SECONDS = 240.0
_DEFAULT_BLOCKING_TICK_INTERVAL_SECONDS = 5.0


# ============================================================================
# PromiseGraph — topology + applicability over a set of promises
# ============================================================================


class PromiseGraph:
    """A registry-derived view that answers topology questions.

    Construct once per tick. Holds the input promises and the
    derived ``by_id`` index. The public surface is small on
    purpose:

      * :meth:`filter_applicable` drops platform-skipped promises
        and reports the count
      * :meth:`topological_levels` yields parallelism-safe groups in
        dependency order, or raises :class:`PromiseCycleError`
    """

    def __init__(self, promises: Iterable[Promise]) -> None:
        self._promises: list[Promise] = list(promises)
        self._by_id: dict[str, Promise] = {p.id: p for p in self._promises}

    def __iter__(self) -> Iterable[Promise]:
        return iter(self._promises)

    def __len__(self) -> int:
        return len(self._promises)

    def filter_applicable(
        self, platform: str,
    ) -> tuple["PromiseGraph", int]:
        """Return a new PromiseGraph containing only the promises that
        apply to ``platform``, plus the count of those that didn't."""
        applicable: list[Promise] = []
        skipped = 0
        for p in self._promises:
            if p.applies_to(platform):
                applicable.append(p)
            else:
                skipped += 1
        return PromiseGraph(applicable), skipped

    def topological_levels(self) -> Iterable[list[Promise]]:
        """Yield levels of promises whose deps are fully satisfied by
        an earlier level. Raises :class:`PromiseCycleError` if the
        ``depends_on`` graph contains a cycle (defensive — the
        registry-load schema ratchet should already have rejected
        it)."""
        order = self._topological_order()
        if order is None:
            raise PromiseCycleError(
                "depends_on graph contains a cycle "
                "(should be CI-blocked by promise-dispatch ratchet)",
            )
        depth: dict[str, int] = {}
        for p in order:
            if not p.depends_on:
                depth[p.id] = 0
                continue
            d = 0
            for dep in p.depends_on:
                if dep in depth:
                    d = max(d, depth[dep] + 1)
            depth[p.id] = d
        by_level: dict[int, list[Promise]] = {}
        for p in order:
            by_level.setdefault(depth[p.id], []).append(p)
        for d in sorted(by_level.keys()):
            yield by_level[d]

    def _topological_order(self) -> list[Promise] | None:
        in_degree = {p.id: 0 for p in self._promises}
        reverse: dict[str, set[str]] = {p.id: set() for p in self._promises}
        for p in self._promises:
            for dep in p.depends_on:
                if dep in self._by_id:
                    in_degree[p.id] += 1
                    reverse[dep].add(p.id)
        ready = [p for p in self._promises if in_degree[p.id] == 0]
        out: list[Promise] = []
        while ready:
            p = ready.pop()
            out.append(p)
            for dependent_id in reverse.get(p.id, ()):
                in_degree[dependent_id] -= 1
                if in_degree[dependent_id] == 0:
                    ready.append(self._by_id[dependent_id])
        if len(out) != len(self._promises):
            return None
        return out


class PromiseCycleError(Exception):
    """Raised by :meth:`PromiseGraph.topological_levels` when the
    ``depends_on`` graph contains a cycle. The orchestrator catches
    this and returns an empty :class:`TickSummary` with the cycle
    error logged at ERROR — same UX as the previous behavior, just
    expressed through an exception instead of a sentinel."""


# ============================================================================
# ProbeStatusInterpreter — pure mapping from probe/ensurer outcomes
# ============================================================================


class ProbeStatusInterpreter:
    """Maps ``ProbeResult`` and ``Outcome`` values into the typed
    ``PromiseStatus`` codes the orchestrator persists. Stateless —
    one shared instance is fine. Lifted out of the orchestrator so
    the mapping is independently testable and the orchestrator's
    method count stays focused on "tick choreography"."""

    def from_probe(self, result: ProbeResult) -> PromiseStatus:
        if result.status == "ok":
            return "ok"
        # Pure probe (no ensurer ran) — treat as transient by default.
        # The orchestrator's cooldown schedule retries on next tick.
        return "failed_transient" if result.status == "failed" else "unknown"

    def after_ensurer(
        self, reprobe: ProbeResult, ensure: Outcome[Any],
    ) -> PromiseStatus:
        if reprobe.is_ok:
            return "ok"
        if not ensure.ok and not ensure.transient:
            return "failed_permanent"
        return "failed_transient"

    def effective_dry_run(
        self,
        promise: Promise,
        *,
        dry_run: bool,
        live_services: frozenset[str] | None,
    ) -> bool:
        """Phase 5 staged-rollout: a promise's effective ``dry_run``
        flips to False when its service is in ``live_services``.
        Promises with no resolvable service id (file probes, infra
        ensurers) always honor the global flag."""
        if not dry_run or not live_services:
            return dry_run
        svc = promise.service_id
        return False if (svc and svc in live_services) else dry_run


# ============================================================================
# BlockingLoopGuard — predicates for the multi-tick blocking loop
# ============================================================================


class BlockingLoopGuard:
    """Decides when ``tick_until_done`` should stop.

    Holds the set of blocking promise ids for the current platform
    and exposes the three exit predicates the orchestrator's outer
    loop checks. Lifting this out of the orchestrator keeps the loop
    body short and makes the termination policy independently
    testable — the promise-id set is the only state, and each
    predicate is one observable behavior."""

    def __init__(self, blocking_ids: frozenset[str]) -> None:
        self._blocking_ids = blocking_ids

    @classmethod
    def for_platform(
        cls, registry: Iterable[Promise], platform: str,
    ) -> "BlockingLoopGuard":
        return cls(frozenset(
            p.id for p in registry
            if p.bootstrap_blocking and p.applies_to(platform)
        ))

    @property
    def blocking_ids(self) -> frozenset[str]:
        return self._blocking_ids

    def first_permanent_failure(
        self, attempts_by_id: Mapping[str, PromiseAttempt],
    ) -> str:
        """Return the first blocking promise that reached
        ``failed_permanent`` this tick, or ``""`` if none did."""
        for pid in self._blocking_ids:
            attempt = attempts_by_id.get(pid)
            if attempt and attempt.status == "failed_permanent":
                return pid
        return ""

    def is_satisfied(
        self, attempts_by_id: Mapping[str, PromiseAttempt],
    ) -> bool:
        """True iff every blocking promise reached ``ok`` this tick.
        Vacuously true when the blocking set is empty (registry has
        no bootstrap_blocking promises applicable to this platform)."""
        if not self._blocking_ids:
            return True
        return all(
            (a := attempts_by_id.get(pid)) is not None and a.status == "ok"
            for pid in self._blocking_ids
        )

    def ok_count(
        self, attempts_by_id: Mapping[str, PromiseAttempt],
    ) -> int:
        """How many blocking promises are currently ``ok`` (used for
        the "X/Y satisfied" log line on timeout)."""
        return sum(
            1 for pid in self._blocking_ids
            if (a := attempts_by_id.get(pid)) and a.status == "ok"
        )


# ============================================================================
# PromiseOrchestrator — the class that owns one tick and the blocking loop
# ============================================================================


class PromiseOrchestrator:
    """One-shot or multi-tick orchestrator.

    The class holds shared state for a single bootstrap or auto-heal
    invocation: the registry, lifecycle resolver, cooldown tracker,
    history emitter, and tunables. Tests inject their own resolver,
    cooldown, and history_emit so the class is exercisable without
    touching ``promise_state.json`` or the run-history file.

    Construct once per logical "session"; call :meth:`tick` for a
    single pass or :meth:`tick_until_done` for the blocking loop.
    The cooldown tracker accumulates state across ticks the same way
    the steady-state auto-heal loop does.
    """

    def __init__(
        self,
        *,
        registry: list[Promise] | None = None,
        resolver: LifecycleResolver | None = None,
        cooldown: CooldownTracker | None = None,
        history_emit: Any = None,
        status_interpreter: ProbeStatusInterpreter | None = None,
        workers: int = _DEFAULT_PROBE_WORKERS,
        slow_probe_warn_seconds: float = _SLOW_PROBE_WARN_SECONDS,
        probe_batch_timeout_seconds: float = _DEFAULT_PROBE_BATCH_TIMEOUT_SECONDS,
        repeated_transient_warn_threshold: int = _REPEATED_TRANSIENT_WARN_THRESHOLD,
    ) -> None:
        self._registry: list[Promise] | None = registry
        self._resolver: LifecycleResolver = resolver or LifecycleResolver()
        if cooldown is None:
            cooldown = CooldownTracker()
            cooldown.load()
        self._cooldown: CooldownTracker = cooldown
        self._history_emit: Callable[..., None] = (
            history_emit if history_emit is not None else _DefaultHistoryEmit()
        )
        self._status: ProbeStatusInterpreter = (
            status_interpreter or ProbeStatusInterpreter()
        )
        self._workers = workers
        self._slow_probe_warn_seconds = slow_probe_warn_seconds
        self._probe_batch_timeout_seconds = probe_batch_timeout_seconds
        self._repeated_transient_warn_threshold = repeated_transient_warn_threshold

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def tick(
        self,
        *,
        platform: str = "compose",
        secrets: Mapping[str, str] | None = None,
        dry_run: bool = False,
        live_services: frozenset[str] | None = None,
    ) -> TickSummary:
        """One orchestration tick.

        ``platform``      — ``compose`` | ``k8s``. Filters platform-
                            scoped promises.
        ``secrets``       — env-resolved secrets to pass into
                            ``OrchestrationContext`` (e.g.
                            SONARR_API_KEY). Default is the process
                            env (read by the lifecycles directly).
        ``dry_run``       — when True, ensurers are NOT called even
                            if probes fail. Used by the discrepancy
                            logger in Phase 4c to compare
                            orchestrator's view of the world to
                            legacy without mutating. Phase 5a:
                            ``dry_run=True`` with ``live_services``
                            set is the per-family rollout knob.
        ``live_services`` — Phase 5 staged-rollout allowlist. When
                            provided, the dry-run gate is RELAXED
                            for any promise whose probe.service (or
                            ensurer.service for LifecycleEnsurer) is
                            in the set: those promises run their
                            ensurers for real even when
                            ``dry_run=True``. Default ``None`` →
                            strict ``dry_run`` honored for every
                            promise.

        Returns a ``TickSummary`` with per-promise ``attempts``.
        """
        started = time.time()
        registry = self._resolved_registry()

        graph = PromiseGraph(registry)
        applicable, skipped_platform = graph.filter_applicable(platform)
        logger.info(
            "orchestrator tick start: platform=%s registry=%d applicable=%d "
            "(skipped_platform=%d)",
            platform, len(graph), len(applicable), skipped_platform,
        )

        attempts: dict[str, PromiseAttempt] = {}
        try:
            level_iter = applicable.topological_levels()
        except PromiseCycleError as exc:
            logger.error("orchestrator tick aborted: %s", exc)
            return TickSummary.empty(
                started_at=started, skipped_platform=skipped_platform,
            )

        # Walk in topological order, but probe IN PARALLEL within each
        # topo-level (promises whose deps are all resolved at this tick).
        for level in level_iter:
            ready = self._gate_level_by_cooldown(level, attempts=attempts)
            if not ready:
                continue
            probe_results = self._probe_level(ready, secrets=secrets)
            for promise, probe_result, probe_started, probe_elapsed in probe_results:
                effective_dry_run = self._status.effective_dry_run(
                    promise, dry_run=dry_run, live_services=live_services,
                )
                attempt = self._handle_probe_outcome(
                    promise=promise,
                    probe_result=probe_result,
                    probe_started=probe_started,
                    probe_elapsed=probe_elapsed,
                    secrets=secrets,
                    dry_run=effective_dry_run,
                )
                attempts[promise.id] = attempt

        self._cooldown.save()

        summary = TickSummary.from_attempts(
            started_at=started,
            skipped_platform=skipped_platform,
            attempts=attempts,
        )
        logger.info(
            "orchestrator tick complete in %.2fs: %s",
            summary.elapsed_seconds, summary.summary_line(),
        )
        return summary

    def tick_until_done(
        self,
        *,
        timeout_seconds: float = _DEFAULT_BLOCKING_TIMEOUT_SECONDS,
        tick_interval_seconds: float = _DEFAULT_BLOCKING_TICK_INTERVAL_SECONDS,
        platform: str = "compose",
        secrets: Mapping[str, str] | None = None,
        dry_run: bool = False,
        live_services: frozenset[str] | None = None,
        sleep: Callable[[float], Any] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> BlockingSummary:
        """Loop :meth:`tick` until all bootstrap-blocking promises are
        ``ok``, one fails permanently, or the timeout elapses
        (ADR-0005 Phase 1).

        ``timeout_seconds``       — total wall-clock budget. Returns
                                    ``BlockingSummary(timed_out=True)``
                                    if exhausted before all blocking
                                    promises are ``ok``.
        ``tick_interval_seconds`` — sleep between ticks. The default
                                    5s gives transient probes time to
                                    recover without busy-spinning.
        ``sleep`` / ``monotonic`` — injection points for tests; real
                                    callers leave them at the
                                    time-module defaults.

        Loop termination order, checked after every tick:

        1. Permanent failure of a blocking promise → return
           immediately with ``blocking_promises_ok=False`` and
           ``permanent_failure_id`` set.
        2. All blocking promises reached ``ok`` → return with
           ``blocking_promises_ok=True``.
        3. Deadline exceeded → return with ``timed_out=True``.
        4. Otherwise sleep ``tick_interval_seconds`` and tick again.
        """
        registry = self._resolved_registry()
        guard = BlockingLoopGuard.for_platform(registry, platform)

        started_monotonic = monotonic()
        deadline = started_monotonic + max(0.0, timeout_seconds)
        ticks = 0
        last_summary: TickSummary | None = None

        while True:
            last_summary = self.tick(
                platform=platform,
                secrets=secrets,
                dry_run=dry_run,
                live_services=live_services,
            )
            ticks += 1

            attempts_by_id: dict[str, PromiseAttempt] = {
                a.promise_id: a for a in last_summary.attempts
            }

            # 1. Permanent failure — abort.
            permanent_failure_id = guard.first_permanent_failure(attempts_by_id)
            if permanent_failure_id:
                logger.error(
                    "PromiseOrchestrator.tick_until_done: blocking promise "
                    "%s reached failed_permanent — aborting",
                    permanent_failure_id,
                )
                return BlockingSummary.at(
                    started_monotonic=started_monotonic,
                    now_monotonic=monotonic(),
                    ticks=ticks,
                    final_summary=last_summary,
                    timed_out=False,
                    blocking_promises_ok=False,
                    permanent_failure_id=permanent_failure_id,
                )

            # 2. All blocking promises ok (or no blocking promises) — done.
            if guard.is_satisfied(attempts_by_id):
                if guard.blocking_ids:
                    logger.info(
                        "PromiseOrchestrator.tick_until_done: all %d "
                        "blocking promises satisfied in %d tick(s)",
                        len(guard.blocking_ids), ticks,
                    )
                return BlockingSummary.at(
                    started_monotonic=started_monotonic,
                    now_monotonic=monotonic(),
                    ticks=ticks,
                    final_summary=last_summary,
                    timed_out=False,
                    blocking_promises_ok=True,
                )

            # 3. Deadline check before sleeping.
            now = monotonic()
            if now >= deadline:
                logger.warning(
                    "PromiseOrchestrator.tick_until_done: deadline reached "
                    "after %d tick(s); %d/%d blocking promises ok",
                    ticks,
                    guard.ok_count(attempts_by_id),
                    len(guard.blocking_ids),
                )
                return BlockingSummary.at(
                    started_monotonic=started_monotonic,
                    now_monotonic=now,
                    ticks=ticks,
                    final_summary=last_summary,
                    timed_out=True,
                    blocking_promises_ok=False,
                )

            # 4. Sleep up to the remaining budget, then tick again.
            remaining = deadline - now
            sleep(min(tick_interval_seconds, remaining))

    # ------------------------------------------------------------------
    # Per-promise lifecycle (probe → optional ensurer → re-probe)
    # ------------------------------------------------------------------

    def _handle_probe_outcome(
        self,
        *,
        promise: Promise,
        probe_result: ProbeResult,
        probe_started: float,
        probe_elapsed: float,
        secrets: Mapping[str, str] | None,
        dry_run: bool,
    ) -> PromiseAttempt:
        """Probe → if non-ok, run ensurer → re-probe → record."""
        if probe_elapsed > self._slow_probe_warn_seconds:
            logger.warning(
                "slow probe: %s took %.2fs (kind=%s)",
                promise.id, probe_elapsed, promise.probe.kind,
            )

        prev = self._cooldown.last_attempt(promise.id)
        state_changed = prev is None or prev.status != self._status.from_probe(probe_result)

        if probe_result.is_ok:
            attempt = self._record(
                promise.id,
                status="ok",
                detail=probe_result.detail,
                started_at=probe_started,
                elapsed=probe_elapsed,
                probe_evidence=dict(probe_result.evidence),
            )
            if state_changed:
                logger.info("promise %s: ok", promise.id)
            else:
                logger.debug(
                    "promise %s: ok (%dms)", promise.id, int(probe_elapsed * 1000),
                )
            self._emit_run_record(promise, attempt, phase="probe")
            return attempt

        # Probe failed or unknown.
        if dry_run:
            attempt = self._record(
                promise.id,
                status=self._status.from_probe(probe_result),
                detail=f"dry_run: {probe_result.detail}",
                started_at=probe_started,
                elapsed=probe_elapsed,
                probe_evidence=dict(probe_result.evidence),
            )
            self._emit_run_record(promise, attempt, phase="probe")
            return attempt

        # Run ensurer.
        logger.info(
            "ensurer fired: %s (kind=%s)", promise.id, promise.ensurer.kind,
        )
        ensure_started = time.time()
        ensure_outcome = dispatch_ensurer(
            promise.ensurer,
            resolver=self._resolver,
            now=ensure_started,
            secrets=secrets,
        )
        ensure_elapsed = time.time() - ensure_started

        # Re-probe to confirm whether the ensurer actually fixed it.
        reprobe_started = time.time()
        reprobe_result = dispatch_probe(
            promise.probe,
            resolver=self._resolver,
            now=reprobe_started,
            secrets=secrets,
        )
        reprobe_elapsed = time.time() - reprobe_started

        final_status = self._status.after_ensurer(reprobe_result, ensure_outcome)
        consec_failures = (prev.consecutive_failures + 1) if (
            prev is not None and prev.status not in (
                "ok", "skipped_cooldown", "skipped_platform",
            )
        ) else (1 if final_status != "ok" else 0)
        if (
            final_status != "ok"
            and consec_failures >= self._repeated_transient_warn_threshold
        ):
            logger.warning(
                "promise %s: %s failure attempt %d (last error: %s)",
                promise.id, final_status, consec_failures, reprobe_result.detail,
            )
        elif final_status == "failed_permanent":
            logger.error(
                "promise %s: permanent failure — operator action expected. "
                "ensurer: %s, re-probe: %s",
                promise.id, ensure_outcome.error or "ok", reprobe_result.detail,
            )
        elif final_status != "ok" and prev is not None and prev.status == "ok":
            logger.info("promise %s: ok → %s", promise.id, final_status)

        attempt = self._record(
            promise.id,
            status=final_status,
            detail=reprobe_result.detail,
            started_at=probe_started,
            elapsed=probe_elapsed + ensure_elapsed + reprobe_elapsed,
            probe_evidence=dict(reprobe_result.evidence),
            ensurer_fired=True,
            ensurer_attempts=ensure_outcome.attempts,
        )
        self._emit_run_record(promise, attempt, phase="ensure")
        return attempt

    # ------------------------------------------------------------------
    # Parallel probing
    # ------------------------------------------------------------------

    def _probe_level(
        self,
        promises: list[Promise],
        *,
        secrets: Mapping[str, str] | None,
    ) -> list[tuple[Promise, ProbeResult, float, float]]:
        """Probe a topological level in parallel. Returns a list of
        ``(promise, probe_result, started_at, elapsed)`` in promise-id
        order so the caller's downstream loop is deterministic."""
        if not promises:
            return []
        n_workers = max(1, min(self._workers, len(promises)))
        results: list[tuple[Promise, ProbeResult, float, float]] = []
        lock = threading.Lock()

        def _run(promise: Promise) -> None:
            started = time.time()
            try:
                r = dispatch_probe(
                    promise.probe,
                    resolver=self._resolver,
                    now=started,
                    secrets=secrets,
                )
            except Exception as exc:  # noqa: BLE001 - probes shouldn't raise
                r = ProbeResult.unknown(
                    f"probe dispatcher raised: {exc}",
                    evidence={"error": str(exc)},
                    evaluated_at=started,
                )
            elapsed = time.time() - started
            with lock:
                results.append((promise, r, started, elapsed))

        with ThreadPoolExecutor(max_workers=n_workers) as pool:
            futures = [pool.submit(_run, p) for p in promises]
            try:
                for f in futures:
                    f.result(timeout=self._probe_batch_timeout_seconds)
            except FutTimeoutError:
                logger.error(
                    "probe batch exceeded %ds — dispatcher may have hung. "
                    "Promises in this level: %s",
                    int(self._probe_batch_timeout_seconds),
                    [p.id for p in promises],
                )

        results.sort(key=lambda t: t[0].id)
        return results

    def _gate_level_by_cooldown(
        self,
        level: list[Promise],
        *,
        attempts: dict[str, PromiseAttempt],
    ) -> list[Promise]:
        """Filter a topo-level to promises that are NOT cooldown-skipped
        and whose deps haven't already failed in this tick. Records
        the dep-failed and cooldown-skipped attempts inline so callers
        get a complete attempts map even for skipped promises."""
        ready: list[Promise] = []
        for promise in level:
            dep_failure = promise.first_failed_dep(attempts)
            if dep_failure:
                attempt = self._record(
                    promise.id,
                    status="dep_failed",
                    detail=f"dep failed: {dep_failure}",
                    started_at=time.time(),
                    elapsed=0.0,
                )
                attempts[promise.id] = attempt
                self._emit_run_record(promise, attempt, phase="probe")
                continue
            if self._cooldown.is_in_cooldown(promise.id, time.time()):
                remaining = self._cooldown.remaining_cooldown_seconds(
                    promise.id, time.time(),
                )
                logger.debug(
                    "promise %s: cooldown active (%.1fs remaining), skipping",
                    promise.id, remaining,
                )
                attempt = self._record(
                    promise.id,
                    status="skipped_cooldown",
                    detail=f"cooldown active ({remaining:.1f}s remaining)",
                    started_at=time.time(),
                    elapsed=0.0,
                )
                attempts[promise.id] = attempt
                # Cooldown skips do NOT emit a run record — would be
                # 30+ rows per minute of pure noise.
                continue
            ready.append(promise)
        return ready

    # ------------------------------------------------------------------
    # Per-promise attempt recording
    # ------------------------------------------------------------------

    def _record(
        self,
        promise_id: str,
        *,
        status: PromiseStatus,
        detail: str,
        started_at: float,
        elapsed: float,
        probe_evidence: Mapping[str, Any] | None = None,
        ensurer_fired: bool = False,
        ensurer_attempts: int = 0,
    ) -> PromiseAttempt:
        raw = PromiseAttempt(
            promise_id=promise_id,
            status=status,
            started_at=started_at,
            elapsed_seconds=elapsed,
            detail=detail,
            probe_evidence=dict(probe_evidence or {}),
            ensurer_fired=ensurer_fired,
            ensurer_attempts=ensurer_attempts,
        )
        return self._cooldown.record_attempt(raw)

    def _emit_run_record(
        self,
        promise: Promise,
        attempt: PromiseAttempt,
        *,
        phase: str,
    ) -> None:
        """Record this attempt in run-history. Best-effort — a failure
        to write doesn't fail the orchestration tick."""
        try:
            self._history_emit(promise, attempt, phase)
        except Exception as exc:  # noqa: BLE001
            logger.debug("run-history emit failed for %s: %s", promise.id, exc)

    def _resolved_registry(self) -> list[Promise]:
        """Lazy-load the registry the first time we need it. Tests that
        pass an explicit ``registry=`` skip the YAML loader."""
        if self._registry is None:
            self._registry = load_registry()
        return self._registry


# ============================================================================
# Default history emitter — appends RunRecords to the JSONL log
# ============================================================================


class _DefaultHistoryEmit:
    """Default ``history_emit`` callable: appends a ``RunRecord`` to
    the existing run-history JSONL. Implemented as a class so unit
    tests can swap it for a no-op without dragging the persistence
    stack into the test fixture."""

    def __call__(
        self, promise: Promise, attempt: PromiseAttempt, phase: str,
    ) -> None:
        from media_stack.application.jobs.run_history import (
            record_run_complete, record_run_start,
        )
        from media_stack.domain.jobs.run_record import RunStatus

        job_name = f"orchestrator:{phase}:{promise.id}"
        rec = record_run_start(
            job_name,
            triggered_by="orchestrator_shadow",
            promise_id=promise.id,
        )
        history_status = (
            RunStatus.OK if attempt.status == "ok"
            else RunStatus.SKIPPED if attempt.status.startswith("skipped")
            else RunStatus.ERROR
        )
        record_run_complete(
            rec.run_id,
            status=history_status,
            error=(
                attempt.detail
                if attempt.status not in ("ok", "skipped_cooldown")
                else None
            ),
        )


# ============================================================================
# Module-level shims — preserve the existing function-call surface so
# contract-resolved handlers (orchestrator_satisfy.py, the auto-heal
# loop, the operator CLI) keep working without churn.
# ============================================================================


def satisfy_promises(
    registry: list[Promise] | None = None,
    *,
    platform: str = "compose",
    resolver: LifecycleResolver | None = None,
    cooldown: CooldownTracker | None = None,
    secrets: Mapping[str, str] | None = None,
    dry_run: bool = False,
    live_services: frozenset[str] | None = None,
    history_emit: Any = None,
    workers: int = _DEFAULT_PROBE_WORKERS,
) -> TickSummary:
    """Backwards-compat shim: delegates to a fresh
    :class:`PromiseOrchestrator` per call. Equivalent to
    ``PromiseOrchestrator(...).tick(...)`` and exists so existing
    callers (orchestrator_satisfy handler, CLI, tests) keep working
    unchanged.

    Construct a :class:`PromiseOrchestrator` directly when you need
    to share state across multiple ticks (the blocking loop in
    :meth:`PromiseOrchestrator.tick_until_done` already does)."""
    return PromiseOrchestrator(
        registry=registry,
        resolver=resolver,
        cooldown=cooldown,
        history_emit=history_emit,
        workers=workers,
    ).tick(
        platform=platform,
        secrets=secrets,
        dry_run=dry_run,
        live_services=live_services,
    )


def satisfy_promises_blocking(
    *,
    timeout_seconds: float = _DEFAULT_BLOCKING_TIMEOUT_SECONDS,
    tick_interval_seconds: float = _DEFAULT_BLOCKING_TICK_INTERVAL_SECONDS,
    sleep: Callable[[float], Any] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    registry: list[Promise] | None = None,
    platform: str = "compose",
    resolver: LifecycleResolver | None = None,
    cooldown: CooldownTracker | None = None,
    secrets: Mapping[str, str] | None = None,
    dry_run: bool = False,
    live_services: frozenset[str] | None = None,
    history_emit: Any = None,
    workers: int = _DEFAULT_PROBE_WORKERS,
) -> BlockingSummary:
    """Backwards-compat shim: delegates to
    :meth:`PromiseOrchestrator.tick_until_done`. ADR-0005 Phase 1."""
    return PromiseOrchestrator(
        registry=registry,
        resolver=resolver,
        cooldown=cooldown,
        history_emit=history_emit,
        workers=workers,
    ).tick_until_done(
        timeout_seconds=timeout_seconds,
        tick_interval_seconds=tick_interval_seconds,
        platform=platform,
        secrets=secrets,
        dry_run=dry_run,
        live_services=live_services,
        sleep=sleep,
        monotonic=monotonic,
    )


__all__ = [
    "PromiseOrchestrator",
    "satisfy_promises",
    "satisfy_promises_blocking",
]
