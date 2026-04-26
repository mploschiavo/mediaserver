"""Unit tests for the session-visibility protocol surface.

Covers dataclasses (MFAState, APIToken), their ``to_dict`` shapes,
default constructors, immutability, and the runtime-checkable
``Protocol`` behavior of the four role-protocols.

These tests don't exercise any real backend — they're contract
tests for the type surface itself. Per-impl tests live alongside
each provider (Authelia, Jellyfin, Null).
"""

from __future__ import annotations

import unittest

from media_stack.core.auth.users.provider import ExternalSession
from media_stack.core.auth.users.visibility_protocols import (
    APIToken,
    APITokenProvider,
    AccountStateProvider,
    MFAState,
    MFAStateProvider,
    SessionAdminProvider,
)


# ---------------------------------------------------------------------------
# MFAState
# ---------------------------------------------------------------------------


class MFAStateTests(unittest.TestCase):

    def test_none_factory_is_empty(self) -> None:
        m = MFAState.none()
        self.assertFalse(m.enrolled)
        self.assertEqual(m.enrolled_methods, ())
        self.assertEqual(m.last_used_method, "")
        self.assertEqual(m.last_used_at, "")
        self.assertFalse(m.required)

    def test_frozen(self) -> None:
        m = MFAState(enrolled=True, enrolled_methods=("totp",))
        with self.assertRaises(Exception):
            m.enrolled = False  # type: ignore[misc]

    def test_to_dict_preserves_order(self) -> None:
        m = MFAState(
            enrolled=True,
            enrolled_methods=("webauthn", "totp"),
            last_used_method="webauthn",
            last_used_at="2026-04-24T10:00:00Z",
            required=True,
        )
        d = m.to_dict()
        self.assertEqual(d["enrolled"], True)
        self.assertEqual(d["enrolled_methods"], ["webauthn", "totp"])
        self.assertEqual(d["last_used_method"], "webauthn")
        self.assertEqual(d["last_used_at"], "2026-04-24T10:00:00Z")
        self.assertEqual(d["required"], True)

    def test_to_dict_returns_a_fresh_list_not_the_tuple(self) -> None:
        # Caller may mutate the returned list without corrupting the
        # frozen dataclass.
        m = MFAState(enrolled=True, enrolled_methods=("totp",))
        d = m.to_dict()
        d["enrolled_methods"].append("webauthn")
        self.assertEqual(m.enrolled_methods, ("totp",))

    def test_enrolled_without_methods_is_permitted(self) -> None:
        # Edge case: a backend that knows MFA is on but hasn't
        # listed the methods yet. Should not raise; UI can render
        # "Enabled (method unknown)".
        m = MFAState(enrolled=True)
        self.assertTrue(m.enrolled)
        self.assertEqual(m.enrolled_methods, ())


# ---------------------------------------------------------------------------
# APIToken
# ---------------------------------------------------------------------------


class APITokenTests(unittest.TestCase):

    def test_required_field_only(self) -> None:
        t = APIToken(token_id="tk-123")
        self.assertEqual(t.token_id, "tk-123")
        self.assertEqual(t.name, "")
        self.assertEqual(t.scopes, ())

    def test_frozen(self) -> None:
        t = APIToken(token_id="tk-1")
        with self.assertRaises(Exception):
            t.name = "new"  # type: ignore[misc]

    def test_to_dict_shape(self) -> None:
        t = APIToken(
            token_id="tk-42",
            name="prowlarr-sync",
            created_at="2026-01-01T00:00:00Z",
            last_used_at="2026-04-24T00:00:00Z",
            scopes=("read", "write"),
            created_by="admin",
        )
        d = t.to_dict()
        self.assertEqual(d, {
            "token_id": "tk-42",
            "name": "prowlarr-sync",
            "created_at": "2026-01-01T00:00:00Z",
            "last_used_at": "2026-04-24T00:00:00Z",
            "scopes": ["read", "write"],
            "created_by": "admin",
        })

    def test_no_secret_field(self) -> None:
        # Defense: the struct must not gain a field that could carry
        # a token secret. Freeze the set of fields.
        allowed = {
            "token_id", "name", "created_at", "last_used_at",
            "scopes", "created_by",
        }
        actual = {f.name for f in APIToken.__dataclass_fields__.values()}
        self.assertEqual(
            actual, allowed,
            "APIToken fields changed — review: adding a field that "
            "could carry a secret is a security bug",
        )


# ---------------------------------------------------------------------------
# Protocol runtime-checkability
# ---------------------------------------------------------------------------


class _FullImpl:
    """A stub impl that implements every protocol for the checks below."""

    name = "stub"

    def list_sessions(self, external_id: str) -> list[ExternalSession]:
        return []

    def revoke_sessions(self, external_id: str) -> None:
        pass

    def revoke_session(self, external_id: str, session_id: str) -> None:
        pass

    def disable_user(self, external_id: str) -> None:
        pass

    def enable_user(self, external_id: str) -> None:
        pass

    def is_disabled(self, external_id: str) -> bool:
        return False

    def mfa_state(self, external_id: str) -> MFAState:
        return MFAState.none()

    def list_api_tokens(self, external_id: str) -> list[APIToken]:
        return []

    def revoke_api_token(self, external_id: str, token_id: str) -> None:
        pass


class _PartialImpl:
    """Implements only SessionAdminProvider + AccountStateProvider."""

    name = "partial"

    def list_sessions(self, external_id: str) -> list[ExternalSession]:
        return []

    def revoke_sessions(self, external_id: str) -> None:
        pass

    def revoke_session(self, external_id: str, session_id: str) -> None:
        pass

    def disable_user(self, external_id: str) -> None:
        pass

    def enable_user(self, external_id: str) -> None:
        pass

    def is_disabled(self, external_id: str) -> bool:
        return False


class _Empty:
    name = "empty"


class ProtocolIsInstanceTests(unittest.TestCase):
    """The four protocols are ``runtime_checkable`` so aggregators
    can probe capability via ``isinstance(p, SessionAdminProvider)``
    without relying on duck-typed hasattr chains."""

    def test_full_impl_satisfies_all(self) -> None:
        p = _FullImpl()
        self.assertIsInstance(p, SessionAdminProvider)
        self.assertIsInstance(p, AccountStateProvider)
        self.assertIsInstance(p, MFAStateProvider)
        self.assertIsInstance(p, APITokenProvider)

    def test_partial_impl_satisfies_only_what_it_implements(self) -> None:
        p = _PartialImpl()
        self.assertIsInstance(p, SessionAdminProvider)
        self.assertIsInstance(p, AccountStateProvider)
        self.assertNotIsInstance(p, MFAStateProvider)
        self.assertNotIsInstance(p, APITokenProvider)

    def test_empty_impl_satisfies_none(self) -> None:
        p = _Empty()
        self.assertNotIsInstance(p, SessionAdminProvider)
        self.assertNotIsInstance(p, AccountStateProvider)
        self.assertNotIsInstance(p, MFAStateProvider)
        self.assertNotIsInstance(p, APITokenProvider)


if __name__ == "__main__":
    unittest.main()
