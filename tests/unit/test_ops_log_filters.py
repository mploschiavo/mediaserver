"""Unit tests for the Logs Phase 1+3 backend helpers.

Covers ``_parse_since_seconds`` (relative + ISO datetime parsing),
``_apply_log_filters`` (action / level / regex search), and
``_read_archive_log_lines`` (gzipped archive replay).
"""

from __future__ import annotations

import gzip
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from media_stack.api.services.ops import (
    _apply_log_filters,
    _parse_since_seconds,
    _read_archive_log_lines,
)


class TestParseSinceSeconds:
    """Operators pass ``5m`` / ``1h`` / ISO from the URL — both
    forms must round-trip to ``int`` seconds."""

    @pytest.mark.parametrize(
        "value,expected",
        [
            ("5m", 5 * 60),
            ("30m", 30 * 60),
            ("2h", 7200),
            ("24h", 86400),
            ("3d", 3 * 86400),
            ("60s", 60),
        ],
    )
    def test_relative_shorthand(self, value: str, expected: int) -> None:
        assert _parse_since_seconds(value) == expected

    def test_iso_datetime_returns_seconds_since(self) -> None:
        ten_minutes_ago = datetime.now(tz=timezone.utc) - timedelta(minutes=10)
        seconds = _parse_since_seconds(ten_minutes_ago.isoformat())
        assert seconds is not None
        # Allow some test-runtime jitter — must be ≥595 (just under
        # 10min) and ≤620 (a 10-second slack window).
        assert 595 <= seconds <= 620

    def test_iso_with_z_suffix(self) -> None:
        ten_minutes_ago = datetime.now(tz=timezone.utc) - timedelta(minutes=10)
        # Authelia / browsers commonly emit Z-suffixed UTC.
        iso = ten_minutes_ago.isoformat().replace("+00:00", "Z")
        seconds = _parse_since_seconds(iso)
        assert seconds is not None
        assert 595 <= seconds <= 620

    def test_invalid_returns_none(self) -> None:
        assert _parse_since_seconds("garbage") is None
        assert _parse_since_seconds("") is None
        assert _parse_since_seconds("17q") is None


class TestApplyLogFilters:
    SAMPLE = [
        "[2026-04-27T03:00:00+0000] [INFO] media_stack: [JOB] envoy-config: starting",
        "[2026-04-27T03:00:01+0000] [INFO] media_stack: Generating Envoy config",
        "[2026-04-27T03:00:02+0000] [ERR] envoy-config: Envoy template not found",
        "[2026-04-27T03:00:03+0000] [WARN] media_stack: ARR preflight skipped",
        "[2026-04-27T03:00:04+0000] [DEBUG] media_stack: cache miss for x",
    ]

    def test_no_filters_returns_input(self) -> None:
        out = _apply_log_filters(list(self.SAMPLE), None, None, None)
        assert out == self.SAMPLE

    def test_action_filter_matches_job_prefix(self) -> None:
        out = _apply_log_filters(
            list(self.SAMPLE), action="envoy-config", level=None, q=None,
        )
        # Three lines mention envoy-config: the [JOB] start, the [ERR],
        # and the bare-substring "Envoy" line is excluded (different
        # casing) — keep precise expectations:
        assert any("[JOB] envoy-config" in ln for ln in out)
        assert any("envoy-config" in ln for ln in out)
        # Must drop lines that don't mention envoy-config at all.
        assert not any("ARR preflight" in ln for ln in out)

    def test_level_filter_error(self) -> None:
        out = _apply_log_filters(
            list(self.SAMPLE), action=None, level="error", q=None,
        )
        assert all("[ERR]" in ln or "ERROR" in ln for ln in out)
        assert len(out) == 1

    def test_level_filter_warning(self) -> None:
        out = _apply_log_filters(
            list(self.SAMPLE), action=None, level="warning", q=None,
        )
        assert len(out) == 1
        assert "[WARN]" in out[0]

    def test_text_search_substring(self) -> None:
        out = _apply_log_filters(
            list(self.SAMPLE), action=None, level=None, q="cache",
        )
        assert len(out) == 1
        assert "cache miss" in out[0]

    def test_regex_search_case_insensitive(self) -> None:
        out = _apply_log_filters(
            list(self.SAMPLE), action=None, level=None, q="/envoy/i",
        )
        # Three lines have envoy in some form (job + generating + err).
        assert len(out) == 3

    def test_combined_filters_chain(self) -> None:
        out = _apply_log_filters(
            list(self.SAMPLE),
            action="envoy-config",
            level="error",
            q=None,
        )
        assert len(out) == 1
        assert "[ERR]" in out[0]
        assert "envoy-config" in out[0]


class TestReadArchiveLogLines:
    def test_no_env_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MEDIA_STACK_LOG_ARCHIVE_DIR", raising=False)
        assert _read_archive_log_lines("controller", None) == []

    def test_reads_gzipped_archive(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        archive_dir = tmp_path / "log-archive"
        archive_dir.mkdir()
        log_path = archive_dir / "controller.log.gz"
        with gzip.open(log_path, "wt", encoding="utf-8") as f:
            f.write("line one\nline two\nline three\n")
        monkeypatch.setenv("MEDIA_STACK_LOG_ARCHIVE_DIR", str(archive_dir))
        out = _read_archive_log_lines("controller", None)
        assert len(out) == 3
        # Each line must be marked with the archive prefix so the UI
        # can dim it.
        assert all(ln.startswith("[archive:controller.log.gz]") for ln in out)
        assert any("line one" in ln for ln in out)

    def test_reads_plain_log(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        archive_dir = tmp_path / "log-archive"
        archive_dir.mkdir()
        (archive_dir / "controller.log").write_text(
            "alpha\nbeta\n", encoding="utf-8",
        )
        monkeypatch.setenv("MEDIA_STACK_LOG_ARCHIVE_DIR", str(archive_dir))
        out = _read_archive_log_lines("controller", None)
        assert len(out) == 2
        assert all(ln.startswith("[archive:controller.log]") for ln in out)

    def test_unknown_service_returns_empty(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        archive_dir = tmp_path / "log-archive"
        archive_dir.mkdir()
        (archive_dir / "controller.log").write_text("hi\n", encoding="utf-8")
        monkeypatch.setenv("MEDIA_STACK_LOG_ARCHIVE_DIR", str(archive_dir))
        out = _read_archive_log_lines("nonexistent-service", None)
        assert out == []

    def test_missing_archive_dir_quiet(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(
            "MEDIA_STACK_LOG_ARCHIVE_DIR",
            str(tmp_path / "does-not-exist"),
        )
        # Best-effort: missing directory returns empty, not raises.
        assert _read_archive_log_lines("controller", None) == []
