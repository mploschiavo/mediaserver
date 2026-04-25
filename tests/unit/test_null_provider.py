"""Unit tests for NullProvider.

Every protocol method is exercised. Reads return safely-empty
results, destructive methods are idempotent no-ops, and
state-creating methods raise loudly.
"""

from __future__ import annotations

import unittest

from media_stack.core.auth.users.ip_deny import IPDeny, IPDenyProvider
from media_stack.core.auth.users.null_provider import (
    NullProvider,
    NullProviderError,
)
from media_stack.core.auth.users.provider import ProviderHealth
from media_stack.core.auth.users.visibility_protocols import (
    APITokenProvider,
    AccountStateProvider,
    MFAState,
    MFAStateProvider,
    SessionAdminProvider,
)


class NullProviderConstructionTests(unittest.TestCase):

    def test_default_name(self) -> None:
        p = NullProvider()
        self.assertEqual(p.name, "null")

    def test_custom_name(self) -> None:
        p = NullProvider(name="init-only")
        self.assertEqual(p.name, "init-only")

    def test_empty_name_rejected(self) -> None:
        with self.assertRaises(ValueError):
            NullProvider(name="")

    def test_capabilities_are_defaults(self) -> None:
        p = NullProvider()
        caps = p.capabilities
        self.assertFalse(caps.source_of_truth)
        self.assertFalse(caps.supports_groups)
        self.assertFalse(caps.supports_password)
        self.assertFalse(caps.supports_policy)
        self.assertFalse(caps.auto_provisions_on_login)


class NullProviderUserCRUDTests(unittest.TestCase):

    def setUp(self) -> None:
        self.p = NullProvider()

    def test_health_check_reports_not_ok(self) -> None:
        h = self.p.health_check()
        self.assertIsInstance(h, ProviderHealth)
        self.assertFalse(h.ok)
        self.assertIn("null", h.detail)

    def test_list_users_empty(self) -> None:
        self.assertEqual(self.p.list_users(), [])

    def test_create_user_raises(self) -> None:
        with self.assertRaises(NullProviderError):
            self.p.create_user(
                username="alice", email="a@x", display_name="A",
                password="pw", groups=[],
            )

    def test_update_user_raises(self) -> None:
        with self.assertRaises(NullProviderError):
            self.p.update_user("id-1", email="new@x")

    def test_set_password_raises(self) -> None:
        with self.assertRaises(NullProviderError):
            self.p.set_password("id-1", "pw")

    def test_delete_user_idempotent(self) -> None:
        # No exception on a user that doesn't exist.
        self.p.delete_user("whatever")
        self.p.delete_user("whatever")  # twice, to prove idempotence

    def test_last_activity_empty(self) -> None:
        self.assertEqual(self.p.last_activity("id-1"), "")


class NullProviderSessionAdminTests(unittest.TestCase):

    def setUp(self) -> None:
        self.p = NullProvider()

    def test_satisfies_session_admin_protocol(self) -> None:
        self.assertIsInstance(self.p, SessionAdminProvider)

    def test_list_sessions_empty(self) -> None:
        self.assertEqual(self.p.list_sessions("id-1"), [])

    def test_revoke_sessions_noop(self) -> None:
        self.p.revoke_sessions("id-1")
        self.p.revoke_sessions("id-1")  # idempotent

    def test_revoke_session_noop(self) -> None:
        self.p.revoke_session("id-1", "session-xyz")
        self.p.revoke_session("id-1", "unknown")  # unknown -> no-op


class NullProviderAccountStateTests(unittest.TestCase):

    def setUp(self) -> None:
        self.p = NullProvider()

    def test_satisfies_account_state_protocol(self) -> None:
        self.assertIsInstance(self.p, AccountStateProvider)

    def test_disable_enable_are_noop(self) -> None:
        self.p.disable_user("id-1")
        self.p.enable_user("id-1")
        self.p.disable_user("id-1")  # idempotent disable
        self.p.enable_user("id-1")   # idempotent enable

    def test_is_disabled_returns_false(self) -> None:
        self.assertFalse(self.p.is_disabled("id-1"))


class NullProviderMFATests(unittest.TestCase):

    def setUp(self) -> None:
        self.p = NullProvider()

    def test_satisfies_mfa_protocol(self) -> None:
        self.assertIsInstance(self.p, MFAStateProvider)

    def test_mfa_state_is_none(self) -> None:
        state = self.p.mfa_state("id-1")
        self.assertIsInstance(state, MFAState)
        self.assertFalse(state.enrolled)
        self.assertEqual(state.enrolled_methods, ())


class NullProviderAPITokenTests(unittest.TestCase):

    def setUp(self) -> None:
        self.p = NullProvider()

    def test_satisfies_api_token_protocol(self) -> None:
        self.assertIsInstance(self.p, APITokenProvider)

    def test_list_api_tokens_empty(self) -> None:
        self.assertEqual(self.p.list_api_tokens("id-1"), [])

    def test_revoke_api_token_noop(self) -> None:
        self.p.revoke_api_token("id-1", "tk-1")
        self.p.revoke_api_token("id-1", "nonexistent")


class NullProviderIPDenyTests(unittest.TestCase):

    def setUp(self) -> None:
        self.p = NullProvider()

    def test_satisfies_ip_deny_protocol(self) -> None:
        self.assertIsInstance(self.p, IPDenyProvider)

    def test_list_ip_denies_empty(self) -> None:
        self.assertEqual(self.p.list_ip_denies(), [])

    def test_add_and_remove_noop(self) -> None:
        rule = IPDeny(cidr="10.0.0.0/8", reason="test")
        self.p.add_ip_deny(rule)
        self.p.remove_ip_deny("10.0.0.0/8")
        # Both are no-ops; list stays empty.
        self.assertEqual(self.p.list_ip_denies(), [])


class NullProviderErrorShapeTests(unittest.TestCase):

    def test_inherits_runtime_error(self) -> None:
        with self.assertRaises(RuntimeError):
            raise NullProviderError("boom")


if __name__ == "__main__":
    unittest.main()
