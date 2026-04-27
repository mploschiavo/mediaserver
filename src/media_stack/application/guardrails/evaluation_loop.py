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
import time
from typing import Any, Mapping

from media_stack.domain.guardrails.protocols import Action, Trigger

from .registry import GuardrailRegistry, default as default_registry
from .state_collector import collect_state

_log = logging.getLogger("media_stack.guardrails")

# Last-tick timestamp shared across calls. Module-scope rather than
# bound to a service instance because the tick() function is the
# public entry; the auto-heal loop calls it directly without going
# through a class. Tests override via the ``min_interval`` kwarg.
_last_tick_at: float = 0.0


def _resolved_interval(min_interval: float | None) -> float:
    if min_interval is not None:
        return float(min_interval)
    raw = os.environ.get("MEDIA_STACK_GUARDRAIL_INTERVAL_SECONDS", "300")
    try:
        return float(raw)
    except ValueError:
        return 300.0


def _record(
    triggers: list[Trigger], actions: list[Action],
    *, elapsed: float,
) -> None:
    """Push one history entry into the job-framework log per trigger
    so each fire is independently visible. We tag the source with
    the rule id so the dashboard's job-history table can render a
    guardrail badge."""
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
        status = "ok" if (action and action.ok) else "error" if t.severity == "critical" else "skipped"
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
    *,
    registry: GuardrailRegistry | None = None,
    state: Mapping[str, Any] | None = None,
    failed_login_tracker: Any | None = None,
    record_history: bool = True,
    min_interval: float | None = None,
) -> dict[str, Any]:
    """Run one evaluation cycle. Returns the triggers + actions for
    consumers (the auto-heal loop discards the return value; tests
    inspect it).

    ``state`` is injectable so unit tests can avoid the live
    ``collect_state`` call entirely.
    ``record_history`` defaults True; tests pass False to keep the
    on-disk history file untouched.
    ``min_interval`` overrides the env-var-driven cadence floor;
    tests pass 0 to force every call to run."""
    global _last_tick_at
    t0 = time.time()
    interval = _resolved_interval(min_interval)
    if interval > 0 and (t0 - _last_tick_at) < interval:
        return {
            "ran_at": t0,
            "elapsed": 0.0,
            "triggers": [],
            "actions": [],
            "skipped": "throttled",
            "next_eligible_at": _last_tick_at + interval,
        }
    _last_tick_at = t0
    reg = registry or default_registry()
    snapshot: dict[str, Any] = (
        dict(state) if state is not None
        else collect_state(failed_login_tracker=failed_login_tracker)
    )
    # Inject per-rule merged thresholds into the state under the
    # ``_threshold:<id>`` key so each rule reads its operator
    # overrides without the rule needing to know about the registry.
    for rule in reg.list_rules():
        snapshot[f"_threshold:{rule.id}"] = reg.threshold_for(rule.id)
    triggers = reg.evaluate_all(snapshot)
    actions = reg.remediate_all(triggers, snapshot)
    elapsed = round(time.time() - t0, 3)
    if record_history:
        _record(triggers, actions, elapsed=elapsed)
    return {
        "ran_at": t0,
        "elapsed": elapsed,
        "triggers": [t.to_dict() for t in triggers],
        "actions": [a.to_dict() for a in actions],
    }


def consecutive_warning_streaks(
    registry: GuardrailRegistry | None = None,
    *,
    min_streak: int = 2,
) -> list[dict[str, Any]]:
    """Return rule entries whose recent history shows ``severity >=
    warning`` for at least ``min_streak`` consecutive evaluation
    ticks. Used by the health-stories rule to emit one story per
    persistently-firing guardrail without re-running the rules."""
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
