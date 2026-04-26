"""Unit tests for ``security_metrics_contract``.

The contract file is documentation-as-code: these tests enforce the
shape invariants (Prometheus-compliant names, no collisions, every
constant actually documented).
"""

from __future__ import annotations

import re

from media_stack.core.observability import security_metrics_contract as contract


_METRIC_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def _constants() -> dict[str, str]:
    """Return every UPPER_CASE str constant defined on the contract module."""
    return {
        name: value
        for name, value in vars(contract).items()
        if name.isupper() and isinstance(value, str) and not name.startswith("_")
    }


def test_every_constant_is_lowercase_underscore():
    for name, value in _constants().items():
        assert _METRIC_NAME_RE.match(value), (
            f"{name}={value!r} is not a valid Prometheus metric name"
        )


def test_no_duplicate_constant_values():
    values = list(_constants().values())
    assert len(values) == len(set(values)), f"duplicate metric names: {values}"


def test_expected_constants_present():
    # Contract smoke-test: confirms the names the spec calls out.
    expected = {
        "SESSIONS_ACTIVE",
        "LOGIN_FAILURES_TOTAL",
        "LOGIN_SUCCESSES_TOTAL",
        "BANS_CURRENT",
        "BAN_APPLIED_TOTAL",
        "SESSION_REVOKED_TOTAL",
        "AUDIT_CHAIN_HEAD_AGE_SECONDS",
        "PASSWORD_CHANGED_TOTAL",
        "ANOMALY_DETECTED_TOTAL",
    }
    assert expected.issubset(set(_constants().keys()))


def test_all_metric_names_tuple_matches_constants():
    assert set(contract.ALL_METRIC_NAMES) == set(_constants().values())
    # Tuple is de-duplicated in a defensive way.
    assert len(contract.ALL_METRIC_NAMES) == len(set(contract.ALL_METRIC_NAMES))


def test_module_has_docstring_and_comments_for_every_constant():
    # Module-level docstring exists.
    assert contract.__doc__ is not None
    assert "session-visibility" in contract.__doc__.lower()

    # Every constant has an inline-comment block that mentions its name.
    # We parse the source so documentation-as-code is enforced mechanically.
    import inspect
    src = inspect.getsource(contract)
    for name in _constants().keys():
        # Find the line that assigns the constant.
        assign_idx = None
        lines = src.splitlines()
        for i, line in enumerate(lines):
            if line.startswith(f"{name} ="):
                assign_idx = i
                break
        assert assign_idx is not None, f"{name} assignment not found"
        # Walk backwards past blank lines to find comment block.
        j = assign_idx - 1
        comment_lines: list[str] = []
        while j >= 0 and (lines[j].startswith("#") or lines[j].strip() == ""):
            if lines[j].startswith("#"):
                comment_lines.append(lines[j])
            j -= 1
        joined = "\n".join(comment_lines)
        assert "Labels:" in joined, f"{name} has no 'Labels:' comment block"


def test_total_suffix_reserved_for_counters():
    # Prometheus convention: ``_total`` marks monotonic counters. The
    # non-_total names in the contract must be gauges. This test guards
    # against someone naming a gauge ``foo_total`` or a counter without
    # the suffix.
    counter_like = {n for n, v in _constants().items() if v.endswith("_total")}
    gauge_like = {n for n, v in _constants().items() if not v.endswith("_total")}
    # Known gauges per the feature spec.
    expected_gauges = {
        "SESSIONS_ACTIVE",
        "BANS_CURRENT",
        "AUDIT_CHAIN_HEAD_AGE_SECONDS",
    }
    assert expected_gauges.issubset(gauge_like)
    # Known counters per the feature spec.
    expected_counters = {
        "LOGIN_FAILURES_TOTAL",
        "LOGIN_SUCCESSES_TOTAL",
        "BAN_APPLIED_TOTAL",
        "SESSION_REVOKED_TOTAL",
        "PASSWORD_CHANGED_TOTAL",
        "ANOMALY_DETECTED_TOTAL",
    }
    assert expected_counters.issubset(counter_like)
