"""Unit tests for :class:`PasswordTicketStore`.

Covers:
  - mint returns a MintedTicket with a non-empty ticket + ISO expiry.
  - consume returns the plaintext exactly once, None on the second try.
  - expiry-based eviction on consume path (ttl_seconds=0 rejected,
    so we fake time via monkeypatched ``time.time``).
  - per-user uniqueness: minting a second ticket for the same user_id
    evicts the prior one so two plaintexts can never be live at once.
  - live_count tracks evictions.
  - thread-safety: a 50-thread stress run never loses a plaintext.

The store is in-process and takes no disk locks, so test isolation
is trivial — every test builds a fresh store.
"""

from __future__ import annotations

import sys
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.core.auth.users.password_ticket_store import (  # noqa: E402
    MintedTicket,
    PasswordTicketStore,
    get_default_store,
)


class PasswordTicketStoreMintTests(unittest.TestCase):

    def test_mint_returns_ticket_and_iso_expiry(self) -> None:
        store = PasswordTicketStore(ttl_seconds=120)
        minted = store.mint(user_id="u1", plaintext="hunter2")
        self.assertIsInstance(minted, MintedTicket)
        self.assertTrue(minted.ticket)
        self.assertGreaterEqual(len(minted.ticket), 20)
        # ISO-8601 UTC stamp with trailing Z.
        self.assertRegex(
            minted.expires_at_iso,
            r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$",
        )

    def test_mint_rejects_empty_user_id(self) -> None:
        store = PasswordTicketStore()
        with self.assertRaises(ValueError):
            store.mint(user_id="", plaintext="x")

    def test_mint_rejects_empty_plaintext(self) -> None:
        store = PasswordTicketStore()
        with self.assertRaises(ValueError):
            store.mint(user_id="u1", plaintext="")

    def test_ttl_zero_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            PasswordTicketStore(ttl_seconds=0)
        with self.assertRaises(ValueError):
            PasswordTicketStore(ttl_seconds=-5)


class PasswordTicketStoreConsumeTests(unittest.TestCase):

    def test_consume_returns_plaintext_once(self) -> None:
        store = PasswordTicketStore()
        minted = store.mint(user_id="u1", plaintext="hunter2")
        self.assertEqual(store.consume(minted.ticket), "hunter2")
        # Second consume returns None — single-use semantics.
        self.assertIsNone(store.consume(minted.ticket))

    def test_consume_unknown_ticket_returns_none(self) -> None:
        store = PasswordTicketStore()
        self.assertIsNone(store.consume("not-a-real-ticket"))
        self.assertIsNone(store.consume(""))

    def test_consume_after_expiry_returns_none(self) -> None:
        store = PasswordTicketStore(ttl_seconds=60)
        # Freeze "now" via a patched time.time to simulate expiry.
        t0 = time.time()
        original = time.time
        try:
            time.time = lambda: t0  # type: ignore[assignment]
            minted = store.mint(user_id="u1", plaintext="hunter2")
            # Jump past the TTL.
            time.time = lambda: t0 + 61  # type: ignore[assignment]
            self.assertIsNone(store.consume(minted.ticket))
        finally:
            time.time = original  # type: ignore[assignment]


class PasswordTicketStorePerUserUniquenessTests(unittest.TestCase):

    def test_remint_evicts_prior_ticket(self) -> None:
        store = PasswordTicketStore()
        first = store.mint(user_id="u1", plaintext="pw1")
        second = store.mint(user_id="u1", plaintext="pw2")
        # Prior ticket is invalid; only the newest survives.
        self.assertIsNone(store.consume(first.ticket))
        self.assertEqual(store.consume(second.ticket), "pw2")

    def test_different_users_keep_independent_tickets(self) -> None:
        store = PasswordTicketStore()
        a = store.mint(user_id="u1", plaintext="pw-alice")
        b = store.mint(user_id="u2", plaintext="pw-bob")
        # Both remain consumable.
        self.assertEqual(store.consume(a.ticket), "pw-alice")
        self.assertEqual(store.consume(b.ticket), "pw-bob")


class PasswordTicketStorePeekTests(unittest.TestCase):

    def test_peek_returns_bound_user_id(self) -> None:
        store = PasswordTicketStore()
        minted = store.mint(user_id="u42", plaintext="pw")
        self.assertEqual(store.peek_user_id(minted.ticket), "u42")
        # peek does NOT consume.
        self.assertEqual(store.consume(minted.ticket), "pw")

    def test_peek_unknown_returns_none(self) -> None:
        store = PasswordTicketStore()
        self.assertIsNone(store.peek_user_id("missing"))
        self.assertIsNone(store.peek_user_id(""))


class PasswordTicketStoreLiveCountTests(unittest.TestCase):

    def test_live_count_tracks_mints_and_consumes(self) -> None:
        store = PasswordTicketStore()
        self.assertEqual(store.live_count(), 0)
        m1 = store.mint(user_id="u1", plaintext="x")
        m2 = store.mint(user_id="u2", plaintext="y")
        self.assertEqual(store.live_count(), 2)
        store.consume(m1.ticket)
        self.assertEqual(store.live_count(), 1)
        store.consume(m2.ticket)
        self.assertEqual(store.live_count(), 0)

    def test_clear_drops_every_ticket(self) -> None:
        store = PasswordTicketStore()
        m = store.mint(user_id="u1", plaintext="x")
        store.clear()
        self.assertEqual(store.live_count(), 0)
        self.assertIsNone(store.consume(m.ticket))

    def test_live_count_evicts_expired(self) -> None:
        store = PasswordTicketStore(ttl_seconds=60)
        t0 = time.time()
        original = time.time
        try:
            time.time = lambda: t0  # type: ignore[assignment]
            store.mint(user_id="u1", plaintext="x")
            self.assertEqual(store.live_count(), 1)
            time.time = lambda: t0 + 61  # type: ignore[assignment]
            # First live_count call with advanced clock runs the
            # expiry sweep and returns 0.
            self.assertEqual(store.live_count(), 0)
        finally:
            time.time = original  # type: ignore[assignment]


class PasswordTicketStoreThreadSafetyTests(unittest.TestCase):

    def test_concurrent_mint_consume_no_lost_plaintext(self) -> None:
        store = PasswordTicketStore()
        n = 50
        results: dict[int, str | None] = {}
        barrier = threading.Barrier(n)

        def _worker(i: int) -> None:
            barrier.wait()
            m = store.mint(user_id=f"u{i}", plaintext=f"pw-{i}")
            results[i] = store.consume(m.ticket)

        threads = [
            threading.Thread(target=_worker, args=(i,)) for i in range(n)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)
        for i in range(n):
            self.assertEqual(results.get(i), f"pw-{i}", f"lost ticket {i}")


class GetDefaultStoreTests(unittest.TestCase):

    def test_returns_singleton_instance(self) -> None:
        a = get_default_store()
        b = get_default_store()
        self.assertIs(a, b)

    def test_singleton_ttl_defaults_to_120s(self) -> None:
        self.assertEqual(get_default_store().ttl_seconds, 120)


if __name__ == "__main__":
    unittest.main()
