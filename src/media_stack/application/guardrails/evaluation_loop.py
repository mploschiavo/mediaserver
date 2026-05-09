"""Evaluation loop — wires the registry into the auto-heal cycle.

We don't spawn our own daemon thread. The auto-heal loop already
ticks every 60s and survives the lifecycle of the API server; piggy-
backing on it keeps the operational footprint minimal.

``tick()`` is the single public entry-point. The auto-heal loop calls
it after each cycle. Tests call it directly with a hand-rolled state
dict.

**Cadence throttle**: storage rules don't change at minute granularity
— floors flap when free space hovers near the limit, and an
already-triggered rule re-fires every 60s polluting the operator's
"Recent batches" view. ``MEDIA_STACK_GUARDRAIL_INTERVAL_SECONDS``
(default 300) gates evaluation: ticks within the window short-circuit
without writing history. Tests can override via the ``min_interval``
kwarg on ``tick()``.

History is recorded via the unified Job framework
(``record_run_start`` / ``record_run_complete``) since v1.0.284 —
the auto-heal loop now invokes ``run_job("guardrails:evaluate",
source="auto-heal")`` rather than calling ``tick()`` directly,
so JobRunner owns history. The legacy ``_record_history``
write inside ``_record()`` below is kept for backwards-compat
with any caller that still passes ``record_history=True``, but
the production path threads ``record_history=False`` to avoid
double-writes (the dashboard reads ``/api/runs`` for both
sources).
"""

from __future__ import annotations

import logging
import os
import sys
import time
from typing import Any, Mapping

from media_stack.domain.guardrails.protocols import Action, Trigger

from .registry import GuardrailRegistry, default as default_registry
from .state_collector import collect_state

_log = logging.getLogger("media_stack.guardrails")


# Action-name constants the dispatcher recognises. Other action
# names (notify, qbit_cleanup, error) fall through unchanged — they
# are handled by other systems (notification adapters, the legacy
# DiskGuardrailsService.enforce() runs on a separate media-hygiene
# job tick).
_ACTION_LOCKDOWN_ENGAGE = "lockdown_engage"
_ACTION_LOCKDOWN_RELEASE = "lockdown_release"


class EvaluationLoop:
    """Orchestrates one guardrail evaluation cycle.

    Per ADR-0012, all helpers are plain instance methods. Module-level
    aliases below preserve the import-as-function call sites; internal
    cross-method calls dispatch through ``sys.modules[__name__]`` so
    test ``mock.patch`` of module attributes still wins.
    """

    def _dispatch_actions(
        self,
        actions: list[Action],
        *,
        lockdown_service: Any | None,
        state: Mapping[str, Any],
    ) -> None:
        """Walk the action list and run side-effects for known names.

        Mutates each Action in place: ``ok`` is flipped to ``True``
        when the dispatch succeeded, and ``detail`` is updated with
        the post-dispatch summary so the audit log surfaces the
        actual outcome rather than the rule's pre-dispatch intent.

        Unknown action names are left untouched. ``lockdown_*`` actions
        are no-op'd (with a debug log) when ``lockdown_service`` is
        ``None`` — happens during early bootstrap before the wirer has
        handed the service in, and during unit tests that don't care
        about the lockdown layer.
        """
        mod = sys.modules[__name__]
        if lockdown_service is None:
            for action in actions:
                if action.action in (
                    _ACTION_LOCKDOWN_ENGAGE, _ACTION_LOCKDOWN_RELEASE,
                ):
                    _log.debug(
                        "guardrails: %s not dispatched — no lockdown "
                        "service registered", action.action,
                    )
            return

        for action in actions:
            if action.action == _ACTION_LOCKDOWN_ENGAGE:
                try:
                    trigger_label = mod._engage_trigger_label(state)
                    result = lockdown_service.engage(
                        trigger="auto",
                        by=trigger_label,
                    )
                except (OSError, RuntimeError, ValueError) as exc:
                    _log.warning(
                        "guardrails: lockdown engage dispatch failed: %s",
                        exc,
                    )
                    action.ok = False
                    action.detail = f"engage failed: {exc}"
                    continue
                paused = result.get("paused_clients") or []
                already = bool(result.get("already_engaged"))
                action.ok = bool(paused) or already
                if already:
                    action.detail = (
                        f"lockdown already engaged "
                        f"(trigger={result.get('trigger')})"
                    )
                else:
                    action.detail = (
                        f"paused {len(paused)} clients "
                        f"(failures={len(result.get('failures') or [])})"
                    )
            elif action.action == _ACTION_LOCKDOWN_RELEASE:
                try:
                    result = lockdown_service.release(
                        by="auto:disk-recovered",
                    )
                except (OSError, RuntimeError, ValueError) as exc:
                    _log.warning(
                        "guardrails: lockdown release dispatch failed: %s",
                        exc,
                    )
                    action.ok = False
                    action.detail = f"release failed: {exc}"
                    continue
                released = result.get("released_clients") or []
                was = bool(result.get("was_engaged"))
                action.ok = was
                if not was:
                    action.detail = "no-op: lockdown not engaged"
                else:
                    action.detail = (
                        f"released {len(released)} clients "
                        f"(failures={len(result.get('failures') or [])})"
                    )

    def _engage_trigger_label(self, state: Mapping[str, Any]) -> str:
        """Build a short human-readable actor string for the audit
        trail. Keeps the dispatch site free of disk-percentage
        formatting so the rule shape stays small."""
        disks = state.get("disk") or {}
        if isinstance(disks, dict) and disks:
            try:
                worst = max(
                    float(info.get("percent_used") or 0.0)
                    for info in disks.values()
                    if isinstance(info, dict)
                )
            except ValueError:
                worst = 0.0
            if worst > 0:
                return f"auto:disk-{worst:.0f}%"
        return "auto:disk-pressure"

    def _resolved_interval(self, min_interval: float | None) -> float:
        if min_interval is not None:
            return float(min_interval)
        raw = os.environ.get(
            "MEDIA_STACK_GUARDRAIL_INTERVAL_SECONDS", "300",
        )
        try:
            return float(raw)
        except ValueError:
            return 300.0

    def _record(
        self,
        triggers: list[Trigger],
        actions: list[Action],
        *,
        elapsed: float,
    ) -> None:
        """Push one history entry into the job-framework log per
        trigger so each fire is independently visible. We tag the
        source with the rule id so the dashboard's job-history table
        can render a guardrail badge."""
        if not triggers:
            return
        try:
            from media_stack.services.jobs.framework import _record_history
        except Exception as exc:  # noqa: BLE001
            _log.debug("guardrails: history module unavailable: %s", exc)
            return
        by_rule: dict[str, Action] = {a.rule_id: a for a in actions}
        for t in triggers:
            action = by_rule.get(t.rule_id)
            status = (
                "ok" if (action and action.ok)
                else "error" if t.severity == "critical"
                else "skipped"
            )
            try:
                _record_history(
                    {
                        "elapsed": elapsed,
                        "ok": 1 if status == "ok" else 0,
                        "skipped": 1 if status == "skipped" else 0,
                        "errors": 1 if status == "error" else 0,
                        "jobs": {
                            f"guardrail:{t.rule_id}": {
                                "status": status,
                                "elapsed": 0,
                            },
                        },
                    },
                    source=f"auto-heal:guardrail:{t.rule_id}",
                    actor="auto-heal",
                )
            except Exception as exc:  # noqa: BLE001
                _log.debug("guardrails: history write failed: %s", exc)

    def tick(
        self,
        *,
        registry: GuardrailRegistry | None = None,
        state: Mapping[str, Any] | None = None,
        failed_login_tracker: Any | None = None,
        record_history: bool = True,
        min_interval: float | None = None,
        lockdown_service: Any | None = None,
    ) -> dict[str, Any]:
        """Run one evaluation cycle. Returns the triggers + actions
        for consumers (the auto-heal loop discards the return value;
        tests inspect it).

        ``state`` is injectable so unit tests can avoid the live
        ``collect_state`` call entirely.
        ``record_history`` defaults True; tests pass False to keep the
        on-disk history file untouched.
        ``min_interval`` overrides the env-var-driven cadence floor;
        tests pass 0 to force every call to run.
        ``lockdown_service`` is the ``DownloadLockdownService``
        instance the dispatcher hands ``lockdown_engage`` /
        ``lockdown_release`` actions to. Production wires it in via
        the auto-heal loop's bootstrap; unit tests inject a fake or
        pass ``None`` to skip dispatch entirely."""
        mod = sys.modules[__name__]
        t0 = time.time()
        interval = mod._resolved_interval(min_interval)
        last = mod._last_tick_at
        if interval > 0 and (t0 - last) < interval:
            return {
                "ran_at": t0,
                "elapsed": 0.0,
                "triggers": [],
                "actions": [],
                "skipped": "throttled",
                "next_eligible_at": last + interval,
            }
        mod._last_tick_at = t0
        reg = registry or default_registry()
        snapshot: dict[str, Any] = (
            dict(state) if state is not None
            else collect_state(failed_login_tracker=failed_login_tracker)
        )
        # Inject per-rule merged thresholds into the state under the
        # ``_threshold:<id>`` key so each rule reads its operator
        # overrides without the rule needing to know about the
        # registry.
        for rule in reg.list_rules():
            snapshot[f"_threshold:{rule.id}"] = reg.threshold_for(rule.id)
        triggers = reg.evaluate_all(snapshot)
        actions = reg.remediate_all(triggers, snapshot)
        # Dispatch known action names to side-effecting services. The
        # rule's ``remediate`` returns an Action with ok=False meaning
        # "action needed but not yet executed"; the dispatcher flips
        # ok=True (and updates detail) after the side-effect runs so
        # the audit history records the post-dispatch outcome.
        mod._dispatch_actions(
            actions, lockdown_service=lockdown_service, state=snapshot,
        )
        elapsed = round(time.time() - t0, 3)
        if record_history:
            mod._record(triggers, actions, elapsed=elapsed)
        return {
            "ran_at": t0,
            "elapsed": elapsed,
            "triggers": [t.to_dict() for t in triggers],
            "actions": [a.to_dict() for a in actions],
        }

    def consecutive_warning_streaks(
        self,
        registry: GuardrailRegistry | None = None,
        *,
        min_streak: int = 2,
    ) -> list[dict[str, Any]]:
        """Return rule entries whose recent history shows ``severity
        >= warning`` for at least ``min_streak`` consecutive
        evaluation ticks. Used by the health-stories rule to emit one
        story per persistently-firing guardrail without re-running
        the rules."""
        reg = registry or default_registry()
        out: list[dict[str, Any]] = []
        for rule_status in reg.status_summary():
            sev = str(rule_status.get("last_severity") or "")
            streak = int(rule_status.get("last_severity_streak") or 0)
            if sev in ("warning", "critical") and streak >= min_streak:
                out.append({
                    "rule_id": rule_status["id"],
                    "domain": rule_status["domain"],
                    "description": rule_status["description"],
                    "severity": sev,
                    "streak": streak,
                })
        return out


# Last-tick timestamp shared across calls. Module-scope rather than
# bound to the EvaluationLoop instance because tests
# ``monkeypatch.setattr(evaluation_loop, "_last_tick_at", 0.0)`` to
# reset the cadence throttle; ``tick()`` reads/writes via
# ``sys.modules[__name__]`` so the module attribute is the source of
# truth.
_last_tick_at: float = 0.0


_INSTANCE = EvaluationLoop()

# Module-level aliases for every public name (ADR-0012). Internal
# cross-method calls inside ``EvaluationLoop`` route through
# ``sys.modules[__name__]`` so ``mock.patch`` on these names wins.
_dispatch_actions = _INSTANCE._dispatch_actions
_engage_trigger_label = _INSTANCE._engage_trigger_label
_resolved_interval = _INSTANCE._resolved_interval
_record = _INSTANCE._record
tick = _INSTANCE.tick
consecutive_warning_streaks = _INSTANCE.consecutive_warning_streaks
