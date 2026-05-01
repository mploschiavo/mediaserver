"""Promise orchestrator — ADR-0003 Phase 4b.

``satisfy_promises(registry, ...)`` is one tick of:

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

Logging tiers (so operators can dial verbosity by deployment):

  INFO  — tick start/end, ensurer fired, state transitions
  WARN  — slow probes (>1s), repeated transient failures (>=3)
  ERROR — permanent failures, defensive topo-sort error
  DEBUG — per-promise ok results, cooldown skips

Every probe and ensurer call lands as a ``RunRecord``. The Jobs
page filters out ``source=orchestrator_shadow`` while shadow mode
is active (Phase 4c) so operators see only the legacy pipeline.
Phase 5 flips the filter; orchestrator becomes primary.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeoutError
from typing import Any, Iterable, Mapping, Optional

from media_stack.domain.services.lifecycle import Outcome, ProbeResult
from media_stack.domain.services.promises import (
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


# ============================================================================
# Public entry points
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
    """One orchestration tick.

    ``registry``      — list of typed Promises (loaded from YAML if
                        not passed). Tests pass synthetic registries.
    ``platform``      — ``compose`` | ``k8s``. Filters platform-
                        scoped promises.
    ``resolver``      — lifecycle class resolver. Default is one
                        instance per call (no cross-call state).
    ``cooldown``      — backoff tracker. Default loads from
                        ``promise_state.json`` automatically; tests
                        pass an in-memory tracker with a tmp path.
    ``secrets``       — env-resolved secrets to pass into
                        ``OrchestrationContext`` (e.g. SONARR_API_KEY).
                        Default is the process env (read by the
                        lifecycles directly).
    ``dry_run``       — when True, ensurers are NOT called even if
                        probes fail. Used by the discrepancy logger
                        in Phase 4c to compare orchestrator's view
                        of the world to legacy without mutating.
                        Phase 5a: ``dry_run=True`` with ``live_services``
                        set is the per-family rollout knob — see
                        ``live_services`` below.
    ``live_services`` — Phase 5 staged-rollout allowlist. When
                        provided, the dry-run gate is RELAXED for any
                        promise whose probe.service (or
                        ensurer.service for LifecycleEnsurer) is in
                        the set: those promises run their ensurers
                        for real even when ``dry_run=True``. All
                        other promises continue to dry-run-shadow.
                        Default ``None`` → strict ``dry_run`` honored
                        for every promise. Phase 5a deploy sets
                        ``frozenset({"jellyfin"})``; Phase 5b adds
                        the Servarr family; etc. Promises with no
                        service id (file probes, k8s probes,
                        infra ensurers) always honor the global
                        ``dry_run`` flag.
    ``history_emit``  — optional callable ``(RunRecord) -> None``
                        invoked for each probe + ensurer outcome.
                        Default is the run-history append.
    ``workers``       — ThreadPoolExecutor size for parallel probes.

    Returns a ``TickSummary`` with per-promise ``attempts``.
    """
    started = time.time()
    if registry is None:
        registry = load_registry()
    if resolver is None:
        resolver = LifecycleResolver()
    if cooldown is None:
        cooldown = CooldownTracker()
        cooldown.load()
    if history_emit is None:
        history_emit = _default_history_emit

    applicable, skipped_platform = _filter_applicable(registry, platform)
    logger.info(
        "orchestrator tick start: platform=%s registry=%d applicable=%d "
        "(skipped_platform=%d)",
        platform, len(registry), len(applicable), skipped_platform,
    )

    order = _topological_order(applicable)
    if order is None:
        logger.error(
            "orchestrator tick aborted: depends_on graph contains a cycle "
            "(should be CI-blocked by promise-dispatch ratchet)",
        )
        return _empty_summary(started, skipped_platform)

    by_id = {p.id: p for p in applicable}
    attempts: dict[str, PromiseAttempt] = {}

    # Walk in topological order, but probe IN PARALLEL within each
    # topo-level (promises whose deps are all resolved at this tick).
    for level in _topological_levels(order, by_id):
        # Two passes per level: cooldown gate, then probe + ensurer.
        ready: list[Promise] = []
        for promise in level:
            # Skip when any depends_on failed in this tick.
            dep_failure = _first_failed_dep(promise, attempts)
            if dep_failure:
                attempt = _record(
                    cooldown, promise.id,
                    status="dep_failed",
                    detail=f"dep failed: {dep_failure}",
                    started_at=time.time(),
                    elapsed=0.0,
                )
                attempts[promise.id] = attempt
                _emit_run_record(history_emit, promise, attempt, phase="probe")
                continue
            if cooldown.is_in_cooldown(promise.id, time.time()):
                remaining = cooldown.remaining_cooldown_seconds(
                    promise.id, time.time(),
                )
                logger.debug(
                    "promise %s: cooldown active (%.1fs remaining), skipping",
                    promise.id, remaining,
                )
                attempt = _record(
                    cooldown, promise.id,
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

        if not ready:
            continue

        # Probe everything in this level in parallel.
        probe_results = _probe_level(
            ready, resolver=resolver, secrets=secrets, workers=workers,
        )

        for promise, probe_result, probe_started, probe_elapsed in probe_results:
            # Phase 5 staged rollout: a promise's effective dry_run
            # flips to False when its service is in ``live_services``.
            # Promises with no resolvable service id (file probes,
            # infra ensurers) always honor the global flag.
            effective_dry_run = dry_run
            if dry_run and live_services:
                svc = _promise_service(promise)
                if svc and svc in live_services:
                    effective_dry_run = False
            attempt = _handle_probe_outcome(
                promise=promise,
                probe_result=probe_result,
                probe_started=probe_started,
                probe_elapsed=probe_elapsed,
                resolver=resolver,
                cooldown=cooldown,
                secrets=secrets,
                dry_run=effective_dry_run,
                history_emit=history_emit,
            )
            attempts[promise.id] = attempt

    cooldown.save()

    summary = _build_summary(started, skipped_platform, attempts)
    logger.info(
        "orchestrator tick complete in %.2fs: %s",
        summary.elapsed_seconds, summary.summary_line(),
    )
    return summary


# ============================================================================
# Probe + ensurer per-promise handling
# ============================================================================


def _handle_probe_outcome(
    *,
    promise: Promise,
    probe_result: ProbeResult,
    probe_started: float,
    probe_elapsed: float,
    resolver: LifecycleResolver,
    cooldown: CooldownTracker,
    secrets: Mapping[str, str] | None,
    dry_run: bool,
    history_emit: Any,
) -> PromiseAttempt:
    """Probe → if non-ok, run ensurer → re-probe → record."""
    if probe_elapsed > _SLOW_PROBE_WARN_SECONDS:
        logger.warning(
            "slow probe: %s took %.2fs (kind=%s)",
            promise.id, probe_elapsed, promise.probe.kind,
        )

    prev = cooldown.last_attempt(promise.id)
    state_changed = prev is None or prev.status != _probe_to_status(probe_result)

    if probe_result.is_ok:
        attempt = _record(
            cooldown, promise.id,
            status="ok",
            detail=probe_result.detail,
            started_at=probe_started,
            elapsed=probe_elapsed,
            probe_evidence=dict(probe_result.evidence),
        )
        if state_changed:
            logger.info("promise %s: ok", promise.id)
        else:
            logger.debug("promise %s: ok (%dms)", promise.id, int(probe_elapsed * 1000))
        _emit_run_record(history_emit, promise, attempt, phase="probe")
        return attempt

    # Probe failed or unknown.
    if dry_run:
        attempt = _record(
            cooldown, promise.id,
            status=_probe_to_status(probe_result),
            detail=f"dry_run: {probe_result.detail}",
            started_at=probe_started,
            elapsed=probe_elapsed,
            probe_evidence=dict(probe_result.evidence),
        )
        _emit_run_record(history_emit, promise, attempt, phase="probe")
        return attempt

    # Run ensurer.
    logger.info(
        "ensurer fired: %s (kind=%s)", promise.id, promise.ensurer.kind,
    )
    ensure_started = time.time()
    ensure_outcome = dispatch_ensurer(
        promise.ensurer, resolver=resolver, now=ensure_started, secrets=secrets,
    )
    ensure_elapsed = time.time() - ensure_started

    # Re-probe to confirm whether the ensurer actually fixed it.
    reprobe_started = time.time()
    reprobe_result = dispatch_probe(
        promise.probe, resolver=resolver, now=reprobe_started, secrets=secrets,
    )
    reprobe_elapsed = time.time() - reprobe_started

    final_status = _final_status_after_ensurer(reprobe_result, ensure_outcome)
    consec_failures = (prev.consecutive_failures + 1) if (
        prev is not None and prev.status not in ("ok", "skipped_cooldown",
                                                  "skipped_platform")
    ) else (1 if final_status != "ok" else 0)
    if final_status != "ok" and consec_failures >= _REPEATED_TRANSIENT_WARN_THRESHOLD:
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

    attempt = _record(
        cooldown, promise.id,
        status=final_status,
        detail=reprobe_result.detail,
        started_at=probe_started,
        elapsed=probe_elapsed + ensure_elapsed + reprobe_elapsed,
        probe_evidence=dict(reprobe_result.evidence),
        ensurer_fired=True,
        ensurer_attempts=ensure_outcome.attempts,
    )
    _emit_run_record(history_emit, promise, attempt, phase="ensure")
    return attempt


def _probe_to_status(result: ProbeResult) -> PromiseStatus:
    if result.status == "ok":
        return "ok"
    # Pure probe (no ensurer ran) — treat as transient by default.
    # The orchestrator's cooldown schedule retries on next tick.
    return "failed_transient" if result.status == "failed" else "unknown"


def _final_status_after_ensurer(
    reprobe: ProbeResult, ensure: Outcome[Any],
) -> PromiseStatus:
    if reprobe.is_ok:
        return "ok"
    if not ensure.ok and not ensure.transient:
        return "failed_permanent"
    return "failed_transient"


# ============================================================================
# Topological sort + parallel probe execution
# ============================================================================


def _topological_order(promises: list[Promise]) -> list[Promise] | None:
    """Returns the topologically-sorted list, or ``None`` on cycle."""
    by_id = {p.id: p for p in promises}
    in_degree = {p.id: 0 for p in promises}
    reverse: dict[str, set[str]] = {p.id: set() for p in promises}
    for p in promises:
        for dep in p.depends_on:
            if dep in by_id:
                in_degree[p.id] += 1
                reverse[dep].add(p.id)
    ready = [p for p in promises if in_degree[p.id] == 0]
    out: list[Promise] = []
    while ready:
        p = ready.pop()
        out.append(p)
        for dependent_id in reverse.get(p.id, ()):
            in_degree[dependent_id] -= 1
            if in_degree[dependent_id] == 0:
                ready.append(by_id[dependent_id])
    if len(out) != len(promises):
        return None
    return out


def _topological_levels(
    order: list[Promise], by_id: Mapping[str, Promise],
) -> Iterable[list[Promise]]:
    """Group the topo-sorted list into "all promises whose deps are
    in earlier levels". Promises in the same level can probe in
    parallel; promises in later levels wait for their deps to land.

    Builds levels by computing each promise's depth in the dep
    graph; promises at the same depth go into the same level.
    """
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


def _probe_level(
    promises: list[Promise],
    *,
    resolver: LifecycleResolver,
    secrets: Mapping[str, str] | None,
    workers: int,
) -> list[tuple[Promise, ProbeResult, float, float]]:
    """Probe a topological level in parallel. Returns a list of
    ``(promise, probe_result, started_at, elapsed)`` in promise-id
    order so the caller's downstream loop is deterministic."""
    if not promises:
        return []
    n_workers = max(1, min(workers, len(promises)))
    results: list[tuple[Promise, ProbeResult, float, float]] = []
    lock = threading.Lock()

    def _run(promise: Promise) -> None:
        started = time.time()
        try:
            r = dispatch_probe(
                promise.probe, resolver=resolver, now=started, secrets=secrets,
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
                f.result(timeout=_DEFAULT_PROBE_BATCH_TIMEOUT_SECONDS)
        except FutTimeoutError:
            logger.error(
                "probe batch exceeded %ds — dispatcher may have hung. "
                "Promises in this level: %s",
                int(_DEFAULT_PROBE_BATCH_TIMEOUT_SECONDS),
                [p.id for p in promises],
            )

    results.sort(key=lambda t: t[0].id)
    return results


# ============================================================================
# Helpers
# ============================================================================


def _promise_service(promise: Promise) -> str | None:
    """The service id this promise pertains to, for staged-rollout
    allowlist gating. Reads probe.service first (the most direct
    signal), then falls back to ensurer.service for LifecycleEnsurers.

    Returns ``None`` when the promise has no service-bound probe
    (file probes, k8s_resource probes, infra ensurers) — those
    always honor the global ``dry_run`` flag.
    """
    probe = promise.probe
    if hasattr(probe, "service") and getattr(probe, "service", ""):
        return str(probe.service).strip().lower() or None
    ensurer = promise.ensurer
    if hasattr(ensurer, "service") and getattr(ensurer, "service", ""):
        return str(ensurer.service).strip().lower() or None
    return None


def _filter_applicable(
    registry: list[Promise], platform: str,
) -> tuple[list[Promise], int]:
    out = []
    skipped = 0
    for p in registry:
        if p.applies_to(platform):
            out.append(p)
        else:
            skipped += 1
    return out, skipped


def _first_failed_dep(
    promise: Promise, attempts: Mapping[str, PromiseAttempt],
) -> Optional[str]:
    for dep in promise.depends_on:
        a = attempts.get(dep)
        if a and a.status not in ("ok", "skipped_cooldown", "skipped_platform"):
            return dep
    return None


def _record(
    cooldown: CooldownTracker,
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
    return cooldown.record_attempt(raw)


def _build_summary(
    started: float,
    skipped_platform: int,
    attempts: Mapping[str, PromiseAttempt],
) -> TickSummary:
    counts = {
        "ok": 0,
        "failed_transient": 0,
        "failed_permanent": 0,
        "dep_failed": 0,
        "skipped_cooldown": 0,
        "skipped_platform": skipped_platform,
        "unknown": 0,
    }
    for a in attempts.values():
        if a.status in counts:
            counts[a.status] += 1
    return TickSummary(
        started_at=started,
        elapsed_seconds=time.time() - started,
        total=len(attempts) + skipped_platform,
        ok=counts["ok"],
        failed_transient=counts["failed_transient"],
        failed_permanent=counts["failed_permanent"],
        dep_failed=counts["dep_failed"],
        skipped_cooldown=counts["skipped_cooldown"],
        skipped_platform=counts["skipped_platform"],
        unknown=counts["unknown"],
        attempts=tuple(attempts.values()),
    )


def _empty_summary(started: float, skipped_platform: int) -> TickSummary:
    return TickSummary(
        started_at=started,
        elapsed_seconds=time.time() - started,
        total=0,
        ok=0,
        failed_transient=0,
        failed_permanent=0,
        dep_failed=0,
        skipped_cooldown=0,
        skipped_platform=skipped_platform,
        unknown=0,
    )


def _emit_run_record(
    history_emit: Any,
    promise: Promise,
    attempt: PromiseAttempt,
    *,
    phase: str,
) -> None:
    """Record this attempt in run-history. Best-effort — a failure to
    write doesn't fail the orchestration tick."""
    try:
        history_emit(promise, attempt, phase)
    except Exception as exc:  # noqa: BLE001
        logger.debug("run-history emit failed for %s: %s", promise.id, exc)


def _default_history_emit(
    promise: Promise, attempt: PromiseAttempt, phase: str,
) -> None:
    """Emit a ``RunRecord`` to the existing run-history JSONL. Late-
    imports the module so unit tests that pass a custom
    ``history_emit`` don't pull the persistence stack."""
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
        error=attempt.detail if attempt.status not in ("ok", "skipped_cooldown") else None,
    )


__all__ = ["satisfy_promises"]
