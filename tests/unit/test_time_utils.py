"""Unit tests for ``media_stack.core.time_utils``.

These exercise the public API only. The module is pure stdlib, so the
tests are too — no pytest-only features — though pytest still runs
them because it discovers ``unittest.TestCase`` subclasses.
"""

from __future__ import annotations

import re
import time
import unittest
from datetime import datetime, timezone

from media_stack.core.time_utils import (
    make_idempotency_key,
    new_request_id,
    parse_iso,
    utcnow_iso,
    utcnow_monotonic,
)

_HEX32_RE = re.compile(r"^[0-9a-f]{32}$")
_URLSAFE_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class UtcNowIsoTests(unittest.TestCase):
    def test_ends_with_zulu(self) -> None:
        self.assertTrue(utcnow_iso().endswith("Z"))

    def test_shape_matches_canonical_format(self) -> None:
        # YYYY-MM-DDTHH:MM:SS.ffffffZ — fixed width so lexical
        # sort equals chronological sort.
        self.assertRegex(
            utcnow_iso(),
            r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$",
        )

    def test_successive_calls_are_lexically_non_decreasing(self) -> None:
        a = utcnow_iso()
        b = utcnow_iso()
        self.assertLessEqual(a, b)

    def test_many_successive_calls_stay_sorted(self) -> None:
        samples = [utcnow_iso() for _ in range(50)]
        self.assertEqual(samples, sorted(samples))

    def test_no_offset_suffix(self) -> None:
        # Mixing "Z" and "+00:00" would break lexical ordering.
        s = utcnow_iso()
        self.assertNotIn("+", s)
        self.assertNotIn("-00:00", s)


class ParseIsoTests(unittest.TestCase):
    def test_round_trips_utcnow_iso(self) -> None:
        s = utcnow_iso()
        dt = parse_iso(s)
        self.assertIsNotNone(dt)
        assert dt is not None  # for the type checker
        self.assertEqual(dt.tzinfo, timezone.utc)

    def test_accepts_z_without_fraction(self) -> None:
        dt = parse_iso("2026-04-24T10:00:00Z")
        self.assertEqual(
            dt, datetime(2026, 4, 24, 10, 0, 0, tzinfo=timezone.utc)
        )

    def test_accepts_z_with_millisecond_fraction(self) -> None:
        dt = parse_iso("2026-04-24T10:00:00.123Z")
        self.assertEqual(
            dt,
            datetime(2026, 4, 24, 10, 0, 0, 123_000, tzinfo=timezone.utc),
        )

    def test_accepts_z_with_microsecond_fraction(self) -> None:
        dt = parse_iso("2026-04-24T10:00:00.123456Z")
        self.assertEqual(
            dt,
            datetime(2026, 4, 24, 10, 0, 0, 123_456, tzinfo=timezone.utc),
        )

    def test_accepts_naive_as_utc(self) -> None:
        dt = parse_iso("2026-04-24T10:00:00")
        self.assertIsNotNone(dt)
        assert dt is not None
        self.assertEqual(dt.tzinfo, timezone.utc)
        self.assertEqual(
            dt, datetime(2026, 4, 24, 10, 0, 0, tzinfo=timezone.utc)
        )

    def test_accepts_lowercase_z(self) -> None:
        dt = parse_iso("2026-04-24T10:00:00z")
        self.assertIsNotNone(dt)

    def test_accepts_explicit_offset_and_converts(self) -> None:
        dt = parse_iso("2026-04-24T12:00:00+02:00")
        self.assertIsNotNone(dt)
        assert dt is not None
        self.assertEqual(
            dt, datetime(2026, 4, 24, 10, 0, 0, tzinfo=timezone.utc)
        )

    def test_returns_none_for_empty_string(self) -> None:
        self.assertIsNone(parse_iso(""))

    def test_returns_none_for_whitespace(self) -> None:
        self.assertIsNone(parse_iso("   "))

    def test_returns_none_for_garbage(self) -> None:
        self.assertIsNone(parse_iso("not-a-date"))

    def test_returns_none_for_out_of_range_fields(self) -> None:
        self.assertIsNone(parse_iso("2026-13-45T99:99:99Z"))

    def test_returns_none_for_non_string_input(self) -> None:
        # Callers sometimes hand us values from untyped JSON.
        self.assertIsNone(parse_iso(None))  # type: ignore[arg-type]
        self.assertIsNone(parse_iso(12345))  # type: ignore[arg-type]


class MakeIdempotencyKeyTests(unittest.TestCase):
    def test_is_deterministic(self) -> None:
        self.assertEqual(
            make_idempotency_key("tenant", "create", "body"),
            make_idempotency_key("tenant", "create", "body"),
        )

    def test_order_matters(self) -> None:
        self.assertNotEqual(
            make_idempotency_key("a", "b"),
            make_idempotency_key("b", "a"),
        )

    def test_different_parts_give_different_keys(self) -> None:
        self.assertNotEqual(
            make_idempotency_key("tenant", "create"),
            make_idempotency_key("tenant", "delete"),
        )

    def test_output_is_32_lowercase_hex(self) -> None:
        key = make_idempotency_key("x", "y", "z")
        self.assertEqual(len(key), 32)
        self.assertRegex(key, _HEX32_RE)

    def test_handles_unicode(self) -> None:
        key = make_idempotency_key("usér", "æction", "pay☃load")
        self.assertEqual(len(key), 32)
        self.assertRegex(key, _HEX32_RE)
        # And is still deterministic across calls.
        self.assertEqual(
            key, make_idempotency_key("usér", "æction", "pay☃load")
        )

    def test_separator_prevents_boundary_collision(self) -> None:
        # Without a non-printable separator, ("ab", "c") and
        # ("a", "bc") could hash to the same key. U+001F prevents that.
        self.assertNotEqual(
            make_idempotency_key("ab", "c"),
            make_idempotency_key("a", "bc"),
        )

    def test_zero_parts(self) -> None:
        # Edge case: no parts is a valid (if unusual) input — joining
        # the empty list yields the empty string, which still hashes.
        key = make_idempotency_key()
        self.assertEqual(len(key), 32)
        self.assertRegex(key, _HEX32_RE)

    def test_single_part(self) -> None:
        key = make_idempotency_key("only")
        self.assertEqual(len(key), 32)
        self.assertRegex(key, _HEX32_RE)


class NewRequestIdTests(unittest.TestCase):
    def test_length_is_22(self) -> None:
        self.assertEqual(len(new_request_id()), 22)

    def test_is_urlsafe(self) -> None:
        rid = new_request_id()
        self.assertRegex(rid, _URLSAFE_RE)
        self.assertNotIn("=", rid)
        self.assertNotIn("+", rid)
        self.assertNotIn("/", rid)

    def test_each_call_is_unique(self) -> None:
        ids = {new_request_id() for _ in range(1000)}
        # 128 bits of entropy — collisions at N=1000 are astronomically
        # unlikely; if this ever fails we have a real RNG bug.
        self.assertEqual(len(ids), 1000)


class UtcNowMonotonicTests(unittest.TestCase):
    def test_returns_float(self) -> None:
        self.assertIsInstance(utcnow_monotonic(), float)

    def test_is_non_decreasing(self) -> None:
        a = utcnow_monotonic()
        b = utcnow_monotonic()
        self.assertGreaterEqual(b, a)

    def test_advances_across_small_sleep(self) -> None:
        a = utcnow_monotonic()
        time.sleep(0.001)
        b = utcnow_monotonic()
        self.assertGreater(b, a)


if __name__ == "__main__":
    unittest.main()
