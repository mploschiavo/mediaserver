"""Unit tests for ``api.services.job_queue``.

Covers enqueue / remove / reorder (up / down / position / clamp /
no-op) and the persistence guarantees the UI relies on (writes
visible to a fresh load, queue order preserved across calls).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from media_stack.api.services import job_queue


@pytest.fixture(autouse=True)
def _isolate_queue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CONFIG_ROOT", str(tmp_path))
    yield


def _ids(queue: list[dict]) -> list[int]:
    return [e["id"] for e in queue]


def _read_disk(tmp_path: Path) -> list[dict]:
    f = tmp_path / ".controller" / "queue.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else []


class TestGetQueue:
    def test_empty_when_no_entries(self) -> None:
        out = job_queue.get_queue()
        assert out == {"queue": [], "count": 0}


class TestEnqueue:
    def test_appends_to_tail(self) -> None:
        a = job_queue.enqueue("scan")
        b = job_queue.enqueue("probe")
        out = job_queue.get_queue()
        assert _ids(out["queue"]) == [a["entry"]["id"], b["entry"]["id"]]
        assert out["count"] == 2

    def test_rejects_blank_job_name(self) -> None:
        out = job_queue.enqueue("")
        assert "error" in out

    def test_rejects_whitespace_only_job_name(self) -> None:
        out = job_queue.enqueue("   ")
        assert "error" in out

    def test_default_source_is_manual(self) -> None:
        out = job_queue.enqueue("scan")
        assert out["entry"]["source"] == "manual"

    def test_label_falls_back_to_job_name(self) -> None:
        out = job_queue.enqueue("scan")
        assert out["entry"]["label"] == "scan"

    def test_label_override_used_when_provided(self) -> None:
        out = job_queue.enqueue("scan", label="hourly scan")
        assert out["entry"]["label"] == "hourly scan"

    def test_persists_to_disk(self, tmp_path: Path) -> None:
        job_queue.enqueue("scan", source="config-save")
        on_disk = _read_disk(tmp_path)
        assert len(on_disk) == 1
        assert on_disk[0]["job_name"] == "scan"
        assert on_disk[0]["source"] == "config-save"


class TestRemove:
    def test_drops_known_entry(self) -> None:
        a = job_queue.enqueue("scan")
        b = job_queue.enqueue("probe")
        out = job_queue.remove_entry(a["entry"]["id"])
        assert out["status"] == "removed"
        assert _ids(job_queue.get_queue()["queue"]) == [b["entry"]["id"]]

    def test_returns_error_on_unknown_id(self) -> None:
        out = job_queue.remove_entry(99999)
        assert "error" in out


class TestReorder:
    def test_up_moves_entry_one_slot_higher(self) -> None:
        a = job_queue.enqueue("a")
        b = job_queue.enqueue("b")
        c = job_queue.enqueue("c")
        out = job_queue.reorder_entry(c["entry"]["id"], direction="up")
        assert out["status"] == "reordered"
        assert _ids(job_queue.get_queue()["queue"]) == [
            a["entry"]["id"], c["entry"]["id"], b["entry"]["id"],
        ]

    def test_down_moves_entry_one_slot_lower(self) -> None:
        a = job_queue.enqueue("a")
        b = job_queue.enqueue("b")
        c = job_queue.enqueue("c")
        out = job_queue.reorder_entry(a["entry"]["id"], direction="down")
        assert out["status"] == "reordered"
        assert _ids(job_queue.get_queue()["queue"]) == [
            b["entry"]["id"], a["entry"]["id"], c["entry"]["id"],
        ]

    def test_up_at_head_is_a_noop_not_an_error(self) -> None:
        a = job_queue.enqueue("a")
        job_queue.enqueue("b")
        out = job_queue.reorder_entry(a["entry"]["id"], direction="up")
        assert out["status"] == "noop"

    def test_down_at_tail_is_a_noop_not_an_error(self) -> None:
        job_queue.enqueue("a")
        b = job_queue.enqueue("b")
        out = job_queue.reorder_entry(b["entry"]["id"], direction="down")
        assert out["status"] == "noop"

    def test_position_jumps_to_absolute_index(self) -> None:
        a = job_queue.enqueue("a")
        b = job_queue.enqueue("b")
        c = job_queue.enqueue("c")
        out = job_queue.reorder_entry(c["entry"]["id"], position=0)
        assert out["status"] == "reordered"
        assert _ids(job_queue.get_queue()["queue"]) == [
            c["entry"]["id"], a["entry"]["id"], b["entry"]["id"],
        ]

    def test_position_clamps_above_tail(self) -> None:
        a = job_queue.enqueue("a")
        b = job_queue.enqueue("b")
        # Position 99 → clamps to last index (1).
        out = job_queue.reorder_entry(a["entry"]["id"], position=99)
        assert out["status"] == "reordered"
        assert _ids(job_queue.get_queue()["queue"]) == [
            b["entry"]["id"], a["entry"]["id"],
        ]

    def test_returns_error_on_unknown_id(self) -> None:
        out = job_queue.reorder_entry(99999, direction="up")
        assert "error" in out

    def test_rejects_invalid_direction(self) -> None:
        a = job_queue.enqueue("a")
        out = job_queue.reorder_entry(
            a["entry"]["id"], direction="sideways",
        )
        assert "error" in out

    def test_rejects_call_with_neither_direction_nor_position(self) -> None:
        a = job_queue.enqueue("a")
        out = job_queue.reorder_entry(a["entry"]["id"])
        assert "error" in out


class TestClear:
    def test_drops_all_entries(self) -> None:
        job_queue.enqueue("a")
        job_queue.enqueue("b")
        out = job_queue.clear_queue()
        assert out == {"status": "cleared", "count": 2}
        assert job_queue.get_queue()["count"] == 0
