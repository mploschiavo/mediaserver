"""Unit tests for :class:`IdempotencyCache`.

Covers every public branch:

* Put + hit inside TTL.
* Miss after TTL expiry (clock-injection lets us skip the real clock).
* LRU eviction once ``max_entries`` is exceeded.
* Per-actor isolation — same key under different actors does not
  collide.
* Thread-safety under a contended read/write mix.
* Registry accessor honours ``set_default`` / ``get_default``.
* Deep-copy semantics on put + get — mutating the returned payload
  does not leak into the cached entry.
"""

from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.core.auth.idempotency_cache import (  # noqa: E402
    IdempotencyCache,
    IdempotencyCacheRegistry,
)


class _FakeClock:
    """Callable returning a mutable monotonic-style epoch."""

    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, secs: float) -> None:
        self.now += secs


class IdempotencyCacheBasicTests(unittest.TestCase):

    def _cache(self, *, max_entries: int = 4,
               ttl: int = 60) -> tuple[IdempotencyCache, _FakeClock]:
        clk = _FakeClock()
        cache = IdempotencyCache(
            max_entries=max_entries, ttl_seconds=ttl, clock=clk,
        )
        return cache, clk

    def test_put_then_get_returns_payload(self) -> None:
        cache, _ = self._cache()
        cache.put("alice", "k-1", {"ok": True, "n": 1})
        out = cache.get("alice", "k-1")
        self.assertEqual(out, {"ok": True, "n": 1})

    def test_miss_after_ttl(self) -> None:
        cache, clk = self._cache(ttl=5)
        cache.put("alice", "k-1", {"x": 1})
        clk.advance(6)
        self.assertIsNone(cache.get("alice", "k-1"))

    def test_hit_just_before_ttl(self) -> None:
        cache, clk = self._cache(ttl=10)
        cache.put("alice", "k-1", {"x": 1})
        clk.advance(9)
        self.assertIsNotNone(cache.get("alice", "k-1"))

    def test_empty_key_is_noop(self) -> None:
        cache, _ = self._cache()
        cache.put("alice", "", {"x": 1})
        self.assertIsNone(cache.get("alice", ""))
        self.assertEqual(cache.size(), 0)

    def test_lru_eviction_on_overflow(self) -> None:
        cache, _ = self._cache(max_entries=3)
        for i in range(4):
            cache.put("a", f"k-{i}", {"i": i})
        # k-0 evicted, k-1..k-3 retained.
        self.assertIsNone(cache.get("a", "k-0"))
        for i in range(1, 4):
            self.assertIsNotNone(cache.get("a", f"k-{i}"))

    def test_lru_touches_most_recently_read(self) -> None:
        cache, _ = self._cache(max_entries=3)
        cache.put("a", "k-1", {"i": 1})
        cache.put("a", "k-2", {"i": 2})
        cache.put("a", "k-3", {"i": 3})
        # Touch k-1 so k-2 becomes the LRU.
        self.assertIsNotNone(cache.get("a", "k-1"))
        cache.put("a", "k-4", {"i": 4})
        self.assertIsNone(cache.get("a", "k-2"))
        self.assertIsNotNone(cache.get("a", "k-1"))

    def test_per_actor_isolation(self) -> None:
        cache, _ = self._cache()
        cache.put("alice", "k-1", {"who": "alice"})
        cache.put("bob", "k-1", {"who": "bob"})
        self.assertEqual(cache.get("alice", "k-1"), {"who": "alice"})
        self.assertEqual(cache.get("bob", "k-1"), {"who": "bob"})

    def test_deep_copy_on_put_and_get(self) -> None:
        cache, _ = self._cache()
        payload = {"nested": {"x": 1}}
        cache.put("alice", "k", payload)
        payload["nested"]["x"] = 999
        got = cache.get("alice", "k")
        self.assertEqual(got["nested"]["x"], 1)
        got["nested"]["x"] = 42
        again = cache.get("alice", "k")
        self.assertEqual(again["nested"]["x"], 1)

    def test_size_and_clear(self) -> None:
        cache, _ = self._cache()
        cache.put("a", "k-1", {})
        cache.put("a", "k-2", {})
        self.assertEqual(cache.size(), 2)
        cache.clear()
        self.assertEqual(cache.size(), 0)

    def test_rejects_non_positive_params(self) -> None:
        with self.assertRaises(ValueError):
            IdempotencyCache(max_entries=0)
        with self.assertRaises(ValueError):
            IdempotencyCache(ttl_seconds=0)

    def test_actor_label_whitespace_normalised(self) -> None:
        cache, _ = self._cache()
        cache.put(" alice ", "k-1", {"v": 1})
        # Read the same logical actor with different whitespace —
        # expect the same bucket.
        self.assertEqual(cache.get("alice", "k-1"), {"v": 1})

    def test_default_actor_collapses_empty_strings(self) -> None:
        cache, _ = self._cache()
        cache.put("", "k-1", {"v": 1})
        cache.put(None, "k-1", {"v": 2})  # type: ignore[arg-type]
        # Both collapsed to "anonymous" — second write overwrites first.
        self.assertEqual(cache.get("anonymous", "k-1"), {"v": 2})


class IdempotencyCacheThreadingTests(unittest.TestCase):

    def test_concurrent_puts_do_not_crash(self) -> None:
        cache = IdempotencyCache(max_entries=64, ttl_seconds=60)

        def worker(i: int) -> None:
            for j in range(50):
                cache.put(f"a-{i}", f"k-{j}", {"i": i, "j": j})
                cache.get(f"a-{i}", f"k-{j}")

        threads = [threading.Thread(target=worker, args=(i,))
                   for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # Cache stayed under the cap despite 400 puts.
        self.assertLessEqual(cache.size(), 64)


class IdempotencyCacheRegistryTests(unittest.TestCase):

    def test_default_cache_is_lazy_and_overridable(self) -> None:
        IdempotencyCacheRegistry.set_default(None)
        first = IdempotencyCacheRegistry.get_default()
        second = IdempotencyCacheRegistry.get_default()
        self.assertIs(first, second)

        custom = IdempotencyCache(max_entries=2, ttl_seconds=30)
        IdempotencyCacheRegistry.set_default(custom)
        self.assertIs(IdempotencyCacheRegistry.get_default(), custom)
        # Clean-up so sibling tests get a fresh default.
        IdempotencyCacheRegistry.set_default(None)


if __name__ == "__main__":
    unittest.main()
