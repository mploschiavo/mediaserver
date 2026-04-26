"""Tests for the token-bucket rate limiter."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.core.auth.rate_limiter import RateLimiter  # noqa: E402


class _FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class RateLimiterTests(unittest.TestCase):
    def test_bucket_starts_at_capacity(self):
        clock = _FakeClock()
        rl = RateLimiter(capacity=3, refill_per_second=1.0, clock=clock)
        for _ in range(3):
            self.assertTrue(rl.allow(client_id="c1"))
        self.assertFalse(rl.allow(client_id="c1"))

    def test_refill_grants_new_tokens(self):
        clock = _FakeClock()
        rl = RateLimiter(capacity=2, refill_per_second=1.0, clock=clock)
        self.assertTrue(rl.allow(client_id="c1"))
        self.assertTrue(rl.allow(client_id="c1"))
        self.assertFalse(rl.allow(client_id="c1"))
        clock.advance(1.5)
        self.assertTrue(rl.allow(client_id="c1"))

    def test_buckets_are_per_client(self):
        rl = RateLimiter(capacity=1, refill_per_second=0.0, clock=_FakeClock())
        self.assertTrue(rl.allow(client_id="c1"))
        self.assertFalse(rl.allow(client_id="c1"))
        self.assertTrue(rl.allow(client_id="c2"))

    def test_named_buckets_are_independent(self):
        rl = RateLimiter(capacity=1, refill_per_second=0.0, clock=_FakeClock())
        self.assertTrue(rl.allow(client_id="c1", bucket="reset"))
        self.assertFalse(rl.allow(client_id="c1", bucket="reset"))
        self.assertTrue(rl.allow(client_id="c1", bucket="create"))

    def test_reset_clears_all_buckets(self):
        rl = RateLimiter(capacity=1, refill_per_second=0.0, clock=_FakeClock())
        rl.allow(client_id="c1")
        self.assertFalse(rl.allow(client_id="c1"))
        rl.reset()
        self.assertTrue(rl.allow(client_id="c1"))


if __name__ == "__main__":
    unittest.main()
