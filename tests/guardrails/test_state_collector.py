"""State collector smoke tests — runs without a live disk service."""

from __future__ import annotations

import time

from media_stack.services.guardrails.state_collector import (
    collect_state,
    record_indexer_429,
)


def test_collect_state_returns_required_sections(fresh_registry):
    state = collect_state()
    for key in (
        "bandwidth", "external_api", "media_quality", "auth", "dependency",
        "cost", "auto_heal", "snapshots",
    ):
        assert key in state, f"missing section: {key}"


def test_record_indexer_429_appears_in_snapshot(fresh_registry):
    record_indexer_429("iptorrents")
    state = collect_state()
    indexers = [
        e["indexer"] for e in state["bandwidth"]["indexer_429s"]
        if isinstance(e, dict)
    ]
    assert "iptorrents" in indexers


def test_old_429_events_dropped_from_window(fresh_registry):
    # Force-record a 2-hour-old event; the 5-min window snapshot
    # must not surface it.
    long_ago = time.time() - 7200
    record_indexer_429("ancient", now=long_ago)
    state = collect_state()
    indexers = [
        e["indexer"] for e in state["bandwidth"]["indexer_429s"]
        if isinstance(e, dict)
    ]
    assert "ancient" not in indexers
