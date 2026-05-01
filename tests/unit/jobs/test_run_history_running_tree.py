"""Verify ``get_running_tree`` correctly assembles a parent→children
tree of in-flight roots, with their full descendant subtree.

The tree powers ``GET /api/jobs/running`` ``tree`` field, which the
onboarding banner uses to drive the "N done of M steps" counter and
the Jobs page's ``CurrentlyRunningCard`` renders with per-step
elapsed glyphs. Behaviours we need to be confident about:

  * Top-level roots are gated to status=running (the card empties
    when the parent finishes).
  * Settled descendants of a running root REMAIN in the tree with
    their terminal status — without that, the onboarding counter
    sticks at "0 done of N" forever because completed sub-jobs
    vanish from the tree the instant they finish.
  * Children whose parent has settled surface as top-level orphans
    so the operator still sees the work in flight.
  * Each node carries the fields the UI renders directly — no
    additional shape coercion required client-side.
  * Top-level nodes appear in ``started_at`` order.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from media_stack.application.jobs import run_history
from media_stack.core.events import reset_default_bus
from media_stack.domain.jobs.run_record import RunStatus


@pytest.fixture(autouse=True)
def _isolate_history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CONFIG_ROOT", str(tmp_path))
    reset_default_bus()
    yield
    reset_default_bus()


class TestGetRunningTree:
    def test_empty_when_no_runs_in_flight(self) -> None:
        assert run_history.get_running_tree() == []

    def test_settled_top_level_runs_excluded(self) -> None:
        # When the only candidate root has settled, the tree is
        # empty — the card auto-empties at the parent layer.
        rec = run_history.record_run_start("scan", triggered_by="cron")
        run_history.record_run_complete(rec.run_id, status=RunStatus.OK)
        assert run_history.get_running_tree() == []

    def test_single_running_record_surfaces_as_top_level(self) -> None:
        rec = run_history.record_run_start("scan", triggered_by="cron")
        tree = run_history.get_running_tree()
        assert len(tree) == 1
        node = tree[0]
        assert node["run_id"] == rec.run_id
        assert node["job_name"] == "scan"
        assert node["status"] == RunStatus.RUNNING
        assert node["children"] == []
        # Each node reports a positive elapsed and the trigger / actor
        # the UI needs without re-querying.
        assert node["elapsed_seconds"] >= 0
        assert node["triggered_by"] == "cron"

    def test_running_child_nests_under_running_parent(self) -> None:
        parent = run_history.record_run_start(
            "bootstrap", triggered_by="manual",
        )
        child = run_history.record_run_start(
            "discover-api-keys",
            parent_run_id=parent.run_id,
            triggered_by="parent",
        )
        tree = run_history.get_running_tree()
        assert len(tree) == 1, "child should nest, not surface as a top-level"
        top = tree[0]
        assert top["run_id"] == parent.run_id
        assert len(top["children"]) == 1
        assert top["children"][0]["run_id"] == child.run_id

    def test_orphan_running_child_surfaces_as_top_level(self) -> None:
        # Parent settled; child still running. The operator should
        # still see the child in flight rather than have it disappear
        # from the card just because the parent finished.
        parent = run_history.record_run_start(
            "bootstrap", triggered_by="manual",
        )
        child = run_history.record_run_start(
            "long-running-task",
            parent_run_id=parent.run_id,
            triggered_by="parent",
        )
        run_history.record_run_complete(parent.run_id, status=RunStatus.OK)
        tree = run_history.get_running_tree()
        assert len(tree) == 1
        assert tree[0]["run_id"] == child.run_id
        assert tree[0]["children"] == []

    def test_top_level_nodes_sorted_by_started_at_ascending(self) -> None:
        first = run_history.record_run_start("a", triggered_by="cron")
        second = run_history.record_run_start("b", triggered_by="cron")
        tree = run_history.get_running_tree()
        ordering = [n["run_id"] for n in tree]
        assert ordering == [first.run_id, second.run_id]

    def test_settled_child_kept_under_running_parent(self) -> None:
        # Onboarding banner contract: the counter renders "N done of
        # M steps" by flattening the tree and counting status. If we
        # prune settled children, the counter is forever stuck at
        # "0 done of N running" because every child that finishes
        # disappears instead of contributing to "done". The settled
        # child MUST stay in the tree under the still-running parent
        # with its terminal status intact.
        parent = run_history.record_run_start(
            "bootstrap", triggered_by="manual",
        )
        done_child = run_history.record_run_start(
            "discover-api-keys",
            parent_run_id=parent.run_id,
            triggered_by="parent",
        )
        live_child = run_history.record_run_start(
            "mass-search-throttled",
            parent_run_id=parent.run_id,
            triggered_by="parent",
        )
        run_history.record_run_complete(
            done_child.run_id, status=RunStatus.OK,
        )
        tree = run_history.get_running_tree()
        assert len(tree) == 1
        kids = tree[0]["children"]
        assert len(kids) == 2, (
            "settled siblings must remain under a running parent so "
            "the banner's done-counter is honest"
        )
        by_id = {k["run_id"]: k for k in kids}
        assert by_id[done_child.run_id]["status"] == RunStatus.OK
        assert by_id[live_child.run_id]["status"] == RunStatus.RUNNING

    def test_settled_child_elapsed_freezes_at_completion(self) -> None:
        # A settled child's elapsed must come from the stored value,
        # not "now - started_at" — otherwise the UI shows it ticking
        # forward forever even though the job finished.
        parent = run_history.record_run_start(
            "bootstrap", triggered_by="manual",
        )
        child = run_history.record_run_start(
            "discover-api-keys",
            parent_run_id=parent.run_id,
            triggered_by="parent",
        )
        rec = run_history.record_run_complete(
            child.run_id, status=RunStatus.OK,
        )
        assert rec is not None and rec.elapsed is not None
        frozen = rec.elapsed
        tree = run_history.get_running_tree()
        kids = tree[0]["children"]
        assert kids[0]["elapsed_seconds"] == pytest.approx(
            round(frozen, 2), abs=0.01,
        )
