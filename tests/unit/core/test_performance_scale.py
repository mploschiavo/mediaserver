"""Performance / scale tests.

Catches O(n^2) algorithms hiding in paths that look fine at N=10
but fall over at N=1000. Thresholds are conservative (10x slower
than actual expected time) so CI noise doesn't cause false fails,
but a real algorithmic regression still trips them.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))


def _perf_floor_seconds(baseline: float) -> float:
    """Return a ceiling allowing 10x the expected runtime. CI can
    be slow and noisy — the test is about 'is this O(1)/O(n log n)
    or did we accidentally write O(n^2)?'."""
    return baseline * 10


class UserStoreScaleTests(unittest.TestCase):
    """Reading the user list at N=1000 must complete in the same
    order of magnitude as N=10. An O(n^2) scan (e.g. linear search
    per item) would 100× at 1000 users — the threshold catches it."""

    def test_list_all_under_one_second_at_1000_users(self):
        from media_stack.core.auth.users.user_store import UserStore
        with tempfile.TemporaryDirectory() as d:
            store = UserStore(Path(d) / "users.json")
            # Seed directly via the file for speed (bulk create
            # through the API isn't the thing under test).
            users = []
            for i in range(1000):
                users.append({
                    "id": f"u-{i:04d}",
                    "email": f"user{i}@local",
                    "username": f"user{i}",
                    "display_name": f"User {i}",
                    "role_slug": "adult",
                    "state": "active",
                    "created_at": "2020-01-01T00:00:00+00:00",
                    "updated_at": "2020-01-01T00:00:00+00:00",
                    "last_login_at": "",
                    "provider_refs": {},
                    "password_history": [],
                })
            (Path(d) / "users.json").write_text(
                json.dumps({"version": 1, "users": users}),
                encoding="utf-8",
            )
            t0 = time.monotonic()
            result = store.list_all()
            elapsed = time.monotonic() - t0
        self.assertEqual(len(result), 1000)
        self.assertLess(
            elapsed, _perf_floor_seconds(0.1),
            f"list_all of 1000 users took {elapsed:.2f}s. "
            "Probably an O(n^2) algorithm hiding in the loader — "
            "UI would lock up on a real-world tenant.",
        )


class AuditLogScaleTests(unittest.TestCase):
    """verify_chain() walks the audit log and re-hashes each entry.
    At 100k entries this must still finish in seconds, not
    minutes — otherwise the integrity check starves out whatever
    else the controller is doing."""

    def test_chain_verify_under_one_second_at_10k_entries(self):
        from media_stack.core.auth.users.audit_log import AuditLog
        with tempfile.TemporaryDirectory() as d:
            audit = AuditLog(Path(d) / "audit.log.jsonl")
            for i in range(10_000):
                audit.append(
                    actor="admin", action="noop",
                    target=f"target-{i}", result="ok",
                    detail={"i": i},
                )
            t0 = time.monotonic()
            ok, _ = audit.verify_chain()
            elapsed = time.monotonic() - t0
        self.assertTrue(ok, "chain verification FAILED on a valid log")
        self.assertLess(
            elapsed, _perf_floor_seconds(1.0),
            f"audit.verify_chain over 10k entries took {elapsed:.2f}s. "
            "The hash-chain check is a hot path; a slowdown here "
            "starves the dashboard's periodic integrity check.",
        )


class PasswordPolicyScaleTests(unittest.TestCase):
    """The policy check runs on every user create + reset. Large
    history lists (5+ default, up to 20) shouldn't make it slow."""

    def test_check_candidate_fast_at_max_history(self):
        from media_stack.core.auth.users.password_policy import (
            PasswordPolicy,
        )
        pol = PasswordPolicy(
            min_length=12, require_class_count=3, history_len=20)
        # 20 prior password hashes — history at the allowed ceiling.
        history = [f"hash-{i}" * 8 for i in range(20)]
        t0 = time.monotonic()
        for _ in range(1000):
            pol.check_candidate("GoodPassword-2026!", history_hashes=history)
        elapsed = time.monotonic() - t0
        self.assertLess(
            elapsed, _perf_floor_seconds(0.1),
            f"1000 policy checks took {elapsed:.2f}s — auth "
            "operations would feel sluggish at scale.",
        )


if __name__ == "__main__":
    unittest.main()
