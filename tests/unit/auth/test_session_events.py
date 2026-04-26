"""Unit tests for session-visibility domain event dataclasses.

Covers:
  * Each subclass advertises the correct dotted ``event_type``.
  * ``to_dict()`` emits every field with its original type preserved
    (bool stays bool, int stays int) — the audit log relies on that.
  * Instances are immutable (frozen dataclass contract).
  * Minimum-field construction works (defaults fill).
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from media_stack.core.events import (
    BanApplied,
    BanRemoved,
    Event,
    LoginBlocked,
    LoginFailed,
    LoginSucceeded,
    PasswordChanged,
    SessionCreated,
    SessionRevoked,
)


@pytest.mark.parametrize(
    "cls,kwargs,expected_type",
    [
        (
            SessionCreated,
            {"username": "u", "session_id": "s", "provider": "p"},
            "session.created",
        ),
        (
            SessionRevoked,
            {
                "username": "u",
                "session_id": "s",
                "provider": "p",
                "reason": "user",
            },
            "session.revoked",
        ),
        (
            LoginSucceeded,
            {
                "username": "u",
                "provider": "p",
                "client_ip": "1.2.3.4",
                "user_agent": "ua",
                "device_class": "browser",
                "first_seen_ip": True,
                "concurrent_count": 1,
            },
            "login.succeeded",
        ),
        (
            LoginFailed,
            {
                "username": "u",
                "provider": "p",
                "client_ip": "1.2.3.4",
                "user_agent": "ua",
                "reason": "bad_password",
            },
            "login.failed",
        ),
        (
            LoginBlocked,
            {
                "username": "u",
                "client_ip": "1.2.3.4",
                "ban_kind": "user",
                "ban_reason": "abuse",
            },
            "login.blocked",
        ),
        (
            BanApplied,
            {
                "kind": "user",
                "target": "alice",
                "actor": "admin",
                "reason": "policy",
                "expires_at": "2030-01-01T00:00:00+00:00",
            },
            "ban.applied",
        ),
        (
            BanRemoved,
            {"kind": "ip", "target": "10.0.0.1", "actor": "admin"},
            "ban.removed",
        ),
        (
            PasswordChanged,
            {
                "username": "u",
                "actor": "u",
                "self_change": True,
                "provider": "p",
            },
            "password.changed",
        ),
    ],
)
def test_event_type_autoset(cls: type[Event], kwargs: dict, expected_type: str) -> None:
    evt = cls(**kwargs)
    assert evt.event_type == expected_type
    assert isinstance(evt, Event)


def test_to_dict_preserves_types_for_login_succeeded() -> None:
    evt = LoginSucceeded(
        username="alice",
        provider="jellyfin",
        client_ip="10.0.0.5",
        user_agent="Firefox",
        device_class="browser",
        first_seen_ip=True,
        concurrent_count=3,
        request_id="req-42",
    )
    d = evt.to_dict()

    assert d["event_type"] == "login.succeeded"
    assert d["username"] == "alice"
    assert d["provider"] == "jellyfin"
    assert d["client_ip"] == "10.0.0.5"
    assert d["user_agent"] == "Firefox"
    assert d["device_class"] == "browser"
    assert d["first_seen_ip"] is True and isinstance(d["first_seen_ip"], bool)
    assert d["concurrent_count"] == 3 and isinstance(d["concurrent_count"], int)
    assert d["request_id"] == "req-42"
    assert isinstance(d["ts"], str)


def test_to_dict_preserves_types_for_password_changed() -> None:
    evt = PasswordChanged(
        username="alice", actor="admin", self_change=False, provider="jellyfin"
    )
    d = evt.to_dict()
    assert d["self_change"] is False and isinstance(d["self_change"], bool)


def test_to_dict_contains_all_dataclass_fields() -> None:
    evt = SessionCreated(
        username="u",
        session_id="s",
        provider="p",
        device_class="dc",
        client_ip="1.1.1.1",
        user_agent="ua",
    )
    d = evt.to_dict()
    for field_name in (
        "event_type",
        "ts",
        "request_id",
        "username",
        "session_id",
        "provider",
        "device_class",
        "client_ip",
        "user_agent",
    ):
        assert field_name in d


def test_instances_are_immutable() -> None:
    evt = SessionCreated(username="u", session_id="s", provider="p")
    with pytest.raises(FrozenInstanceError):
        evt.username = "other"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        evt.event_type = "tampered"  # type: ignore[misc]


def test_construction_with_minimum_fields_fills_defaults() -> None:
    evt = SessionCreated(username="u", session_id="s", provider="p")
    assert evt.device_class == ""
    assert evt.client_ip == ""
    assert evt.user_agent == ""
    assert evt.request_id == ""
    assert evt.ts  # default_factory ran


def test_explicit_event_type_override_is_respected() -> None:
    """Callers supplying an explicit ``event_type`` (e.g. for test
    fixtures mimicking a legacy tag) are not overridden by the
    ``__post_init__`` backfill. Documents the 'only fill if empty'
    branch."""
    evt = SessionCreated(
        username="u", session_id="s", provider="p", event_type="legacy.session.created"
    )
    assert evt.event_type == "legacy.session.created"


def test_base_event_without_event_type_stays_empty() -> None:
    """``Event`` itself has no ``EVENT_TYPE`` and no caller override
    -> ``event_type`` stays ``""``. This is the branch that guards the
    auto-fill: without it, the base would silently swallow a missing
    sentinel on a buggy subclass."""
    evt = Event()
    assert evt.event_type == ""


def test_ban_applied_optional_permanent_expiry() -> None:
    evt = BanApplied(
        kind="user", target="alice", actor="admin", reason="policy", expires_at=""
    )
    assert evt.expires_at == ""
    assert evt.event_type == "ban.applied"


def test_ban_removed_fields() -> None:
    evt = BanRemoved(kind="ip", target="10.0.0.1", actor="admin")
    d = evt.to_dict()
    assert d["kind"] == "ip"
    assert d["target"] == "10.0.0.1"
    assert d["actor"] == "admin"
    assert d["event_type"] == "ban.removed"


def test_session_revoked_reason_is_free_string() -> None:
    for reason in (
        "user",
        "admin_revoke",
        "idle",
        "absolute",
        "banned",
        "password_changed",
        "replaced",
    ):
        evt = SessionRevoked(
            username="u", session_id="s", provider="p", reason=reason
        )
        assert evt.reason == reason


def test_login_failed_reason_is_free_string() -> None:
    for reason in ("bad_password", "unknown_user", "rate_limited"):
        evt = LoginFailed(
            username="u",
            provider="p",
            client_ip="1.1.1.1",
            user_agent="ua",
            reason=reason,
        )
        assert evt.reason == reason


def test_login_blocked_fields() -> None:
    evt = LoginBlocked(
        username="u", client_ip="1.1.1.1", ban_kind="ip", ban_reason="abuse"
    )
    d = evt.to_dict()
    assert d["ban_kind"] == "ip"
    assert d["ban_reason"] == "abuse"
