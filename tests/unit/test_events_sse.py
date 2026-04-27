"""Unit tests for ``api.services.events_sse``.

Covers the topic mapping, query-param parsing, the topic-filter
predicate, and the SSE frame formatter. The SSE handler itself
(``handlers_get._handle_events_sse``) is exercised by an integration
test elsewhere; here we keep things to pure-function coverage so the
helpers stay unit-testable in isolation.
"""

from __future__ import annotations

import json

from media_stack.api.services.events_sse import (
    EVENT_TYPE_TO_TOPIC,
    HEARTBEAT_FRAME,
    KNOWN_TOPICS,
    event_matches_topics,
    event_topic,
    format_event_frame,
    parse_topics,
)
from media_stack.core.events.job_events import JobCompleted, JobStarted


class TestEventTopic:
    def test_known_prefix_returns_topic(self) -> None:
        assert event_topic("job.started") == "jobs"
        assert event_topic("job.completed") == "jobs"
        assert event_topic("login.succeeded") == "sessions"
        assert event_topic("session.created") == "sessions"
        assert event_topic("ban.applied") == "sessions"
        assert event_topic("password.changed") == "sessions"
        assert event_topic("media_integrity.duplicate.review_needed") == (
            "media_integrity"
        )

    def test_unknown_prefix_returns_empty_string(self) -> None:
        assert event_topic("widget.foo") == ""

    def test_blank_event_type_returns_empty_string(self) -> None:
        assert event_topic("") == ""

    def test_no_dot_returns_topic_when_prefix_known(self) -> None:
        # ``"job"`` (no dot) is still the prefix; the partition-on-dot
        # treats the whole string as the prefix segment.
        assert event_topic("job") == "jobs"

    def test_known_topics_set_matches_mapping(self) -> None:
        assert KNOWN_TOPICS == frozenset(EVENT_TYPE_TO_TOPIC.values())


class TestParseTopics:
    def test_empty_returns_all_known_topics(self) -> None:
        assert parse_topics(None) == KNOWN_TOPICS
        assert parse_topics("") == KNOWN_TOPICS

    def test_single_topic_returns_singleton(self) -> None:
        assert parse_topics("jobs") == frozenset({"jobs"})

    def test_multi_topic_csv_returns_intersection(self) -> None:
        assert parse_topics("jobs,sessions") == frozenset(
            {"jobs", "sessions"},
        )

    def test_unknown_topic_silently_dropped(self) -> None:
        # Forward-compat: a UI shipping a future topic name shouldn't
        # break against an older controller. Drop the unknown name and
        # honour the rest.
        assert parse_topics("jobs,future_topic") == frozenset({"jobs"})

    def test_only_unknown_topics_returns_empty_set(self) -> None:
        # No matches → empty set. The handler treats this as "the
        # client asked for nothing legal" and emits no events.
        assert parse_topics("future_topic") == frozenset()

    def test_whitespace_in_csv_trimmed(self) -> None:
        assert parse_topics("jobs , sessions") == frozenset(
            {"jobs", "sessions"},
        )

    def test_blank_segments_skipped(self) -> None:
        assert parse_topics("jobs,,sessions,") == frozenset(
            {"jobs", "sessions"},
        )


class TestEventMatchesTopics:
    def test_event_in_requested_topic_matches(self) -> None:
        ev = JobStarted(
            run_id="01", job_name="x", triggered_by="cron",
        )
        assert event_matches_topics(ev, frozenset({"jobs"})) is True

    def test_event_not_in_requested_topic_filtered(self) -> None:
        ev = JobStarted(
            run_id="01", job_name="x", triggered_by="cron",
        )
        assert event_matches_topics(ev, frozenset({"sessions"})) is False

    def test_unmapped_event_type_always_filtered(self) -> None:
        # Construct an Event-like instance with a totally unknown
        # event_type. Topic resolution returns "", so the predicate
        # returns False even when ``"jobs"`` is in the requested set.
        from dataclasses import dataclass
        from typing import ClassVar

        from media_stack.core.events.bus import Event

        @dataclass(frozen=True, kw_only=True)
        class UnmappedEvent(Event):
            EVENT_TYPE: ClassVar[str] = "widget.changed"

        ev = UnmappedEvent()
        assert event_matches_topics(ev, frozenset({"jobs"})) is False
        # And of course not in any other set either.
        assert event_matches_topics(ev, KNOWN_TOPICS) is False


class TestFormatEventFrame:
    def test_frame_layout_includes_event_and_data_lines(self) -> None:
        ev = JobCompleted(
            run_id="01J5",
            job_name="scan",
            status="ok",
            elapsed=1.25,
        )
        frame = format_event_frame(ev)
        assert frame.endswith(b"\n\n"), (
            "SSE block must end with a blank line"
        )
        assert frame.startswith(b"event: job.completed\n")
        body_line = frame.split(b"\n")[1]
        assert body_line.startswith(b"data: ")

    def test_data_line_is_valid_json_with_full_payload(self) -> None:
        ev = JobCompleted(
            run_id="01J5",
            job_name="scan",
            status="ok",
            elapsed=1.25,
            error="",
        )
        frame = format_event_frame(ev)
        data_line = next(
            line[len(b"data: "):]
            for line in frame.split(b"\n")
            if line.startswith(b"data: ")
        )
        payload = json.loads(data_line)
        # Every dataclass field is on the wire — including the
        # auto-populated ``event_type`` and the timestamped ``ts``.
        assert payload["run_id"] == "01J5"
        assert payload["job_name"] == "scan"
        assert payload["status"] == "ok"
        assert payload["elapsed"] == 1.25
        assert payload["event_type"] == "job.completed"
        assert "ts" in payload

    def test_heartbeat_is_an_sse_comment_line(self) -> None:
        # Comment lines (``: ...``) are valid SSE per the W3C spec
        # and never trigger a client-side ``message`` event — the
        # whole point is to pass through proxies without surfacing
        # to the consumer.
        assert HEARTBEAT_FRAME.startswith(b": ")
        assert HEARTBEAT_FRAME.endswith(b"\n\n")
