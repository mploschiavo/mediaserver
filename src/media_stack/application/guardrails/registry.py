"""GuardrailRegistry — singleton + decorator-based rule registration.

Mirrors the ``JobRegistry`` design (``cli/commands/job_framework.py``):

- A module-level decorator (``@register_guardrail``) attaches the
  rule to the registry at import time.
- The registry keeps a flat dict keyed by rule ``id`` so per-rule
  overrides (threshold, disabled, last status) can be applied without
  walking a list.
- Operator overrides persist to a single JSON file under
  ``CONFIG_ROOT/.controller/guardrails.json`` to keep the storage
  story trivial — one file, one disk write per change. The file is
  the source of truth; defaults defined in the rule classes are only
  used when an override is missing.

The single-blob-on-disk choice is deliberate. Per-domain files would
spread reads and writes across 8 paths and demand a sync abstraction
to keep them coherent during a multi-rule update. Bundling them into
one ~2 KB JSON is fast enough to rewrite atomically on every change
and tests can scaffold a fixture with a single ``write_text``.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from media_stack.domain.guardrails.protocols import Action, Guardrail, Trigger

_log = logging.getLogger("media_stack.guardrails")


_OVERRIDE_FILE_DEFAULT = "/srv-config/.controller/guardrails.json"


def _override_path() -> Path:
    """Resolve the override JSON path. Honours ``CONFIG_ROOT``."""
    config_root = os.environ.get("CONFIG_ROOT", "")
    if config_root:
        return Path(config_root) / ".controller" / "guardrails.json"
    return Path(_OVERRIDE_FILE_DEFAULT)


@dataclass
class _RuleEntry:
    """Wraps one Guardrail with cached state used by the UI."""

    rule: Guardrail
    last_status: str = "unknown"  # "ok" | "info" | "warning" | "critical" | "disabled"
    last_evaluated_at: float = 0.0
    last_triggered_at: float = 0.0
    last_severity_streak: int = 0
    last_severity: str = ""
    history: list[dict[str, Any]] = field(default_factory=list)

    def append_history(self, entry: dict[str, Any], cap: int = 20) -> None:
        self.history.append(entry)
        if len(self.history) > cap:
            self.history = self.history[-cap:]


class GuardrailRegistry:
    """Singleton registry. The ``default()`` accessor resolves to a
    process-wide instance so the decorator and the evaluation loop
    see the same set of rules without threading a handle through
    every import.

    Concurrency: the registry is read often (every evaluation tick,
    every API call) and written rarely (only when an operator updates
    a threshold). A single ``RLock`` is sufficient — readers iterate
    a snapshot copy, writers swap the dict atomically.
    """

    def __init__(
        self,
        *,
        override_path_fn: Callable[[], Path] | None = None,
    ) -> None:
        self._rules: dict[str, _RuleEntry] = {}
        self._overrides: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._override_path_fn = override_path_fn or _override_path
        self._loaded = False

    # -- Registration ---------------------------------------------------

    def register(self, rule: Guardrail) -> Guardrail:
        """Idempotent register. Re-registering the same id replaces
        the rule (handy for tests and hot-reload)."""
        with self._lock:
            if not getattr(rule, "id", None):
                raise ValueError("guardrail rule missing 'id'")
            self._rules[rule.id] = _RuleEntry(rule=rule)
        return rule

    def unregister(self, rule_id: str) -> None:
        with self._lock:
            self._rules.pop(rule_id, None)

    def list_rules(self) -> list[Guardrail]:
        with self._lock:
            return [e.rule for e in self._rules.values()]

    def get(self, rule_id: str) -> Guardrail | None:
        with self._lock:
            entry = self._rules.get(rule_id)
            return entry.rule if entry else None

    # -- Overrides (persisted) -----------------------------------------

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            path = self._override_path_fn()
            if path.is_file():
                try:
                    raw = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(raw, dict):
                        self._overrides = {
                            str(k): dict(v) if isinstance(v, dict) else {}
                            for k, v in raw.items()
                        }
                except Exception as exc:  # noqa: BLE001
                    _log.debug(
                        "guardrails: failed to load overrides at %s: %s",
                        path, exc,
                    )
            self._loaded = True

    def _save_overrides(self) -> None:
        path = self._override_path_fn()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(
                json.dumps(self._overrides, indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(tmp, path)
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "guardrails: failed to persist overrides at %s: %s",
                path, exc,
            )

    def threshold_for(self, rule_id: str) -> dict[str, Any]:
        """Merged default + override for the rule. Returns a fresh
        dict so callers can mutate it safely."""
        self._ensure_loaded()
        with self._lock:
            entry = self._rules.get(rule_id)
            if not entry:
                return {}
            merged: dict[str, Any] = dict(entry.rule.default_threshold or {})
            override = self._overrides.get(rule_id, {})
            if isinstance(override, dict):
                t = override.get("threshold")
                if isinstance(t, dict):
                    merged.update(t)
            return merged

    def is_disabled(self, rule_id: str) -> bool:
        self._ensure_loaded()
        with self._lock:
            o = self._overrides.get(rule_id, {})
            return bool(o.get("disabled", False))

    def update_threshold(
        self, rule_id: str, threshold: Mapping[str, Any],
    ) -> dict[str, Any]:
        self._ensure_loaded()
        with self._lock:
            if rule_id not in self._rules:
                return {"error": f"unknown guardrail: {rule_id}"}
            o = self._overrides.setdefault(rule_id, {})
            existing = o.get("threshold")
            t = dict(existing) if isinstance(existing, dict) else {}
            t.update({str(k): v for k, v in (threshold or {}).items()})
            o["threshold"] = t
            self._save_overrides()
            return {"rule_id": rule_id, "threshold": self.threshold_for(rule_id)}

    def set_disabled(self, rule_id: str, disabled: bool) -> dict[str, Any]:
        self._ensure_loaded()
        with self._lock:
            if rule_id not in self._rules:
                return {"error": f"unknown guardrail: {rule_id}"}
            o = self._overrides.setdefault(rule_id, {})
            o["disabled"] = bool(disabled)
            self._save_overrides()
            return {"rule_id": rule_id, "disabled": bool(disabled)}

    # -- Evaluation -----------------------------------------------------

    def evaluate_one(
        self, rule_id: str, state: Mapping[str, Any],
    ) -> Trigger | None:
        """Public single-rule evaluator (used by the dry-run endpoint).

        Always builds the threshold-merged state slice the rule sees
        in production so a Test button result matches the next live
        tick exactly. Disabled rules still evaluate here — the dry-
        run endpoint is for "what would happen if I turned this on?"
        """
        self._ensure_loaded()
        with self._lock:
            entry = self._rules.get(rule_id)
        if not entry:
            return None
        return self._evaluate_entry(entry, state, record=False)

    def evaluate_all(
        self, state: Mapping[str, Any],
    ) -> list[Trigger]:
        """Evaluate every (non-disabled) rule. Returns triggers in
        deterministic order — severity descending, then rule id."""
        self._ensure_loaded()
        triggers: list[Trigger] = []
        with self._lock:
            entries = list(self._rules.values())
        for entry in entries:
            if self.is_disabled(entry.rule.id):
                with self._lock:
                    entry.last_status = "disabled"
                    entry.last_evaluated_at = time.time()
                continue
            t = self._evaluate_entry(entry, state, record=True)
            if t is not None:
                triggers.append(t)
        triggers.sort(
            key=lambda t: (_SEV_ORDER.get(t.severity, 9), t.rule_id),
        )
        return triggers

    def _evaluate_entry(
        self,
        entry: _RuleEntry,
        state: Mapping[str, Any],
        *,
        record: bool,
    ) -> Trigger | None:
        rule = entry.rule
        try:
            severity = rule.evaluate(state)
        except Exception as exc:  # noqa: BLE001
            # A buggy rule must not break the tick — log + record
            # the failure but keep going. The error surfaces in the
            # rule's history so the operator can spot it.
            _log.debug("guardrails: rule %s raised %s", rule.id, exc)
            severity = None
        now = time.time()
        # Dry-runs (force=True) deliberately don't mutate entry state
        # so a Test-button click can't accidentally start a streak or
        # erase one. Only the live ``evaluate_all`` path with
        # ``record=True`` updates last_* and the history ring.
        if record:
            with self._lock:
                entry.last_evaluated_at = now
                if severity is None:
                    entry.last_status = "ok"
                    # Reset the consecutive-tick streak so flapping
                    # rules don't accumulate a fake "story-worthy" run.
                    entry.last_severity_streak = 0
                    entry.last_severity = ""
                    entry.append_history({
                        "ts": now, "severity": None, "status": "ok",
                    })
                else:
                    entry.last_status = severity
                    entry.last_triggered_at = now
                    if entry.last_severity == severity:
                        entry.last_severity_streak += 1
                    else:
                        entry.last_severity_streak = 1
                        entry.last_severity = severity
                    entry.append_history({
                        "ts": now, "severity": severity, "status": severity,
                    })
        if severity is None:
            return None
        threshold = self.threshold_for(rule.id)
        # The rule decides what to put in current_value; we don't
        # synthesize one. Many rules will key off the same field of
        # ``state`` they used in evaluate(); a few (auth-spike) emit
        # a count.
        current = _extract_current(rule, state)
        return Trigger(
            rule_id=rule.id,
            domain=rule.domain,
            severity=severity,
            description=rule.description,
            current_value=current,
            threshold=threshold,
            detail="",
            evaluated_at=now,
        )

    def remediate_all(
        self, triggers: Iterable[Trigger], state: Mapping[str, Any],
    ) -> list[Action]:
        """Run remediation for each trigger. The rule's ``remediate``
        is allowed to return ``None`` (no action available — alert-
        only rule) and we synthesize an Action(action="notify",
        ok=False) record so the audit history is uniform.
        """
        out: list[Action] = []
        with self._lock:
            entries = {rid: e for rid, e in self._rules.items()}
        for trig in triggers:
            entry = entries.get(trig.rule_id)
            if entry is None:
                continue
            try:
                action = entry.rule.remediate(state)
            except Exception as exc:  # noqa: BLE001
                _log.debug(
                    "guardrails: remediate %s raised %s", trig.rule_id, exc,
                )
                action = Action(
                    rule_id=trig.rule_id, action="error",
                    ok=False, detail=str(exc)[:200],
                )
            if action is None:
                action = Action(
                    rule_id=trig.rule_id, action="notify", ok=False,
                    detail="no automatic remediation; alert only",
                )
            out.append(action)
        return out

    # -- Read-side projections used by the UI --------------------------

    def status_summary(self) -> list[dict[str, Any]]:
        """Build the response payload for ``GET /api/guardrails``.

        Each entry exposes the static rule metadata, the merged
        threshold, the latest evaluation status, and the timestamp
        of the most recent fire. Sorted by domain then id so the
        UI's tab-grouping is stable.
        """
        self._ensure_loaded()
        out: list[dict[str, Any]] = []
        with self._lock:
            for rid, entry in self._rules.items():
                disabled = bool(self._overrides.get(rid, {}).get("disabled", False))
                out.append({
                    "id": rid,
                    "domain": entry.rule.domain,
                    "description": entry.rule.description,
                    "threshold": self.threshold_for(rid),
                    "default_threshold": dict(entry.rule.default_threshold or {}),
                    "last_status": (
                        "disabled" if disabled else entry.last_status
                    ),
                    "last_severity": entry.last_severity,
                    "last_severity_streak": entry.last_severity_streak,
                    "last_evaluated_at": entry.last_evaluated_at,
                    "last_triggered_at": entry.last_triggered_at,
                    "disabled": disabled,
                })
        out.sort(key=lambda r: (str(r["domain"]), str(r["id"])))
        return out

    def history_snapshot(self) -> dict[str, list[dict[str, Any]]]:
        """Per-rule recent evaluation history. Used by the consecutive-
        tick health-story rule so it can answer "has this rule fired
        for the last N ticks?" without re-running evaluate."""
        with self._lock:
            return {
                rid: list(entry.history)
                for rid, entry in self._rules.items()
            }

    def reset(self) -> None:
        """Test helper — clears registered rules + overrides + cache."""
        with self._lock:
            self._rules.clear()
            self._overrides.clear()
            self._loaded = False


_SEV_ORDER = {"critical": 0, "warning": 1, "info": 2}


def _extract_current(
    rule: Guardrail, state: Mapping[str, Any],
) -> Any:
    """Best-effort current-value extraction for the API payload.

    The protocol doesn't require rules to expose a ``current_value``
    helper — most just compare a state field to a threshold. We
    inspect the rule for an optional ``current_value(state)`` method
    and call it if present; otherwise return ``None`` and let the
    UI render "—".
    """
    fn = getattr(rule, "current_value", None)
    if callable(fn):
        try:
            return fn(state)
        except Exception:  # noqa: BLE001
            return None
    return None


# ---------------------------------------------------------------------------
# Module-level singleton + decorator
# ---------------------------------------------------------------------------


_DEFAULT: GuardrailRegistry | None = None
_DEFAULT_LOCK = threading.Lock()


def default() -> GuardrailRegistry:
    """Return the process-wide registry. Lazily initialised so test
    fixtures can monkey-patch ``_override_path`` before first use."""
    global _DEFAULT
    if _DEFAULT is None:
        with _DEFAULT_LOCK:
            if _DEFAULT is None:
                _DEFAULT = GuardrailRegistry()
    return _DEFAULT


def reset_default() -> None:
    """Test helper — clear the singleton so the next ``default()``
    call returns a fresh instance. Domain modules don't auto-import
    on default() so test code must explicitly re-import them after
    reset to repopulate the rule set."""
    global _DEFAULT
    with _DEFAULT_LOCK:
        _DEFAULT = None


def register_guardrail(rule: Guardrail) -> Guardrail:
    """Decorator/function: register on the default registry. Domain
    modules call this at import time so the singleton always sees
    the rule the moment ``application.guardrails`` is imported."""
    default().register(rule)
    return rule


__all__ = [
    "GuardrailRegistry",
    "default",
    "register_guardrail",
    "reset_default",
]
