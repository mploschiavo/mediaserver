"""Tests for the persistent BanStore."""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.core.auth.users.ban_store import (  # noqa: E402
    BanReason,
    BanStore,
    BanStoreError,
    BanStoreProtocol,
    IPBanRecord,
    UserBan,
)


def _user_ban(username="alice", key="", expires_at="", reason=BanReason.POLICY_VIOLATION):
    return UserBan(
        username=username,
        reason=reason,
        reason_detail="",
        actor="admin",
        banned_at="2026-04-24T00:00:00Z",
        expires_at=expires_at,
        idempotency_key=key,
    )


def _ip_ban(cidr="203.0.113.5", key="", expires_at="", reason=BanReason.SECURITY_INCIDENT):
    return IPBanRecord(
        cidr=cidr,
        reason=reason,
        reason_detail="",
        actor="admin",
        banned_at="2026-04-24T00:00:00Z",
        expires_at=expires_at,
        idempotency_key=key,
    )


class BanReasonTests(unittest.TestCase):
    def test_label_covers_every_value(self):
        for r in BanReason:
            self.assertIsInstance(r.label, str)
            self.assertTrue(r.label)

    def test_specific_labels(self):
        self.assertEqual(BanReason.CREDENTIAL_STUFFING.label, "Credential stuffing")
        self.assertEqual(BanReason.OTHER.label, "Other")


class BanStoreBasicsTests(unittest.TestCase):
    def test_fresh_file_written_on_first_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bans.json"
            store = BanStore(path)
            self.assertEqual(store.schema_version(), 1)
            self.assertTrue(path.is_file())
            data = json.loads(path.read_text())
            self.assertEqual(data["schema"], 1)
            self.assertEqual(data["user_bans"], [])
            self.assertEqual(data["ip_bans"], [])

    def test_add_and_list_user_ban(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = BanStore(Path(tmp) / "bans.json")
            b = _user_ban()
            store.add_user_ban(b)
            self.assertEqual([x.username for x in store.list_user_bans()], ["alice"])

    def test_add_and_list_ip_ban(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = BanStore(Path(tmp) / "bans.json")
            store.add_ip_ban(_ip_ban())
            got = store.list_ip_bans()
            self.assertEqual(got[0].cidr, "203.0.113.5/32")

    def test_ip_ban_normalises_cidr(self):
        b = _ip_ban(cidr="10.0.0.0/24")
        self.assertEqual(b.cidr, "10.0.0.0/24")
        b2 = _ip_ban(cidr="2001:db8::1")
        self.assertEqual(b2.cidr, "2001:db8::1/128")

    def test_protocol_conformance(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = BanStore(Path(tmp) / "bans.json")
            self.assertIsInstance(store, BanStoreProtocol)


class IdempotencyTests(unittest.TestCase):
    def test_duplicate_user_add_with_key_returns_existing(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = BanStore(Path(tmp) / "bans.json")
            first = store.add_user_ban(_user_ban(key="k1"))
            second = store.add_user_ban(_user_ban(username="bob", key="k1"))
            # second add matches by key — returns the original record
            self.assertEqual(second.username, first.username)
            self.assertEqual(len(store.list_user_bans()), 1)

    def test_duplicate_user_add_without_key_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = BanStore(Path(tmp) / "bans.json")
            store.add_user_ban(_user_ban())
            with self.assertRaises(BanStoreError):
                store.add_user_ban(_user_ban())

    def test_duplicate_ip_add_with_key_returns_existing(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = BanStore(Path(tmp) / "bans.json")
            first = store.add_ip_ban(_ip_ban(key="k-ip"))
            second = store.add_ip_ban(_ip_ban(cidr="10.0.0.1", key="k-ip"))
            self.assertEqual(first.cidr, second.cidr)
            self.assertEqual(len(store.list_ip_bans()), 1)

    def test_duplicate_ip_add_without_key_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = BanStore(Path(tmp) / "bans.json")
            store.add_ip_ban(_ip_ban())
            with self.assertRaises(BanStoreError):
                store.add_ip_ban(_ip_ban())


class RemovalTests(unittest.TestCase):
    def test_remove_returns_record_then_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = BanStore(Path(tmp) / "bans.json")
            store.add_user_ban(_user_ban())
            first = store.remove_user_ban("alice")
            self.assertIsNotNone(first)
            self.assertEqual(first.username, "alice")
            self.assertIsNone(store.remove_user_ban("alice"))

    def test_remove_ip_normalises_cidr(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = BanStore(Path(tmp) / "bans.json")
            store.add_ip_ban(_ip_ban(cidr="10.0.0.5"))
            # caller passes bare address — store normalises to /32
            removed = store.remove_ip_ban("10.0.0.5")
            self.assertIsNotNone(removed)
            self.assertIsNone(store.remove_ip_ban("10.0.0.5"))

    def test_remove_ip_invalid_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = BanStore(Path(tmp) / "bans.json")
            self.assertIsNone(store.remove_ip_ban("not-an-ip"))


class ExpiryTests(unittest.TestCase):
    def test_user_expiry_returns_false_when_past(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = BanStore(Path(tmp) / "bans.json")
            store.add_user_ban(_user_ban(expires_at="2026-01-01T00:00:00Z"))
            self.assertFalse(store.is_user_banned("alice", now_iso="2026-04-24T00:00:00Z"))
            self.assertTrue(store.is_user_banned("alice", now_iso="2025-12-01T00:00:00Z"))

    def test_indefinite_user_ban_stays_true(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = BanStore(Path(tmp) / "bans.json")
            store.add_user_ban(_user_ban(expires_at=""))
            self.assertTrue(store.is_user_banned("alice", now_iso="9999-01-01T00:00:00Z"))

    def test_unknown_user_not_banned(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = BanStore(Path(tmp) / "bans.json")
            self.assertFalse(store.is_user_banned("ghost", now_iso="2026-04-24T00:00:00Z"))

    def test_prune_expired_removes_and_returns(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = BanStore(Path(tmp) / "bans.json")
            store.add_user_ban(_user_ban(username="alice", expires_at="2026-01-01T00:00:00Z"))
            store.add_user_ban(_user_ban(username="bob", expires_at=""))
            store.add_ip_ban(_ip_ban(cidr="10.0.0.1", expires_at="2026-01-01T00:00:00Z"))
            store.add_ip_ban(_ip_ban(cidr="10.0.0.2", expires_at=""))
            users, ips = store.prune_expired("2026-04-24T00:00:00Z")
            self.assertEqual([u.username for u in users], ["alice"])
            self.assertEqual([i.cidr for i in ips], ["10.0.0.1/32"])
            self.assertEqual([u.username for u in store.list_user_bans()], ["bob"])
            self.assertEqual([i.cidr for i in store.list_ip_bans()], ["10.0.0.2/32"])

    def test_prune_expired_noop_when_nothing_expired(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = BanStore(Path(tmp) / "bans.json")
            store.add_user_ban(_user_ban(expires_at=""))
            users, ips = store.prune_expired("2026-04-24T00:00:00Z")
            self.assertEqual(users, [])
            self.assertEqual(ips, [])


class IPMatchingTests(unittest.TestCase):
    def test_slash32_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = BanStore(Path(tmp) / "bans.json")
            store.add_ip_ban(_ip_ban(cidr="203.0.113.5"))
            self.assertTrue(store.is_ip_banned("203.0.113.5", now_iso="2026-04-24T00:00:00Z"))
            self.assertFalse(store.is_ip_banned("203.0.113.6", now_iso="2026-04-24T00:00:00Z"))

    def test_cidr_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = BanStore(Path(tmp) / "bans.json")
            store.add_ip_ban(_ip_ban(cidr="10.0.0.0/24"))
            self.assertTrue(store.is_ip_banned("10.0.0.5", now_iso="2026-04-24T00:00:00Z"))
            self.assertTrue(store.is_ip_banned("10.0.0.254", now_iso="2026-04-24T00:00:00Z"))
            self.assertFalse(store.is_ip_banned("10.0.1.5", now_iso="2026-04-24T00:00:00Z"))

    def test_ipv6_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = BanStore(Path(tmp) / "bans.json")
            store.add_ip_ban(_ip_ban(cidr="2001:db8::/64"))
            self.assertTrue(store.is_ip_banned("2001:db8::1", now_iso="2026-04-24T00:00:00Z"))
            self.assertFalse(store.is_ip_banned("2001:db9::1", now_iso="2026-04-24T00:00:00Z"))

    def test_ipv4_address_never_matches_ipv6_cidr(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = BanStore(Path(tmp) / "bans.json")
            store.add_ip_ban(_ip_ban(cidr="2001:db8::/64"))
            self.assertFalse(store.is_ip_banned("10.0.0.1", now_iso="2026-04-24T00:00:00Z"))

    def test_malformed_ip_returns_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = BanStore(Path(tmp) / "bans.json")
            store.add_ip_ban(_ip_ban())
            self.assertFalse(store.is_ip_banned("not-an-ip", now_iso="2026-04-24T00:00:00Z"))
            self.assertFalse(store.is_ip_banned("", now_iso="2026-04-24T00:00:00Z"))

    def test_expired_ip_not_matched(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = BanStore(Path(tmp) / "bans.json")
            store.add_ip_ban(_ip_ban(expires_at="2026-01-01T00:00:00Z"))
            self.assertFalse(store.is_ip_banned("203.0.113.5", now_iso="2026-04-24T00:00:00Z"))
            self.assertTrue(store.is_ip_banned("203.0.113.5", now_iso="2025-12-01T00:00:00Z"))


class PersistenceTests(unittest.TestCase):
    def test_round_trip_across_instances(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bans.json"
            store1 = BanStore(path)
            store1.add_user_ban(_user_ban())
            store1.add_ip_ban(_ip_ban())
            store2 = BanStore(path)
            self.assertEqual([u.username for u in store2.list_user_bans()], ["alice"])
            self.assertEqual([i.cidr for i in store2.list_ip_bans()], ["203.0.113.5/32"])
            self.assertEqual(store2.schema_version(), 1)

    def test_schema_version_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bans.json"
            path.write_text(json.dumps({"schema": 1, "user_bans": [], "ip_bans": []}))
            store = BanStore(path)
            self.assertEqual(store.schema_version(), 1)
            store.add_user_ban(_user_ban())
            data = json.loads(path.read_text())
            self.assertEqual(data["schema"], 1)

    def test_malformed_json_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bans.json"
            path.write_text("{not json")
            store = BanStore(path)
            with self.assertRaises(BanStoreError):
                store.list_user_bans()

    def test_atomic_write_crash_leaves_original_intact(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bans.json"
            store = BanStore(path)
            store.add_user_ban(_user_ban(username="seed"))
            original_bytes = path.read_bytes()
            with mock.patch(
                "media_stack.core.auth.users.safe_json_edit.os.replace",
                side_effect=OSError("boom"),
            ):
                with self.assertRaises(BanStoreError):
                    store.add_user_ban(_user_ban(username="alice"))
            self.assertEqual(path.read_bytes(), original_bytes)
            leftovers = [
                p
                for p in path.parent.iterdir()
                if p.name.startswith(path.name + ".") and p.name.endswith(".tmp")
            ]
            self.assertEqual(leftovers, [])


class ThreadSafetyTests(unittest.TestCase):
    def test_concurrent_adds_all_persist_no_dupes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bans.json"
            store = BanStore(path)

            errors: list[BaseException] = []

            def _worker(thread_idx: int):
                try:
                    for i in range(100):
                        store.add_user_ban(
                            _user_ban(username=f"u-{thread_idx}-{i}")
                        )
                except BaseException as exc:  # noqa: BLE001
                    errors.append(exc)

            threads = [threading.Thread(target=_worker, args=(t,)) for t in range(10)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            self.assertEqual(errors, [])
            bans = store.list_user_bans()
            self.assertEqual(len(bans), 1000)
            self.assertEqual(len({b.username for b in bans}), 1000)

            # File on disk matches in-memory count
            disk = json.loads(path.read_text())
            self.assertEqual(len(disk["user_bans"]), 1000)


if __name__ == "__main__":
    unittest.main()
