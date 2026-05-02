"""Tests for the session-visibility audit-log extensions.

Covers:
  * ``recent_by_actions`` — set-filter + since-timestamp behavior
  * ``iter_since`` — lexical-compare iteration
  * ``head`` — height, last-hash, last-ts, fresh-file case
  * ``audit_actions`` constants module — group memberships + ALL union

The existing audit-log invariants (hash chain, rotation, idempotency)
are covered by ``test_audit_log.py``; these tests only exercise the
new surface.
"""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.core.auth.users import audit_actions  # noqa: E402
from media_stack.core.auth.users.audit_log import AuditLog  # noqa: E402


def _new_log(tmp: str) -> AuditLog:
    return AuditLog(Path(tmp) / "audit.jsonl")


class RecentByActionsTests(unittest.TestCase):

    def test_empty_when_no_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = _new_log(tmp)
            self.assertEqual(
                log.recent_by_actions(audit_actions.AUTH_EVENTS), [],
            )

    def test_empty_when_actions_iterable_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = _new_log(tmp)
            log.append("alice", audit_actions.LOGIN_SUCCESS, "alice@x")
            self.assertEqual(log.recent_by_actions([]), [])

    def test_filters_by_action_set_exact_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = _new_log(tmp)
            log.append("alice", audit_actions.LOGIN_SUCCESS, "alice@x")
            log.append("bob", audit_actions.LOGIN_FAILURE, "bob@x")
            log.append("carol", audit_actions.CREATE_USER, "carol@x")
            results = log.recent_by_actions(audit_actions.AUTH_EVENTS)
            self.assertEqual(len(results), 2)
            actions = {r["action"] for r in results}
            self.assertEqual(actions, {"login_success", "login_failure"})

    def test_exact_match_not_substring(self) -> None:
        # Defense: "login_success_legacy" must not be picked up when
        # the caller asked for "login_success".
        with tempfile.TemporaryDirectory() as tmp:
            log = _new_log(tmp)
            log.append("alice", "login_success", "alice@x")
            log.append("alice", "login_success_legacy", "alice@x")
            results = log.recent_by_actions(["login_success"])
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["action"], "login_success")

    def test_since_filter_lexical_compare(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = _new_log(tmp)
            log.append("alice", audit_actions.LOGIN_SUCCESS, "alice@x")
            time.sleep(1.1)  # ensure timestamp tick
            barrier = log.append(
                "bob", audit_actions.LOGIN_SUCCESS, "bob@x",
            )
            time.sleep(1.1)
            log.append("carol", audit_actions.LOGIN_SUCCESS, "carol@x")
            results = log.recent_by_actions(
                [audit_actions.LOGIN_SUCCESS],
                since=barrier.timestamp,
            )
            actors = [r["actor"] for r in results]
            # barrier included (>= since), alice excluded (<).
            self.assertIn("bob", actors)
            self.assertIn("carol", actors)
            self.assertNotIn("alice", actors)

    def test_limit_returns_most_recent_tail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = _new_log(tmp)
            for i in range(5):
                log.append(f"user{i}", audit_actions.LOGIN_SUCCESS, "x")
            results = log.recent_by_actions(
                [audit_actions.LOGIN_SUCCESS], limit=2,
            )
            actors = [r["actor"] for r in results]
            # Most recent 2 = user3, user4
            self.assertEqual(actors, ["user3", "user4"])

    def test_ignores_unrelated_actions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = _new_log(tmp)
            log.append("alice", audit_actions.CREATE_USER, "alice@x")
            log.append("alice", audit_actions.SET_ROLE, "alice@x")
            results = log.recent_by_actions(audit_actions.AUTH_EVENTS)
            self.assertEqual(results, [])


class IterSinceTests(unittest.TestCase):

    def test_yields_all_when_since_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = _new_log(tmp)
            log.append("alice", audit_actions.LOGIN_SUCCESS, "alice@x")
            log.append("bob", audit_actions.LOGIN_FAILURE, "bob@x")
            out = list(log.iter_since(""))
            self.assertEqual(len(out), 2)

    def test_yields_none_when_file_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = _new_log(tmp)  # never wrote
            self.assertEqual(list(log.iter_since("2026-04-24T00:00:00Z")), [])

    def test_filters_by_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = _new_log(tmp)
            log.append("alice", audit_actions.LOGIN_SUCCESS, "alice@x")
            time.sleep(1.1)
            barrier = log.append(
                "bob", audit_actions.LOGIN_SUCCESS, "bob@x",
            )
            time.sleep(1.1)
            log.append("carol", audit_actions.LOGIN_SUCCESS, "carol@x")
            out = list(log.iter_since(barrier.timestamp))
            actors = [e.actor for e in out]
            self.assertIn("bob", actors)
            self.assertIn("carol", actors)
            self.assertNotIn("alice", actors)


class HeadEndpointTests(unittest.TestCase):

    def test_fresh_file_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = _new_log(tmp)
            head = log.head()
            self.assertEqual(head["height"], 0)
            self.assertEqual(head["hash"], "")
            self.assertEqual(head["ts"], "")
            self.assertTrue(head["ok"])

    def test_after_one_entry_height_is_1(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = _new_log(tmp)
            entry = log.append("alice", audit_actions.LOGIN_SUCCESS, "alice@x")
            head = log.head()
            self.assertEqual(head["height"], 1)
            self.assertEqual(head["hash"], entry.hash)
            self.assertEqual(head["ts"], entry.timestamp)

    def test_after_many_entries_height_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = _new_log(tmp)
            last = None
            for i in range(10):
                last = log.append(
                    f"u{i}", audit_actions.LOGIN_SUCCESS, "x@x",
                )
            head = log.head()
            self.assertEqual(head["height"], 10)
            assert last is not None
            self.assertEqual(head["hash"], last.hash)

    def test_head_hash_advances_on_each_append(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = _new_log(tmp)
            log.append("alice", audit_actions.LOGIN_SUCCESS, "alice@x")
            h1 = log.head()["hash"]
            log.append("bob", audit_actions.LOGIN_SUCCESS, "bob@x")
            h2 = log.head()["hash"]
            self.assertNotEqual(h1, h2)
            self.assertTrue(h1 and h2)


class HeadEndpointColdStartTests(unittest.TestCase):
    """A new AuditLog instance on an existing file must reconstruct
    the chain head without having a warm cache — exercises the
    file-scan path in ``_last_hash`` that ``append`` warm-path
    callers normally skip."""

    def test_cold_start_on_existing_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            # Write some entries via one instance, then instantiate
            # a fresh one and read the head — first call must scan
            # the file to populate the cache.
            log1 = _new_log(tmp)
            log1.append("alice", audit_actions.LOGIN_SUCCESS, "alice@x")
            last_entry = log1.append(
                "bob", audit_actions.LOGIN_SUCCESS, "bob@x",
            )
            # Fresh instance, cold cache.
            log2 = AuditLog(Path(tmp) / "audit.jsonl")
            head = log2.head()
            self.assertEqual(head["height"], 2)
            self.assertEqual(head["hash"], last_entry.hash)

    def test_cold_start_on_file_with_blank_lines(self) -> None:
        # Defense: operator manually edits the log and leaves blank
        # lines. Our parser tolerates them.
        with tempfile.TemporaryDirectory() as tmp:
            log = _new_log(tmp)
            log.append("alice", audit_actions.LOGIN_SUCCESS, "alice@x")
            # Append a raw blank line.
            (Path(tmp) / "audit.jsonl").open("a").write("\n")
            log2 = AuditLog(Path(tmp) / "audit.jsonl")
            head = log2.head()
            # Height counts non-blank valid rows only.
            self.assertEqual(head["height"], 1)

    def test_cold_start_on_file_with_corrupt_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = _new_log(tmp)
            log.append("alice", audit_actions.LOGIN_SUCCESS, "alice@x")
            # Append garbage.
            (Path(tmp) / "audit.jsonl").open("a").write("NOT JSON\n")
            log2 = AuditLog(Path(tmp) / "audit.jsonl")
            head = log2.head()
            self.assertEqual(head["height"], 1)


class AuditActionsGroupTests(unittest.TestCase):

    def test_all_is_union_of_groups(self) -> None:
        expected = (
            audit_actions.ACCOUNT_MGMT
            | audit_actions.AUTH_EVENTS
            | audit_actions.SESSION_MGMT
            | audit_actions.PASSWORD_EVENTS
            | audit_actions.BAN_EVENTS
            | audit_actions.ANOMALY_EVENTS
            | audit_actions.MEDIA_INTEGRITY_EVENTS
        )
        self.assertEqual(audit_actions.ALL, expected)

    def test_groups_are_frozensets(self) -> None:
        for group in (
            audit_actions.ACCOUNT_MGMT,
            audit_actions.AUTH_EVENTS,
            audit_actions.SESSION_MGMT,
            audit_actions.PASSWORD_EVENTS,
            audit_actions.BAN_EVENTS,
            audit_actions.ANOMALY_EVENTS,
            audit_actions.MEDIA_INTEGRITY_EVENTS,
        ):
            self.assertIsInstance(group, frozenset)

    def test_no_action_belongs_to_two_groups(self) -> None:
        # Every action has exactly one home group — invariant keeps
        # the UI filter unambiguous.
        groups = [
            audit_actions.ACCOUNT_MGMT,
            audit_actions.AUTH_EVENTS,
            audit_actions.SESSION_MGMT,
            audit_actions.PASSWORD_EVENTS,
            audit_actions.BAN_EVENTS,
            audit_actions.ANOMALY_EVENTS,
            audit_actions.MEDIA_INTEGRITY_EVENTS,
        ]
        all_action_appearances: list[str] = []
        for group in groups:
            all_action_appearances.extend(group)
        dupes = {
            a for a in all_action_appearances
            if all_action_appearances.count(a) > 1
        }
        self.assertEqual(dupes, set())

    def test_expected_auth_events_present(self) -> None:
        # Session visibility depends on these; regression guard.
        for action in (
            "login_success", "login_failure", "login_blocked",
            "login_rate_limited", "logout",
        ):
            self.assertIn(action, audit_actions.AUTH_EVENTS)

    def test_expected_ban_events_present(self) -> None:
        for action in (
            "ban_user_add", "ban_user_remove",
            "ban_ip_add", "ban_ip_remove",
        ):
            self.assertIn(action, audit_actions.BAN_EVENTS)

    def test_no_action_constant_collides(self) -> None:
        # The string values must be unique across the module.
        values = [
            v for k, v in audit_actions.__dict__.items()
            if k.isupper() and isinstance(v, str)
        ]
        dupes = [v for v in values if values.count(v) > 1]
        self.assertEqual(dupes, [])


if __name__ == "__main__":
    unittest.main()
