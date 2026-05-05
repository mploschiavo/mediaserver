"""Unit tests for ``DiskGuardrailsService`` (ADR-0008 Phase 2).

Covers the new ``order_strategy`` dispatch table + the
``WatchedLookupAdapter`` fall-back behaviour. The legacy FIFO sort
behaviour stays the default; tests pin the new strategies'
contracts so a refactor of the dispatch table can't silently
regress.
"""

from __future__ import annotations

from typing import Any

import pytest

from media_stack.services.disk_guardrails_service import (
    DiskGuardrailsService,
    WatchedLookupAdapter,
    _ORDER_STRATEGIES,
)


# ---------------------------------------------------------------------------
# Test scaffolding — tiny stubs for the constructor-injected callables.
# ---------------------------------------------------------------------------


def _bool_cfg(d: dict[str, Any], key: str, default: bool) -> bool:
    if key not in d:
        return default
    return bool(d[key])


def _coerce_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _to_int(value: Any, default: Any) -> Any:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_float(value: Any, default: Any) -> Any:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_url(url: str) -> str:
    return str(url).rstrip("/")


def _fmt_bytes(n: int) -> str:
    return f"{int(n)}B"


def _make_service(
    *,
    torrents: list[dict[str, Any]],
    deleted: list[list[str]] | None = None,
    log_lines: list[str] | None = None,
    used_pct: float = 90.0,
    watched_lookup: WatchedLookupAdapter | None = None,
) -> tuple[DiskGuardrailsService, list[list[str]]]:
    """Construct a service wired to in-memory fakes.

    Returns ``(service, deleted)`` where ``deleted`` is the list of
    delete-batches the qbit adapter received during ``enforce()``.
    """
    deleted_batches = deleted if deleted is not None else []
    log_sink = log_lines if log_lines is not None else []

    def _qbit_login(url: str, u: str, p: str) -> Any:  # noqa: ARG001
        return object()

    def _qbit_list_completed(opener: Any, url: str) -> list[dict[str, Any]]:  # noqa: ARG001
        return list(torrents)

    def _qbit_delete(opener: Any, url: str, hashes: list[str], delete_files: bool) -> None:  # noqa: ARG001
        deleted_batches.append(list(hashes))

    def _disk_usage(path: str) -> tuple[float, int, int]:  # noqa: ARG001
        return used_pct, 1_000_000, 100_000

    svc = DiskGuardrailsService(
        log=log_sink.append,
        bool_cfg=_bool_cfg,
        coerce_list=_coerce_list,
        to_int=_to_int,
        to_float=_to_float,
        normalize_url=_normalize_url,
        disk_usage_percent=_disk_usage,
        fmt_bytes=_fmt_bytes,
        qbit_login=_qbit_login,
        qbit_list_completed_torrents=_qbit_list_completed,
        qbit_delete_torrents=_qbit_delete,
        watched_lookup=watched_lookup,
    )
    return svc, deleted_batches


def _torrent(
    *,
    h: str,
    completion_on: int,
    size: int,
    ratio: float = 5.0,
    category: str = "tv-sonarr",
    seeding_time: int = 999_999,
) -> dict[str, Any]:
    return {
        "hash": h,
        "category": category,
        "completion_on": completion_on,
        "size": size,
        "ratio": ratio,
        "seeding_time": seeding_time,
    }


def _cfg(
    *,
    order_strategy: str | None = None,
    enabled: bool = True,
) -> dict[str, Any]:
    qbit_cleanup = {
        "enabled": True,
        "min_completion_age_hours": 0.0,
        "min_ratio": 0.0,
        "min_seeding_time_minutes": 0,
        "max_delete_per_run": 100,
    }
    if order_strategy is not None:
        qbit_cleanup["order_strategy"] = order_strategy
    return {
        "disk_guardrails": {
            "enabled": enabled,
            "monitor_path": "/tmp",
            "max_used_percent": 60.0,
            "target_used_percent": 50.0,
            "qbit_cleanup": qbit_cleanup,
        },
    }


_QBIT_CFG = {"url": "http://qbit.example/"}


# ---------------------------------------------------------------------------
# Strategy table sanity — pin each ordering's primary key so refactoring
# the table can't silently flip the contract.
# ---------------------------------------------------------------------------


class TestOrderStrategyTable:
    def test_table_has_four_strategies(self) -> None:
        assert set(_ORDER_STRATEGIES.keys()) == {
            "oldest_first",
            "largest_first",
            "poor_ratio_first",
            "watched_first",
        }

    def test_oldest_first_sorts_ascending_completion_on(self) -> None:
        candidates = [
            {"completion_on": 200, "size": 10},
            {"completion_on": 100, "size": 5},
            {"completion_on": 300, "size": 20},
        ]
        candidates.sort(key=_ORDER_STRATEGIES["oldest_first"])
        assert [c["completion_on"] for c in candidates] == [100, 200, 300]

    def test_largest_first_sorts_descending_size(self) -> None:
        candidates = [
            {"completion_on": 100, "size": 5},
            {"completion_on": 200, "size": 10},
            {"completion_on": 300, "size": 20},
        ]
        candidates.sort(key=_ORDER_STRATEGIES["largest_first"])
        assert [c["size"] for c in candidates] == [20, 10, 5]

    def test_poor_ratio_first_sorts_ascending_ratio(self) -> None:
        candidates = [
            {"completion_on": 100, "ratio": 5.0},
            {"completion_on": 200, "ratio": 0.5},
            {"completion_on": 300, "ratio": 1.5},
        ]
        candidates.sort(key=_ORDER_STRATEGIES["poor_ratio_first"])
        assert [c["ratio"] for c in candidates] == [0.5, 1.5, 5.0]

    def test_watched_first_puts_watched_before_unwatched(self) -> None:
        candidates = [
            {"completion_on": 100, "size": 10, "_watched": False},
            {"completion_on": 200, "size": 20, "_watched": True},
            {"completion_on": 300, "size": 30, "_watched": True},
        ]
        candidates.sort(key=_ORDER_STRATEGIES["watched_first"])
        assert [c["_watched"] for c in candidates] == [True, True, False]


# ---------------------------------------------------------------------------
# enforce() integration — pin the per-strategy delete-batch ordering.
# ---------------------------------------------------------------------------


class TestEnforceOrderingStrategy:
    def test_oldest_first_matches_legacy_fifo(self) -> None:
        torrents = [
            _torrent(h="h-young", completion_on=300, size=100),
            _torrent(h="h-old", completion_on=100, size=10),
            _torrent(h="h-mid", completion_on=200, size=50),
        ]
        svc, deleted = _make_service(torrents=torrents)
        report = svc.enforce(
            cfg=_cfg(order_strategy="oldest_first"),
            config_root="/tmp",
            qbit_cfg=_QBIT_CFG,
            qb_username="admin",
            qb_password="x",
        )
        # Oldest first: oldest completion timestamp gets deleted first.
        assert deleted[0] == ["h-old", "h-mid", "h-young"]
        assert report["deleted"] == 3
        assert report["strategy"] == "oldest_first"

    def test_largest_first_reverses_by_size(self) -> None:
        torrents = [
            _torrent(h="h-small", completion_on=300, size=10),
            _torrent(h="h-medium", completion_on=200, size=50),
            _torrent(h="h-large", completion_on=100, size=200),
        ]
        svc, deleted = _make_service(torrents=torrents)
        svc.enforce(
            cfg=_cfg(order_strategy="largest_first"),
            config_root="/tmp",
            qbit_cfg=_QBIT_CFG,
            qb_username="admin",
            qb_password="x",
        )
        assert deleted[0] == ["h-large", "h-medium", "h-small"]

    def test_poor_ratio_first_ascending_ratio(self) -> None:
        torrents = [
            _torrent(h="h-good", completion_on=100, size=10, ratio=5.0),
            _torrent(h="h-bad", completion_on=200, size=10, ratio=0.5),
            _torrent(h="h-mid", completion_on=300, size=10, ratio=1.5),
        ]
        svc, deleted = _make_service(torrents=torrents)
        svc.enforce(
            cfg=_cfg(order_strategy="poor_ratio_first"),
            config_root="/tmp",
            qbit_cfg=_QBIT_CFG,
            qb_username="admin",
            qb_password="x",
        )
        assert deleted[0] == ["h-bad", "h-mid", "h-good"]

    def test_unknown_strategy_falls_back_to_oldest(self) -> None:
        torrents = [
            _torrent(h="h-young", completion_on=300, size=100),
            _torrent(h="h-old", completion_on=100, size=10),
        ]
        svc, deleted = _make_service(torrents=torrents)
        report = svc.enforce(
            cfg=_cfg(order_strategy="not-a-real-strategy"),
            config_root="/tmp",
            qbit_cfg=_QBIT_CFG,
            qb_username="admin",
            qb_password="x",
        )
        assert deleted[0] == ["h-old", "h-young"]
        assert report["strategy"] == "oldest_first"


class _StubWatchedLookup(WatchedLookupAdapter):
    """Watched-lookup that returns deterministic decorations and a
    boolean to control success/failure."""

    def __init__(
        self, *, watched_hashes: set[str] | None = None, ok: bool = True,
    ) -> None:
        super().__init__()
        self._watched = watched_hashes or set()
        self._ok = ok

    def decorate(
        self, candidates: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], bool]:
        if not self._ok:
            return candidates, False
        decorated = [
            {**c, "_watched": c.get("hash") in self._watched}
            for c in candidates
        ]
        return decorated, True


class TestWatchedFirst:
    def test_watched_torrents_sorted_first(self) -> None:
        torrents = [
            _torrent(h="h-unwatched-1", completion_on=100, size=10),
            _torrent(h="h-watched-1", completion_on=200, size=10),
            _torrent(h="h-unwatched-2", completion_on=300, size=10),
            _torrent(h="h-watched-2", completion_on=400, size=10),
        ]
        lookup = _StubWatchedLookup(
            watched_hashes={"h-watched-1", "h-watched-2"}, ok=True,
        )
        svc, deleted = _make_service(
            torrents=torrents, watched_lookup=lookup,
        )
        svc.enforce(
            cfg=_cfg(order_strategy="watched_first"),
            config_root="/tmp",
            qbit_cfg=_QBIT_CFG,
            qb_username="admin",
            qb_password="x",
        )
        # First two should be the watched ones (oldest watched first).
        assert deleted[0][:2] == ["h-watched-1", "h-watched-2"]

    def test_lookup_failure_falls_back_to_oldest_first(self) -> None:
        torrents = [
            _torrent(h="h-young", completion_on=300, size=100),
            _torrent(h="h-old", completion_on=100, size=10),
        ]
        lookup = _StubWatchedLookup(ok=False)
        log_lines: list[str] = []
        svc, deleted = _make_service(
            torrents=torrents, watched_lookup=lookup, log_lines=log_lines,
        )
        report = svc.enforce(
            cfg=_cfg(order_strategy="watched_first"),
            config_root="/tmp",
            qbit_cfg=_QBIT_CFG,
            qb_username="admin",
            qb_password="x",
        )
        assert deleted[0] == ["h-old", "h-young"]
        assert report["strategy"] == "oldest_first"


# ---------------------------------------------------------------------------
# Force flag — synchronous cleanup ignores disk-percent threshold.
# ---------------------------------------------------------------------------


class TestForceFlag:
    def test_force_runs_cleanup_below_threshold(self) -> None:
        torrents = [_torrent(h="h-x", completion_on=100, size=10)]
        # Disk usage 30% — well below the 60% configured max.
        svc, deleted = _make_service(torrents=torrents, used_pct=30.0)
        report = svc.enforce(
            cfg=_cfg(),
            config_root="/tmp",
            qbit_cfg=_QBIT_CFG,
            qb_username="admin",
            qb_password="x",
            force=True,
        )
        assert report["deleted"] == 1
        assert deleted[0] == ["h-x"]

    def test_no_force_skips_when_below_threshold(self) -> None:
        torrents = [_torrent(h="h-x", completion_on=100, size=10)]
        svc, deleted = _make_service(torrents=torrents, used_pct=30.0)
        report = svc.enforce(
            cfg=_cfg(),
            config_root="/tmp",
            qbit_cfg=_QBIT_CFG,
            qb_username="admin",
            qb_password="x",
            force=False,
        )
        assert report["deleted"] == 0
        assert deleted == []
