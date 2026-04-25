"""Per-domain rule semantics — one happy path + one fire path each."""

from __future__ import annotations


def _eval(reg, rule_id: str, state: dict) -> str | None:
    state = dict(state)
    state[f"_threshold:{rule_id}"] = reg.threshold_for(rule_id)
    rule = reg.get(rule_id)
    return rule.evaluate(state) if rule else None


def test_inode_floor_fires_at_high_usage(fresh_registry):
    severity = _eval(fresh_registry, "storage:inode_floor", {
        "mount_inodes": {"media": 96.0},
    })
    assert severity == "critical"


def test_inode_floor_silent_when_no_data(fresh_registry):
    assert _eval(fresh_registry, "storage:inode_floor", {}) is None


def test_unpacker_scratch_critical_when_free_below_largest(fresh_registry):
    severity = _eval(fresh_registry, "storage:unpacker_scratch_floor", {
        "unpacker_scratch": {
            "free_bytes": 50_000_000,
            "largest_in_flight_bytes": 200_000_000,
        },
    })
    assert severity == "critical"


def test_trash_retention_warns_for_old_bin_items(fresh_registry):
    severity = _eval(fresh_registry, "storage:trash_retention", {
        "arr_recycle_bins": [{"age_days": 30, "count": 5}],
    })
    assert severity == "warning"


def test_snapshot_retention_warns_above_count(fresh_registry):
    severity = _eval(fresh_registry, "storage:snapshot_retention", {
        "snapshots": {"count": 100, "oldest_age_days": 5},
    })
    assert severity == "warning"


def test_per_content_type_quota_critical_at_120pct(fresh_registry):
    fresh_registry.update_threshold(
        "storage:per_content_type_quota",
        {"ceilings_gb": {"movies": 1}},  # 1 GB ceiling
    )
    severity = _eval(fresh_registry, "storage:per_content_type_quota", {
        "storage_breakdown": {"movies": int(2 * 1024 ** 3)},  # 2 GB used
    })
    assert severity == "critical"


def test_daily_upload_cap_silent_when_disabled(fresh_registry):
    # Default cap is 0 → disabled.
    assert _eval(fresh_registry, "bandwidth:daily_upload_cap", {
        "bandwidth": {"upload_gb_today": 9999},
    }) is None


def test_daily_upload_cap_warns_when_set(fresh_registry):
    fresh_registry.update_threshold(
        "bandwidth:daily_upload_cap", {"max_gb_per_day": 100},
    )
    severity = _eval(fresh_registry, "bandwidth:daily_upload_cap", {
        "bandwidth": {"upload_gb_today": 100},
    })
    assert severity == "warning"


def test_concurrent_downloads_cap_warns(fresh_registry):
    severity = _eval(fresh_registry, "bandwidth:concurrent_downloads_cap", {
        "bandwidth": {"concurrent_downloads": 99},
    })
    assert severity == "warning"


def test_indexer_429_window_warns_on_burst(fresh_registry):
    events = [{"indexer": "iptorrents"}] * 6
    severity = _eval(fresh_registry, "bandwidth:indexer_429_window", {
        "bandwidth": {"indexer_429s": events},
    })
    assert severity == "warning"


def test_opensubtitles_quota_warns_at_80pct(fresh_registry):
    severity = _eval(fresh_registry, "api:opensubtitles_quota", {
        "external_api": {"opensubtitles_used": 180},
    })
    assert severity == "warning"


def test_opensubtitles_quota_critical_at_cap(fresh_registry):
    severity = _eval(fresh_registry, "api:opensubtitles_quota", {
        "external_api": {"opensubtitles_used": 250},
    })
    assert severity == "critical"


def test_tmdb_call_budget_warns_above_85pct(fresh_registry):
    severity = _eval(fresh_registry, "api:tmdb_call_budget", {
        "external_api": {"tmdb_calls_today": 90_000},
    })
    assert severity == "warning"


def test_indexer_ban_risk_critical_on_2x(fresh_registry):
    events = [{"indexer": "x"}] * 60
    severity = _eval(fresh_registry, "api:indexer_ban_risk", {
        "external_api": {"indexer_429s": events},
    })
    assert severity == "critical"


def test_duplicate_count_warns_above_threshold(fresh_registry):
    severity = _eval(fresh_registry, "media:duplicate_count", {
        "media_quality": {"duplicate_count": 30},
    })
    assert severity == "warning"


def test_orphan_files_warns_above_threshold(fresh_registry):
    severity = _eval(fresh_registry, "media:orphan_files", {
        "media_quality": {"orphan_files": 250},
    })
    assert severity == "warning"


def test_stuck_imports_age_warns_at_threshold(fresh_registry):
    severity = _eval(fresh_registry, "media:stuck_imports_age", {
        "media_quality": {"stuck_imports": [{"age_hours": 18}]},
    })
    assert severity == "warning"


def test_runtime_cap_warns_on_long_job(fresh_registry):
    severity = _eval(fresh_registry, "job:runtime_cap", {
        "job_history": [{"jobs": {"slow": {"elapsed": 400, "status": "ok"}}}],
    })
    assert severity == "warning"


def test_consecutive_errors_critical_on_streak(fresh_registry):
    history = [
        {"jobs": {"discover-api-keys": {"status": "error"}}},
        {"jobs": {"discover-api-keys": {"status": "error"}}},
        {"jobs": {"discover-api-keys": {"status": "error"}}},
    ]
    severity = _eval(fresh_registry, "job:consecutive_errors", {
        "job_history": history,
    })
    assert severity == "critical"


def test_consecutive_errors_resets_on_ok_run(fresh_registry):
    history = [
        {"jobs": {"discover-api-keys": {"status": "ok"}}},
        {"jobs": {"discover-api-keys": {"status": "error"}}},
        {"jobs": {"discover-api-keys": {"status": "error"}}},
    ]
    severity = _eval(fresh_registry, "job:consecutive_errors", {
        "job_history": history,
    })
    assert severity is None


def test_auto_heal_cycle_cap_warns(fresh_registry):
    severity = _eval(fresh_registry, "job:auto_heal_cycle_cap", {
        "auto_heal": {"cycles_per_hour": 12},
    })
    assert severity == "warning"


def test_failed_login_spike_warns_via_alerted_flag(fresh_registry):
    severity = _eval(fresh_registry, "auth:failed_login_spike", {
        "auth": {"failed_login_tracker": {
            "bob": {"count": 5, "alerted": True, "first_failure_at": 0},
        }},
    })
    assert severity == "warning"


def test_failed_login_spike_silent_on_low_count(fresh_registry):
    severity = _eval(fresh_registry, "auth:failed_login_spike", {
        "auth": {"failed_login_tracker": {
            "bob": {"count": 1, "alerted": False, "first_failure_at": 0},
        }},
    })
    assert severity is None


def test_concurrent_session_spike_warns(fresh_registry):
    severity = _eval(fresh_registry, "auth:concurrent_session_spike", {
        "auth": {"concurrent_sessions": 999},
    })
    assert severity == "warning"


def test_session_inactivity_warns_on_idle_session(fresh_registry):
    severity = _eval(fresh_registry, "auth:session_inactivity", {
        "auth": {"inactive_sessions": [
            {"session_id": "x", "idle_seconds": 99 * 60},
        ]},
    })
    assert severity == "warning"


def test_provider_unreachable_warns(fresh_registry):
    severity = _eval(fresh_registry, "dep:provider_unreachable", {
        "dependency": {"provider_down_minutes": {"authelia": 8.0}},
    })
    assert severity == "warning"


def test_provider_unreachable_critical_on_3x(fresh_registry):
    severity = _eval(fresh_registry, "dep:provider_unreachable", {
        "dependency": {"provider_down_minutes": {"authelia": 60.0}},
    })
    assert severity == "critical"


def test_egress_cap_silent_when_disabled(fresh_registry):
    # Default cap is 0 → cloud-only placeholder.
    assert _eval(fresh_registry, "cost:egress_gb_month_cap", {
        "cost": {"egress_gb_month": 10_000},
    }) is None
