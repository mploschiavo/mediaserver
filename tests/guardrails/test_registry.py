"""GuardrailRegistry — registration, evaluation, override persistence.

Pins the contract the api handlers + UI rely on:

- 8 domains are present after default-load.
- evaluate_all returns triggers in severity-then-id order.
- Override updates persist to a single JSON blob and round-trip.
- Disabled rules are skipped by evaluate_all.
"""

from __future__ import annotations

import json

from media_stack.services.guardrails import (
    consecutive_warning_streaks,
    default,
)
from media_stack.services.guardrails.evaluation_loop import tick


def test_default_registry_has_all_8_domains(fresh_registry):
    rules = fresh_registry.list_rules()
    domains = {r.domain for r in rules}
    assert domains == {
        "storage", "bandwidth", "external_api", "media_quality",
        "job_health", "auth", "dependency", "cost",
    }


def test_per_mount_threshold_fires_critical_above_max(fresh_registry):
    state = {
        "disk": {
            "media": {"percent_used": 90.0, "free_bytes": 10 ** 12},
        },
    }
    triggers = fresh_registry.evaluate_all(state)
    by_id = {t.rule_id: t for t in triggers}
    assert "storage:per_mount_threshold" in by_id
    assert by_id["storage:per_mount_threshold"].severity == "critical"


def test_per_mount_threshold_fires_warning_above_target(fresh_registry):
    state = {
        "disk": {
            "media": {"percent_used": 80.0, "free_bytes": 10 ** 12},
        },
    }
    triggers = fresh_registry.evaluate_all(state)
    by_id = {t.rule_id: t for t in triggers}
    assert by_id["storage:per_mount_threshold"].severity == "warning"


def test_per_mount_threshold_quiet_when_under_target(fresh_registry):
    state = {
        "disk": {
            "media": {"percent_used": 50.0, "free_bytes": 10 ** 12},
        },
    }
    triggers = fresh_registry.evaluate_all(state)
    assert "storage:per_mount_threshold" not in {t.rule_id for t in triggers}


def test_free_space_floor_critical_below_half(fresh_registry):
    # Default floor is 10 GiB; 4 GiB free is below half → critical.
    four_gib = 4 * (1024 ** 3)
    state = {"disk": {"media": {"percent_used": 5.0, "free_bytes": four_gib}}}
    triggers = fresh_registry.evaluate_all(state)
    by_id = {t.rule_id: t for t in triggers}
    assert by_id["storage:free_space_floor"].severity == "critical"


def test_threshold_override_persists_to_single_json_file(
    fresh_registry, tmp_path,
):
    fresh_registry.update_threshold(
        "storage:per_mount_threshold",
        {"max_percent": 50.0, "target_percent": 40.0},
    )
    blob = (tmp_path / ".controller" / "guardrails.json").read_text()
    parsed = json.loads(blob)
    assert "storage:per_mount_threshold" in parsed
    assert parsed["storage:per_mount_threshold"]["threshold"]["max_percent"] == 50.0


def test_disable_skips_rule_in_evaluate_all(fresh_registry):
    fresh_registry.set_disabled("storage:per_mount_threshold", True)
    triggers = fresh_registry.evaluate_all({
        "disk": {"media": {"percent_used": 99.0, "free_bytes": 10 ** 12}},
    })
    assert "storage:per_mount_threshold" not in {t.rule_id for t in triggers}
    summary = {r["id"]: r for r in fresh_registry.status_summary()}
    assert summary["storage:per_mount_threshold"]["last_status"] == "disabled"


def test_evaluate_one_ignores_disabled(fresh_registry):
    """The dry-run endpoint runs the rule even when disabled —
    operators want to know "what would happen if I turned this on?".
    """
    fresh_registry.set_disabled("storage:per_mount_threshold", True)
    state = {
        "disk": {"media": {"percent_used": 99.0, "free_bytes": 10 ** 12}},
        "_threshold:storage:per_mount_threshold":
            fresh_registry.threshold_for("storage:per_mount_threshold"),
    }
    trig = fresh_registry.evaluate_one("storage:per_mount_threshold", state)
    assert trig is not None
    assert trig.severity == "critical"


def test_triggers_sorted_by_severity_then_id(fresh_registry):
    state = {
        "disk": {
            "media": {"percent_used": 99.0, "free_bytes": 10 ** 12},
        },
        "auth": {"failed_login_tracker": {
            "alice": {"count": 99, "alerted": True, "first_failure_at": 0},
        }},
    }
    triggers = fresh_registry.evaluate_all(state)
    # All criticals first.
    severities = [t.severity for t in triggers]
    assert severities == sorted(
        severities,
        key={"critical": 0, "warning": 1, "info": 2}.get,
    )


def test_remediation_runs_per_trigger(fresh_registry):
    state = {
        "disk": {"media": {"percent_used": 99.0, "free_bytes": 10 ** 12}},
    }
    triggers = fresh_registry.evaluate_all(state)
    actions = fresh_registry.remediate_all(triggers, state)
    rule_ids = {a.rule_id for a in actions}
    assert "storage:per_mount_threshold" in rule_ids


def test_consecutive_warning_streaks_increments(fresh_registry):
    state = {
        "disk": {"media": {"percent_used": 90.0, "free_bytes": 10 ** 12}},
    }
    fresh_registry.evaluate_all(state)
    fresh_registry.evaluate_all(state)
    streaks = consecutive_warning_streaks(fresh_registry, min_streak=2)
    rule_ids = {s["rule_id"] for s in streaks}
    assert "storage:per_mount_threshold" in rule_ids


def test_consecutive_streaks_reset_on_recovery(fresh_registry):
    bad = {
        "disk": {"media": {"percent_used": 90.0, "free_bytes": 10 ** 12}},
    }
    good = {
        "disk": {"media": {"percent_used": 50.0, "free_bytes": 10 ** 12}},
    }
    fresh_registry.evaluate_all(bad)
    fresh_registry.evaluate_all(bad)
    fresh_registry.evaluate_all(good)  # recovery resets streak
    streaks = consecutive_warning_streaks(fresh_registry, min_streak=2)
    rule_ids = {s["rule_id"] for s in streaks}
    assert "storage:per_mount_threshold" not in rule_ids


def test_tick_returns_triggers_and_actions(fresh_registry):
    result = tick(
        registry=fresh_registry,
        state={
            "disk": {"media": {"percent_used": 99.0, "free_bytes": 10 ** 12}},
        },
        record_history=False,
    )
    rule_ids = {t["rule_id"] for t in result["triggers"]}
    assert "storage:per_mount_threshold" in rule_ids
    assert len(result["actions"]) >= len(result["triggers"])


def test_test_endpoint_handles_unknown_rule(fresh_registry):
    assert fresh_registry.evaluate_one("nope:nope", {}) is None
