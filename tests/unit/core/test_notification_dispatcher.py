"""Unit tests for :class:`NotificationDispatcher`."""

from __future__ import annotations

import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.core.notifications.dispatcher import (
    Channel,
    DeliveryStatus,
    Notification,
    NotificationDispatcher,
    NotificationResult,
)


class _RecordingChannel:
    """Minimal :class:`Channel` implementation for tests."""

    def __init__(
        self,
        name: str,
        *,
        accepts_events: set[str] | None = None,
        raises: Exception | None = None,
        status: DeliveryStatus = DeliveryStatus.OK,
        accepts_raises: Exception | None = None,
        return_value: object | None = None,
    ) -> None:
        self.name = name
        self._accepts = accepts_events
        self._raises = raises
        self._status = status
        self._accepts_raises = accepts_raises
        self._return_value = return_value
        self.seen: list[Notification] = []

    def accepts(self, event_type: str) -> bool:
        if self._accepts_raises is not None:
            raise self._accepts_raises
        if self._accepts is None:
            return True
        return event_type in self._accepts

    def send(self, notification: Notification) -> NotificationResult:
        self.seen.append(notification)
        if self._raises is not None:
            raise self._raises
        if self._return_value is not None:
            # Intentional escape hatch for "misbehaving channel" test.
            return self._return_value  # type: ignore[return-value]
        return NotificationResult(
            channel_name=self.name,
            status=self._status,
            detail="",
        )


def _make_notification(event_type: str = "auth.new_location", **overrides) -> Notification:
    base = dict(
        event_type=event_type,
        title="Login from new location",
        body="user=alice ip=1.2.3.4",
        severity="warn",
        structured={"user": "alice", "ip": "1.2.3.4"},
        dedupe_key="",
    )
    base.update(overrides)
    return Notification(**base)


class DispatcherRegistrationTests(unittest.TestCase):
    def test_register_then_dispatch_delivers_to_channel(self):
        d = NotificationDispatcher()
        ch = _RecordingChannel("w1")
        d.register(ch)
        results = d.dispatch(_make_notification())
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].channel_name, "w1")
        self.assertEqual(results[0].status, DeliveryStatus.OK)
        self.assertEqual(len(ch.seen), 1)

    def test_multiple_channels_all_called(self):
        d = NotificationDispatcher()
        a = _RecordingChannel("a")
        b = _RecordingChannel("b")
        d.register(a)
        d.register(b)
        results = d.dispatch(_make_notification())
        names = sorted(r.channel_name for r in results)
        self.assertEqual(names, ["a", "b"])
        self.assertEqual(len(a.seen), 1)
        self.assertEqual(len(b.seen), 1)

    def test_channel_skipped_when_accepts_false(self):
        d = NotificationDispatcher()
        # Only accepts auth.ban events
        a = _RecordingChannel("a", accepts_events={"auth.ban"})
        b = _RecordingChannel("b")
        d.register(a)
        d.register(b)
        results = d.dispatch(_make_notification(event_type="auth.new_location"))
        names = [r.channel_name for r in results]
        self.assertEqual(names, ["b"])
        self.assertEqual(len(a.seen), 0)
        self.assertEqual(len(b.seen), 1)

    def test_send_exception_becomes_undeliverable(self):
        d = NotificationDispatcher()
        ch = _RecordingChannel("boom", raises=RuntimeError("smtp down"))
        d.register(ch)
        results = d.dispatch(_make_notification())
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, DeliveryStatus.UNDELIVERABLE)
        self.assertIn("smtp down", results[0].detail)

    def test_accepts_exception_becomes_undeliverable(self):
        d = NotificationDispatcher()
        ch = _RecordingChannel(
            "broken-filter", accepts_raises=ValueError("bad config"),
        )
        d.register(ch)
        results = d.dispatch(_make_notification())
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, DeliveryStatus.UNDELIVERABLE)
        self.assertIn("bad config", results[0].detail)

    def test_register_replaces_same_name(self):
        d = NotificationDispatcher()
        first = _RecordingChannel("shared")
        second = _RecordingChannel("shared")
        d.register(first)
        d.register(second)
        d.dispatch(_make_notification())
        self.assertEqual(len(first.seen), 0)
        self.assertEqual(len(second.seen), 1)
        self.assertEqual(d.channels(), ["shared"])

    def test_unregister_unknown_is_noop(self):
        d = NotificationDispatcher()
        d.unregister("never-registered")  # must not raise
        d.register(_RecordingChannel("a"))
        d.unregister("a")
        self.assertEqual(d.channels(), [])

    def test_channels_returns_registered_names(self):
        d = NotificationDispatcher()
        d.register(_RecordingChannel("x"))
        d.register(_RecordingChannel("y"))
        self.assertEqual(sorted(d.channels()), ["x", "y"])

    def test_channel_returning_wrong_type_is_undeliverable(self):
        d = NotificationDispatcher()
        ch = _RecordingChannel("sloppy", return_value="not a result")
        d.register(ch)
        results = d.dispatch(_make_notification())
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, DeliveryStatus.UNDELIVERABLE)
        self.assertIn("str", results[0].detail)

    def test_channel_protocol_isinstance(self):
        # ``runtime_checkable`` lets config loaders sanity-check
        # plugin instances — confirm the recording channel passes.
        self.assertIsInstance(_RecordingChannel("x"), Channel)


class DispatcherDedupeTests(unittest.TestCase):
    def test_dedupe_suppresses_repeat_within_window(self):
        d = NotificationDispatcher(dedupe_window_seconds=60)
        ch = _RecordingChannel("a")
        d.register(ch)
        note = _make_notification(dedupe_key="bf:alice")
        r1 = d.dispatch(note)
        r2 = d.dispatch(note)
        self.assertEqual(r1[0].status, DeliveryStatus.OK)
        self.assertEqual(len(r2), 1)
        self.assertEqual(r2[0].status, DeliveryStatus.DROPPED)
        self.assertEqual(r2[0].channel_name, "__dispatcher__")
        # Only first dispatch reached the channel.
        self.assertEqual(len(ch.seen), 1)

    def test_empty_dedupe_key_never_deduplicates(self):
        d = NotificationDispatcher(dedupe_window_seconds=60)
        ch = _RecordingChannel("a")
        d.register(ch)
        note = _make_notification(dedupe_key="")
        d.dispatch(note)
        d.dispatch(note)
        self.assertEqual(len(ch.seen), 2)

    def test_dedupe_window_expiry_allows_resend(self):
        d = NotificationDispatcher(dedupe_window_seconds=60)
        ch = _RecordingChannel("a")
        d.register(ch)
        note = _make_notification(dedupe_key="bf:alice")

        fake_now = [1000.0]

        def fake_monotonic() -> float:
            return fake_now[0]

        with mock.patch(
            "media_stack.core.notifications.dispatcher.time.monotonic",
            side_effect=fake_monotonic,
        ):
            d.dispatch(note)
            # Advance past the window
            fake_now[0] += 61.0
            results = d.dispatch(note)

        self.assertEqual(results[0].status, DeliveryStatus.OK)
        self.assertEqual(len(ch.seen), 2)

    def test_deduplicate_method_updates_window(self):
        d = NotificationDispatcher(dedupe_window_seconds=60)
        ch = _RecordingChannel("a")
        d.register(ch)
        note = _make_notification(dedupe_key="bf:alice")
        d.dispatch(note)
        # Setting the window to 0 disables dedupe and clears history.
        d.deduplicate(0)
        results = d.dispatch(note)
        self.assertEqual(results[0].status, DeliveryStatus.OK)
        self.assertEqual(len(ch.seen), 2)

    def test_deduplicate_negative_clamps_to_zero(self):
        d = NotificationDispatcher(dedupe_window_seconds=60)
        d.deduplicate(-5)
        ch = _RecordingChannel("a")
        d.register(ch)
        note = _make_notification(dedupe_key="bf:alice")
        d.dispatch(note)
        d.dispatch(note)
        self.assertEqual(len(ch.seen), 2)

    def test_dedupe_disabled_at_construction(self):
        d = NotificationDispatcher(dedupe_window_seconds=0)
        ch = _RecordingChannel("a")
        d.register(ch)
        note = _make_notification(dedupe_key="x")
        d.dispatch(note)
        d.dispatch(note)
        self.assertEqual(len(ch.seen), 2)

    def test_dedupe_prunes_expired_entries(self):
        d = NotificationDispatcher(dedupe_window_seconds=30)
        ch = _RecordingChannel("a")
        d.register(ch)
        # Seed entries that will be expired by the time we dispatch.
        with d._lock:  # noqa: SLF001 - whitebox test of prune path
            d._dedupe_seen["ancient"] = time.monotonic() - 3600
            d._dedupe_seen["also-ancient"] = time.monotonic() - 7200
        d.dispatch(_make_notification(dedupe_key="fresh"))
        self.assertNotIn("ancient", d._dedupe_seen)
        self.assertNotIn("also-ancient", d._dedupe_seen)
        self.assertIn("fresh", d._dedupe_seen)


class DispatcherConcurrencyTests(unittest.TestCase):
    def test_parallel_dispatch_is_safe(self):
        d = NotificationDispatcher(dedupe_window_seconds=0)
        channels = [_RecordingChannel(f"c{i}") for i in range(3)]
        for ch in channels:
            d.register(ch)

        errors: list[BaseException] = []

        def worker(worker_id: int) -> None:
            try:
                for i in range(100):
                    d.dispatch(
                        _make_notification(
                            event_type="auth.new_location",
                            title=f"w{worker_id}-{i}",
                        )
                    )
            except BaseException as exc:  # pragma: no cover - collected
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        # 10 threads × 100 events × 3 channels = 3000 sends.
        total = sum(len(ch.seen) for ch in channels)
        self.assertEqual(total, 10 * 100 * 3)

    def test_concurrent_register_and_dispatch(self):
        d = NotificationDispatcher(dedupe_window_seconds=0)
        stop = threading.Event()
        errors: list[BaseException] = []

        def registrar() -> None:
            try:
                i = 0
                while not stop.is_set():
                    d.register(_RecordingChannel(f"c{i % 5}"))
                    d.unregister(f"c{(i + 1) % 5}")
                    i += 1
            except BaseException as exc:  # pragma: no cover
                errors.append(exc)

        def dispatcher_worker() -> None:
            try:
                for _ in range(200):
                    d.dispatch(_make_notification())
            except BaseException as exc:  # pragma: no cover
                errors.append(exc)

        r = threading.Thread(target=registrar)
        ds = [threading.Thread(target=dispatcher_worker) for _ in range(3)]
        r.start()
        for t in ds:
            t.start()
        for t in ds:
            t.join()
        stop.set()
        r.join()
        self.assertEqual(errors, [])


class NotificationDataclassTests(unittest.TestCase):
    def test_notification_is_immutable(self):
        n = _make_notification()
        with self.assertRaises(Exception):  # frozen dataclass → FrozenInstanceError
            n.title = "mutated"  # type: ignore[misc]

    def test_structured_default_is_independent_per_instance(self):
        a = Notification(
            event_type="x", title="t", body="b", severity="info",
        )
        b = Notification(
            event_type="y", title="t", body="b", severity="info",
        )
        # default_factory: separate dict instances, not a shared default.
        self.assertIsNot(a.structured, b.structured)

    def test_result_default_attempt_is_one(self):
        r = NotificationResult(channel_name="c", status=DeliveryStatus.OK)
        self.assertEqual(r.attempt, 1)
        self.assertEqual(r.detail, "")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
