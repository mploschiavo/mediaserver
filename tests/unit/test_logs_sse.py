"""Unit tests for ``api.services.logs_sse``.

Covers compile_q (literal vs regex vs invalid), should_emit_log_line
(action/level/q precedence), and format_sse_event (frame layout).
"""

from __future__ import annotations

import json
import re
import time

from media_stack.api.services.logs_sse import (
    compile_q,
    format_sse_event,
    should_emit_log_line,
)


class TestCompileQ:
    def test_empty_returns_none(self) -> None:
        assert compile_q("") is None
        assert compile_q(None) is None

    def test_literal_substring_is_case_insensitive(self) -> None:
        pat = compile_q("ENVOY")
        assert pat is not None
        assert pat.search("envoy crashed") is not None

    def test_literal_substring_escapes_regex_metachars(self) -> None:
        pat = compile_q("a.b")
        assert pat is not None
        assert pat.search("a.b") is not None
        # Confirm the dot was escaped (not a wildcard).
        assert pat.search("aXb") is None

    def test_regex_form_with_i_flag(self) -> None:
        pat = compile_q("/err.*timeout/i")
        assert pat is not None
        assert pat.search("ERR connection timeout reached") is not None
        assert pat.search("ok") is None

    def test_regex_form_without_i_flag(self) -> None:
        pat = compile_q("/Foo/")
        assert pat is not None
        assert pat.search("Foo") is not None
        assert pat.search("foo") is None

    def test_invalid_regex_falls_back_to_literal(self) -> None:
        # Unbalanced paren → re.error → falls back to literal-substring
        # match on the *raw* input. So a log line containing the typo
        # itself ("/foo(/i") would still match — the operator just sees
        # their query echoed back instead of crashing the stream.
        pat = compile_q("/foo(/i")
        assert pat is not None
        assert pat.search("/foo(/i appears here") is not None


class TestShouldEmitLogLine:
    def test_no_filters_passes_all(self) -> None:
        assert should_emit_log_line("hello", "")

    def test_action_filter_match(self) -> None:
        assert should_emit_log_line("msg", "envoy-config",
                                    action_filter="envoy-config")

    def test_action_filter_mismatch(self) -> None:
        assert not should_emit_log_line("msg", "scan-completed",
                                        action_filter="envoy-config")

    def test_action_filter_empty_action_field(self) -> None:
        # The current_action is unset — request asked for a specific
        # action, so the line is NOT a match.
        assert not should_emit_log_line("msg", "",
                                        action_filter="envoy-config")

    def test_level_filter_error_match(self) -> None:
        assert should_emit_log_line("[ERROR] boom", "",
                                    level_filter="error")

    def test_level_filter_error_miss(self) -> None:
        assert not should_emit_log_line("[INFO] all good", "",
                                        level_filter="error")

    def test_level_filter_warning_synonyms(self) -> None:
        assert should_emit_log_line("WARN: leaky", "", level_filter="warning")
        assert should_emit_log_line("WARNING: leaky", "",
                                    level_filter="warning")

    def test_level_filter_unknown_token_passthrough(self) -> None:
        # Unknown level token → pattern lookup misses → no level filter
        # is applied (line passes other filters). This intentionally
        # mirrors the polling endpoint's permissive behaviour.
        assert should_emit_log_line("anything", "", level_filter="bogus")

    def test_q_pattern_match_required(self) -> None:
        pat = compile_q("envoy")
        assert should_emit_log_line("envoy connection lost", "", q_pattern=pat)
        assert not should_emit_log_line("nothing here", "", q_pattern=pat)

    def test_combined_filters_all_must_pass(self) -> None:
        pat = compile_q("/timeout/i")
        # Action match, level match, q match → emit.
        assert should_emit_log_line(
            "[ERROR] read TIMEOUT", "envoy",
            action_filter="envoy", level_filter="error", q_pattern=pat,
        )
        # Action match, level match, q miss → drop.
        assert not should_emit_log_line(
            "[ERROR] connection refused", "envoy",
            action_filter="envoy", level_filter="error", q_pattern=pat,
        )
        # Action miss → drop regardless of other matches.
        assert not should_emit_log_line(
            "[ERROR] timeout", "scan",
            action_filter="envoy", level_filter="error", q_pattern=pat,
        )


class TestFormatSseEvent:
    def test_frame_shape(self) -> None:
        frame = format_sse_event(
            42, 1700_000_000.0, "hello world", "envoy-config",
        )
        assert isinstance(frame, bytes)
        text = frame.decode("utf-8")
        assert text.startswith("id: 42\n")
        assert text.endswith("\n\n")
        # data: {json}\n is the middle line.
        assert "data: " in text

    def test_data_is_valid_json_with_required_fields(self) -> None:
        frame = format_sse_event(7, 1700_000_000.0, "msg", "act")
        text = frame.decode("utf-8")
        data_line = next(
            ln for ln in text.splitlines() if ln.startswith("data: ")
        )
        payload = json.loads(data_line[len("data: "):])
        assert payload["seq"] == 7
        assert payload["msg"] == "msg"
        assert payload["action"] == "act"
        assert isinstance(payload["ts"], str)
        # ISO-ish prefix — the local-time encoder emits
        # "YYYY-MM-DDTHH:MM:SS".
        assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}",
                        payload["ts"])

    def test_ts_uses_localtime(self) -> None:
        # Pin the encoded timestamp by formatting the same epoch through
        # localtime ourselves and asserting equality.
        epoch = 1700_000_000.0
        expected = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(epoch))
        frame = format_sse_event(1, epoch, "x", "")
        text = frame.decode("utf-8")
        data_line = next(
            ln for ln in text.splitlines() if ln.startswith("data: ")
        )
        payload = json.loads(data_line[len("data: "):])
        assert payload["ts"] == expected
