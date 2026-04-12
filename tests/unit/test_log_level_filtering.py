"""Tests for log-level-aware filtering in runtime_platform.

The log system uses [PREFIX] tags to determine level:
  DEBUG=0, INFO/OK/WAIT/ACTION/JOB=1, WARN=2, ERR/ERROR=3
  TRACE maps to DEBUG (0) so traces only show in DEBUG mode.

Setting MEDIA_STACK_LOG_LEVEL filters messages below that level.
"""

from __future__ import annotations

import io
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


@pytest.fixture(autouse=True)
def _reset_log_level():
    """Reset log level to INFO after each test."""
    import media_stack.services.runtime_platform as rp
    original = rp._current_log_level
    yield
    rp._current_log_level = original
    os.environ.pop("MEDIA_STACK_LOG_LEVEL", None)


def _capture_log(msg: str) -> str:
    """Call runtime_platform.log and return captured stdout (or empty if filtered)."""
    import media_stack.services.runtime_platform as rp
    buf = io.StringIO()
    with mock.patch("builtins.print", side_effect=lambda *a, **kw: buf.write(str(a[0]) + "\n")):
        rp.log(msg)
    return buf.getvalue()


class TestExtractLevel:
    """Test _extract_level prefix parsing."""

    def test_debug_prefix(self):
        from media_stack.services.runtime_platform import _extract_level
        assert _extract_level("[DEBUG] something") == 0

    def test_info_prefix(self):
        from media_stack.services.runtime_platform import _extract_level
        assert _extract_level("[INFO] something") == 1

    def test_ok_prefix(self):
        from media_stack.services.runtime_platform import _extract_level
        assert _extract_level("[OK] something") == 1

    def test_warn_prefix(self):
        from media_stack.services.runtime_platform import _extract_level
        assert _extract_level("[WARN] something") == 2

    def test_error_prefix(self):
        from media_stack.services.runtime_platform import _extract_level
        assert _extract_level("[ERR] something") == 3
        assert _extract_level("[ERROR] something") == 3

    def test_trace_is_debug_level(self):
        from media_stack.services.runtime_platform import _extract_level
        assert _extract_level("[TRACE] traceback line") == 0

    def test_action_is_info_level(self):
        from media_stack.services.runtime_platform import _extract_level
        assert _extract_level("[ACTION] bootstrap: starting") == 1

    def test_job_is_info_level(self):
        from media_stack.services.runtime_platform import _extract_level
        assert _extract_level("[JOB] configure-libraries: starting") == 1

    def test_heal_is_info_level(self):
        from media_stack.services.runtime_platform import _extract_level
        assert _extract_level("[HEAL] Marked sonarr as failed") == 1

    def test_wait_is_info_level(self):
        from media_stack.services.runtime_platform import _extract_level
        assert _extract_level("[WAIT] jellyfin not ready") == 1

    def test_cred_is_info_level(self):
        from media_stack.services.runtime_platform import _extract_level
        assert _extract_level("[CRED] sonarr: passed") == 1

    def test_no_prefix_defaults_to_info(self):
        from media_stack.services.runtime_platform import _extract_level
        assert _extract_level("plain message") == 1

    def test_empty_string(self):
        from media_stack.services.runtime_platform import _extract_level
        assert _extract_level("") == 1

    def test_case_insensitive_prefix(self):
        from media_stack.services.runtime_platform import _extract_level
        assert _extract_level("[debug] lower case") == 0
        assert _extract_level("[WARN] upper case") == 2

    def test_leading_whitespace(self):
        from media_stack.services.runtime_platform import _extract_level
        assert _extract_level("  [DEBUG] indented") == 0


class TestLogLevelFiltering:
    """Test that log() filters messages below the current level."""

    def test_info_level_shows_info(self):
        import media_stack.services.runtime_platform as rp
        rp.set_log_level("INFO")
        output = _capture_log("[INFO] visible")
        assert "visible" in output

    def test_info_level_hides_debug(self):
        import media_stack.services.runtime_platform as rp
        rp.set_log_level("INFO")
        output = _capture_log("[DEBUG] hidden")
        assert output == ""

    def test_debug_level_shows_debug(self):
        import media_stack.services.runtime_platform as rp
        rp.set_log_level("DEBUG")
        output = _capture_log("[DEBUG] visible")
        assert "visible" in output

    def test_debug_level_shows_all(self):
        import media_stack.services.runtime_platform as rp
        rp.set_log_level("DEBUG")
        for prefix in ("[DEBUG]", "[INFO]", "[OK]", "[WARN]", "[ERR]"):
            output = _capture_log(f"{prefix} msg")
            assert "msg" in output, f"{prefix} should be visible at DEBUG level"

    def test_warn_level_hides_info(self):
        import media_stack.services.runtime_platform as rp
        rp.set_log_level("WARN")
        assert _capture_log("[INFO] hidden") == ""
        assert "visible" in _capture_log("[WARN] visible")
        assert "visible" in _capture_log("[ERR] visible")

    def test_error_level_hides_warn(self):
        import media_stack.services.runtime_platform as rp
        rp.set_log_level("ERROR")
        assert _capture_log("[WARN] hidden") == ""
        assert _capture_log("[INFO] hidden") == ""
        assert "visible" in _capture_log("[ERR] visible")

    def test_trace_visible_at_debug(self):
        import media_stack.services.runtime_platform as rp
        rp.set_log_level("DEBUG")
        assert "traceback" in _capture_log("[TRACE] traceback line")

    def test_trace_hidden_at_info(self):
        import media_stack.services.runtime_platform as rp
        rp.set_log_level("INFO")
        assert _capture_log("[TRACE] traceback line") == ""

    def test_plain_message_is_info_level(self):
        """Messages without a prefix default to INFO level."""
        import media_stack.services.runtime_platform as rp
        rp.set_log_level("INFO")
        assert "hello" in _capture_log("hello")
        rp.set_log_level("WARN")
        assert _capture_log("hello") == ""


class TestSetGetLogLevel:
    """Test set_log_level / get_log_level API."""

    def test_set_and_get(self):
        from media_stack.services.runtime_platform import set_log_level, get_log_level
        set_log_level("DEBUG")
        assert get_log_level() == "DEBUG"
        set_log_level("WARN")
        assert get_log_level() == "WARN"

    def test_set_updates_env(self):
        from media_stack.services.runtime_platform import set_log_level
        set_log_level("DEBUG")
        assert os.environ.get("MEDIA_STACK_LOG_LEVEL") == "DEBUG"

    def test_invalid_level_returns_current(self):
        from media_stack.services.runtime_platform import set_log_level, get_log_level
        set_log_level("INFO")
        result = set_log_level("INVALID")
        assert result == "INFO"
        assert get_log_level() == "INFO"

    def test_case_insensitive(self):
        from media_stack.services.runtime_platform import set_log_level, get_log_level
        set_log_level("debug")
        assert get_log_level() == "DEBUG"

    def test_all_valid_levels(self):
        from media_stack.services.runtime_platform import set_log_level, get_log_level
        for level in ("DEBUG", "INFO", "WARN", "ERROR"):
            set_log_level(level)
            assert get_log_level() == level


class TestLogLevelConstants:
    """Test that level ordering is correct."""

    def test_level_order(self):
        from media_stack.services.runtime_platform import _LOG_LEVEL_ORDER
        assert _LOG_LEVEL_ORDER["DEBUG"] < _LOG_LEVEL_ORDER["INFO"]
        assert _LOG_LEVEL_ORDER["INFO"] < _LOG_LEVEL_ORDER["WARN"]
        assert _LOG_LEVEL_ORDER["WARN"] < _LOG_LEVEL_ORDER["ERROR"]

    def test_all_prefixes_mapped(self):
        """Every known prefix maps to a valid level."""
        from media_stack.services.runtime_platform import _PREFIX_TO_LEVEL
        for prefix, level in _PREFIX_TO_LEVEL.items():
            assert 0 <= level <= 3, f"Prefix {prefix} has invalid level {level}"

    def test_known_prefixes_present(self):
        from media_stack.services.runtime_platform import _PREFIX_TO_LEVEL
        for expected in ("DEBUG", "INFO", "OK", "WARN", "ERR", "ERROR",
                         "TRACE", "ACTION", "JOB", "WAIT", "RETRY",
                         "CRED", "HEAL"):
            assert expected in _PREFIX_TO_LEVEL, f"Missing prefix: {expected}"


class TestSubprocessLogFilter:
    """Test that subprocess log redirect also respects level filtering."""

    def test_subprocess_log_uses_extract_level(self):
        """Verify _extract_level is accessible for subprocess log redirect."""
        import media_stack.services.runtime_platform as rp
        assert callable(rp._extract_level)
        assert isinstance(rp._current_log_level, int)

    def test_subprocess_style_filtering(self):
        """Simulate the subprocess log filter pattern used in controller_serve."""
        import media_stack.services.runtime_platform as rp
        rp.set_log_level("INFO")
        captured = []

        def _subprocess_log(msg):
            if rp._extract_level(str(msg)) < rp._current_log_level:
                return
            captured.append(msg)

        _subprocess_log("[DEBUG] hidden in subprocess")
        _subprocess_log("[INFO] visible in subprocess")
        _subprocess_log("[ERR] visible error")
        assert len(captured) == 2
        assert "hidden" not in " ".join(captured)

    def test_subprocess_debug_mode_shows_all(self):
        """In DEBUG mode, subprocess should forward everything."""
        import media_stack.services.runtime_platform as rp
        rp.set_log_level("DEBUG")
        captured = []

        def _subprocess_log(msg):
            if rp._extract_level(str(msg)) < rp._current_log_level:
                return
            captured.append(msg)

        _subprocess_log("[DEBUG] debug msg")
        _subprocess_log("[TRACE] traceback")
        _subprocess_log("[INFO] info msg")
        _subprocess_log("[WARN] warning")
        assert len(captured) == 4


class TestLogOutputFormat:
    """Test that log output includes timestamp."""

    def test_log_includes_timestamp(self):
        import media_stack.services.runtime_platform as rp
        rp.set_log_level("DEBUG")
        output = _capture_log("[INFO] test message")
        # Should contain ISO-style timestamp
        assert "T" in output  # ISO timestamp separator
        assert "test message" in output

    def test_log_output_flushed(self):
        """Verify flush=True is passed to print."""
        import media_stack.services.runtime_platform as rp
        rp.set_log_level("DEBUG")
        flush_called = []
        with mock.patch("builtins.print", side_effect=lambda *a, **kw: flush_called.append(kw.get("flush"))):
            rp.log("[INFO] test")
        assert True in flush_called
