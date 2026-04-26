"""Unit tests for :class:`EmailChannel`."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.core.notifications.dispatcher import (
    DeliveryStatus,
    Notification,
)
from media_stack.core.notifications.email_channel import (
    EmailChannel,
    InMemoryEmailSender,
)


def _note(event_type: str = "auth.password_change", **overrides) -> Notification:
    base = dict(
        event_type=event_type,
        title="Password changed",
        body="user=alice changed password from 1.2.3.4",
        severity="warn",
        structured={"user": "alice", "ip": "1.2.3.4"},
        dedupe_key="",
    )
    base.update(overrides)
    return Notification(**base)


class EmailChannelSendTests(unittest.TestCase):
    def test_sender_invoked_with_correct_args(self):
        calls: list[tuple[list[str], str, str]] = []

        def sender(to, subject, body):
            calls.append((list(to), subject, body))

        ch = EmailChannel(
            "ops-email", ["ops@example.com", "oncall@example.com"], sender=sender,
        )
        result = ch.send(_note())
        self.assertEqual(result.status, DeliveryStatus.OK)
        self.assertEqual(result.channel_name, "ops-email")
        self.assertEqual(len(calls), 1)
        to, subject, body = calls[0]
        self.assertEqual(to, ["ops@example.com", "oncall@example.com"])
        self.assertIn("[WARN]", subject)
        self.assertIn("Password changed", subject)
        self.assertIn("user=alice changed password", body)

    def test_subject_prefixes_severity(self):
        sent: list[tuple[list[str], str, str]] = []

        def sender(to, subj, body):
            sent.append((to, subj, body))

        ch = EmailChannel("e", ["a@example.com"], sender=sender)
        ch.send(_note(severity="critical", title="IP banned"))
        self.assertTrue(sent[0][1].startswith("[CRITICAL] "))
        self.assertIn("IP banned", sent[0][1])

    def test_body_includes_event_type_severity_and_structured(self):
        sent: list[tuple[list[str], str, str]] = []

        def sender(to, subj, body):
            sent.append((to, subj, body))

        ch = EmailChannel("e", ["a@example.com"], sender=sender)
        ch.send(
            _note(
                event_type="auth.ban",
                severity="critical",
                structured={"ip": "9.9.9.9", "reason": "brute_force"},
            )
        )
        body = sent[0][2]
        self.assertIn("event_type: auth.ban", body)
        self.assertIn("severity: critical", body)
        self.assertIn("Details:", body)
        self.assertIn("ip: 9.9.9.9", body)
        self.assertIn("reason: brute_force", body)

    def test_body_without_structured_skips_details_section(self):
        sent: list[tuple[list[str], str, str]] = []

        def sender(to, subj, body):
            sent.append((to, subj, body))

        ch = EmailChannel("e", ["a@example.com"], sender=sender)
        n = Notification(
            event_type="auth.ok",
            title="Hello",
            body="Just FYI",
            severity="info",
        )
        ch.send(n)
        body = sent[0][2]
        self.assertIn("Just FYI", body)
        self.assertNotIn("Details:", body)

    def test_sender_raising_yields_undeliverable(self):
        def bad_sender(to, subject, body):
            raise RuntimeError("SMTP auth failed")

        ch = EmailChannel("e", ["a@example.com"], sender=bad_sender)
        result = ch.send(_note())
        self.assertEqual(result.status, DeliveryStatus.UNDELIVERABLE)
        self.assertIn("SMTP auth failed", result.detail)

    def test_recipients_are_defensively_copied(self):
        recipients = ["a@example.com"]
        captured: list[list[str]] = []

        def sender(to, subj, body):
            captured.append(to)

        ch = EmailChannel("e", recipients, sender=sender)
        recipients.append("mutated@example.com")  # after construction
        ch.send(_note())
        # Mutation to the original list does not change what the sender sees.
        self.assertEqual(captured[0], ["a@example.com"])

    def test_default_sender_is_in_memory_stub(self):
        ch = EmailChannel("e", ["a@example.com"])
        result = ch.send(_note())
        self.assertEqual(result.status, DeliveryStatus.OK)
        # The default sender records into its ``sent`` buffer — verify
        # by pulling the internal sender and inspecting it.
        sender = ch._sender  # type: ignore[attr-defined]
        self.assertIsInstance(sender, InMemoryEmailSender)
        self.assertEqual(len(sender.sent), 1)
        to, subject, body = sender.sent[0]
        self.assertEqual(to, ["a@example.com"])
        self.assertIn("Password changed", subject)

    def test_severity_missing_falls_back_to_info(self):
        sent: list[tuple[list[str], str, str]] = []

        def sender(to, subj, body):
            sent.append((to, subj, body))

        ch = EmailChannel("e", ["a@example.com"], sender=sender)
        # Severity is required on Notification, but an empty string
        # should not crash the subject builder.
        n = Notification(
            event_type="x", title="T", body="B", severity="",
        )
        ch.send(n)
        self.assertTrue(sent[0][1].startswith("[INFO] "))


class EmailChannelAcceptsTests(unittest.TestCase):
    def test_accepts_everything_when_filter_is_none(self):
        ch = EmailChannel("e", ["a@example.com"])
        self.assertTrue(ch.accepts("auth.ban"))
        self.assertTrue(ch.accepts("anything.else"))

    def test_accepts_filters_by_event_types(self):
        ch = EmailChannel(
            "e",
            ["a@example.com"],
            event_types=frozenset({"auth.ban"}),
        )
        self.assertTrue(ch.accepts("auth.ban"))
        self.assertFalse(ch.accepts("auth.new_location"))


class InMemoryEmailSenderTests(unittest.TestCase):
    def test_records_messages_with_defensive_copy(self):
        s = InMemoryEmailSender()
        recipients = ["a@example.com"]
        s(recipients, "subj", "body")
        recipients.append("b@example.com")
        # Append must not mutate the recorded row.
        self.assertEqual(s.sent, [(["a@example.com"], "subj", "body")])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
