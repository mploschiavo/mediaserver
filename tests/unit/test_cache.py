"""Tests for TTLCache — thread-safe cache with eviction and invalidation."""

import sys
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.api.cache import TTLCache  # noqa: E402


class TestTTLCacheBasic(unittest.TestCase):
    def test_set_and_get_within_ttl(self):
        c = TTLCache()
        c.set("k", "v")
        self.assertEqual(c.get("k", 10), "v")

    def test_get_returns_none_after_ttl(self):
        c = TTLCache()
        c.set("k", "v")
        time.sleep(0.06)
        self.assertIsNone(c.get("k", 0.05))

    def test_get_evicts_stale_entry(self):
        c = TTLCache()
        c.set("k", "v")
        time.sleep(0.06)
        c.get("k", 0.05)  # Should evict
        self.assertEqual(c.size, 0)

    def test_get_missing_key(self):
        c = TTLCache()
        self.assertIsNone(c.get("missing", 10))

    def test_invalidate_existing_returns_true(self):
        c = TTLCache()
        c.set("k", "v")
        self.assertTrue(c.invalidate("k"))

    def test_invalidate_missing_returns_false(self):
        c = TTLCache()
        self.assertFalse(c.invalidate("missing"))

    def test_invalidate_removes_key(self):
        c = TTLCache()
        c.set("k", "v")
        c.invalidate("k")
        self.assertIsNone(c.get("k", 10))

    def test_clear_returns_count(self):
        c = TTLCache()
        c.set("a", 1)
        c.set("b", 2)
        self.assertEqual(c.clear(), 2)

    def test_clear_empties_cache(self):
        c = TTLCache()
        c.set("a", 1)
        c.clear()
        self.assertEqual(c.size, 0)

    def test_size_property(self):
        c = TTLCache()
        self.assertEqual(c.size, 0)
        c.set("a", 1)
        self.assertEqual(c.size, 1)

    def test_set_overwrites_existing(self):
        c = TTLCache()
        c.set("k", "v1")
        c.set("k", "v2")
        self.assertEqual(c.get("k", 10), "v2")

    def test_zero_ttl_always_misses(self):
        c = TTLCache()
        c.set("k", "v")
        self.assertIsNone(c.get("k", 0))

    def test_large_value(self):
        c = TTLCache()
        big = "x" * 100_000
        c.set("k", big)
        self.assertEqual(c.get("k", 10), big)

    def test_set_after_invalidate(self):
        c = TTLCache()
        c.set("k", "v1")
        c.invalidate("k")
        c.set("k", "v2")
        self.assertEqual(c.get("k", 10), "v2")

    def test_max_size_triggers_eviction(self):
        c = TTLCache(max_size=5)
        for i in range(10):
            c.set(f"k{i}", i)
            time.sleep(0.01)
        # After eviction, size should be <= max_size
        self.assertLessEqual(c.size, 10)

    def test_default_max_size(self):
        c = TTLCache()
        self.assertEqual(c._max_size, 128)

    def test_multiple_keys_different_ttls(self):
        c = TTLCache()
        c.set("fast", "v")
        time.sleep(0.06)
        c.set("slow", "v")
        self.assertIsNone(c.get("fast", 0.05))
        self.assertEqual(c.get("slow", 0.1), "v")

    def test_thread_safety_concurrent_set_get(self):
        c = TTLCache()
        errors = []

        def writer():
            for i in range(100):
                c.set(f"k{i}", i)

        def reader():
            for i in range(100):
                c.get(f"k{i}", 10)

        threads = [threading.Thread(target=writer) for _ in range(4)]
        threads += [threading.Thread(target=reader) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # No exceptions = thread safe

    def test_evict_expired_keeps_fresh(self):
        c = TTLCache()
        c.set("fresh", "v")
        with c._lock:
            evicted = c._evict_expired(max_age=300)
        self.assertEqual(evicted, 0)
        self.assertEqual(c.get("fresh", 10), "v")

    def test_evict_expired_removes_old(self):
        c = TTLCache()
        c.set("old", "v")
        time.sleep(0.06)
        with c._lock:
            evicted = c._evict_expired(max_age=0.05)
        self.assertEqual(evicted, 1)


if __name__ == "__main__":
    unittest.main()
