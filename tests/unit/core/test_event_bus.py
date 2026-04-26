"""Unit tests for the thread-safe event bus.

Tests assert the behavioural contract documented in ``bus.py``:
ordering, fault isolation, re-entrancy under ``RLock``, and
idempotent unsubscribe. Concurrency coverage uses a moderate thread
count (10 x 100) — enough to exercise the lock without creating a
flaky test that depends on scheduler fairness.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import ClassVar

import pytest

from media_stack.core.events import Event, EventBus
from media_stack.core.events.bus import SubscriberHandle


@dataclass(frozen=True, kw_only=True)
class _Ping(Event):
    EVENT_TYPE: ClassVar[str] = "test.ping"

    payload: str = ""


@dataclass(frozen=True, kw_only=True)
class _Pong(Event):
    EVENT_TYPE: ClassVar[str] = "test.pong"

    payload: str = ""


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


def test_subscribe_then_publish_delivers(bus: EventBus) -> None:
    received: list[Event] = []
    bus.subscribe("test.ping", received.append)

    evt = _Ping(payload="hi")
    bus.publish(evt)

    assert received == [evt]


def test_multiple_subscribers_receive_in_subscription_order(bus: EventBus) -> None:
    order: list[str] = []
    bus.subscribe("test.ping", lambda e: order.append("first"))
    bus.subscribe("test.ping", lambda e: order.append("second"))
    bus.subscribe("test.ping", lambda e: order.append("third"))

    bus.publish(_Ping())

    assert order == ["first", "second", "third"]


def test_subscribe_all_receives_every_event_type(bus: EventBus) -> None:
    seen: list[str] = []
    bus.subscribe_all(lambda e: seen.append(e.event_type))

    bus.publish(_Ping())
    bus.publish(_Pong())
    bus.publish(_Ping())

    assert seen == ["test.ping", "test.pong", "test.ping"]


def test_typed_subscribers_fire_before_catch_all(bus: EventBus) -> None:
    order: list[str] = []
    bus.subscribe("test.ping", lambda e: order.append("typed"))
    bus.subscribe_all(lambda e: order.append("all"))

    bus.publish(_Ping())

    assert order == ["typed", "all"]


def test_unsubscribe_stops_delivery(bus: EventBus) -> None:
    received: list[Event] = []
    handle = bus.subscribe("test.ping", received.append)
    bus.publish(_Ping(payload="a"))

    bus.unsubscribe(handle)
    bus.publish(_Ping(payload="b"))

    assert len(received) == 1
    assert received[0].to_dict()["payload"] == "a"


def test_unsubscribe_is_idempotent(bus: EventBus) -> None:
    handle = bus.subscribe("test.ping", lambda e: None)
    bus.unsubscribe(handle)
    bus.unsubscribe(handle)  # must not raise
    bus.unsubscribe(SubscriberHandle(_id=9999))  # unknown id, also fine


def test_unsubscribe_on_subscribe_all(bus: EventBus) -> None:
    seen: list[Event] = []
    handle = bus.subscribe_all(seen.append)
    bus.publish(_Ping())
    bus.unsubscribe(handle)
    bus.publish(_Ping())
    assert len(seen) == 1


def test_raising_handler_does_not_abort_dispatch(bus: EventBus) -> None:
    received: list[str] = []

    def boom(_e: Event) -> None:
        raise RuntimeError("handler failure")

    bus.subscribe("test.ping", lambda e: received.append("before"))
    bus.subscribe("test.ping", boom)
    bus.subscribe("test.ping", lambda e: received.append("after"))

    bus.publish(_Ping())

    assert received == ["before", "after"]


def test_raising_handler_logs_at_debug(
    bus: EventBus, caplog: pytest.LogCaptureFixture
) -> None:
    def boom(_e: Event) -> None:
        raise ValueError("nope")

    bus.subscribe("test.ping", boom)
    with caplog.at_level("DEBUG", logger="media_stack.core.events.bus"):
        bus.publish(_Ping())

    assert any("event handler raised" in r.message for r in caplog.records)


def test_clear_drops_subscribers(bus: EventBus) -> None:
    bus.subscribe("test.ping", lambda e: None)
    bus.subscribe_all(lambda e: None)
    assert bus.subscriber_count() == 2

    bus.clear()
    assert bus.subscriber_count() == 0
    assert bus.subscriber_count("test.ping") == 0


def test_subscriber_count_across_cycles(bus: EventBus) -> None:
    assert bus.subscriber_count() == 0
    h1 = bus.subscribe("test.ping", lambda e: None)
    h2 = bus.subscribe("test.ping", lambda e: None)
    h3 = bus.subscribe("test.pong", lambda e: None)
    h4 = bus.subscribe_all(lambda e: None)

    # total: 3 typed + 1 catch-all = 4
    assert bus.subscriber_count() == 4
    # per-type: 2 ping typed + 1 catch-all = 3
    assert bus.subscriber_count("test.ping") == 3
    # per-type: 1 pong typed + 1 catch-all = 2
    assert bus.subscriber_count("test.pong") == 2
    # an untyped name still counts catch-alls
    assert bus.subscriber_count("test.missing") == 1

    bus.unsubscribe(h1)
    bus.unsubscribe(h3)
    bus.unsubscribe(h4)

    assert bus.subscriber_count() == 1
    assert bus.subscriber_count("test.ping") == 1
    assert bus.subscriber_count("test.pong") == 0

    bus.unsubscribe(h2)
    assert bus.subscriber_count() == 0


def test_reentrant_publish_does_not_deadlock(bus: EventBus) -> None:
    """A handler for ``test.ping`` that itself publishes ``test.pong``
    must complete rather than deadlock — this is exactly why the bus
    uses an ``RLock`` not a ``Lock``."""
    pong_seen: list[Event] = []
    bus.subscribe("test.pong", pong_seen.append)

    def cascade(_e: Event) -> None:
        bus.publish(_Pong(payload="from-handler"))

    bus.subscribe("test.ping", cascade)
    bus.publish(_Ping())

    assert len(pong_seen) == 1
    assert pong_seen[0].to_dict()["payload"] == "from-handler"


def test_handler_that_subscribes_during_dispatch_does_not_affect_current_publish(
    bus: EventBus,
) -> None:
    """Subscribing from inside a handler must not mutate the snapshot
    the current publish is iterating — the new subscriber sees the
    *next* publish instead. Documents the 'snapshot under lock'
    semantics."""
    fired: list[str] = []

    def first(_e: Event) -> None:
        fired.append("first")
        bus.subscribe("test.ping", lambda _e2: fired.append("late"))

    bus.subscribe("test.ping", first)
    bus.publish(_Ping())
    assert fired == ["first"]

    bus.publish(_Ping())
    # both the original and the late-added handler fire on the 2nd publish
    assert fired == ["first", "first", "late"]


def test_thread_safety_concurrent_publish() -> None:
    bus = EventBus()
    received: list[Event] = []
    lock = threading.Lock()

    def collector(e: Event) -> None:
        with lock:
            received.append(e)

    bus.subscribe("test.ping", collector)

    errors: list[BaseException] = []

    def worker() -> None:
        try:
            for i in range(100):
                bus.publish(_Ping(payload=str(i)))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert len(received) == 10 * 100


def test_thread_safety_concurrent_subscribe_and_publish() -> None:
    bus = EventBus()
    errors: list[BaseException] = []
    stop = threading.Event()

    def publisher() -> None:
        try:
            while not stop.is_set():
                bus.publish(_Ping())
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    def churner() -> None:
        try:
            for _ in range(200):
                h = bus.subscribe("test.ping", lambda _e: None)
                bus.unsubscribe(h)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    pubs = [threading.Thread(target=publisher) for _ in range(3)]
    churns = [threading.Thread(target=churner) for _ in range(3)]
    for t in pubs + churns:
        t.start()
    for t in churns:
        t.join()
    stop.set()
    for t in pubs:
        t.join()

    assert errors == []


def test_publish_with_no_subscribers_is_noop(bus: EventBus) -> None:
    bus.publish(_Ping())  # must not raise


def test_default_timestamp_is_iso_string(bus: EventBus) -> None:
    evt = _Ping()
    assert isinstance(evt.ts, str)
    # reasonable ISO-8601 with UTC offset or 'Z'
    assert "T" in evt.ts
