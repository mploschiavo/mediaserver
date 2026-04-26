"""Unit tests for the sudo re-auth gate."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.api import server as srv


class _FakeHeaders:
    def __init__(self, mapping):
        self._m = mapping

    def get(self, name, default=""):
        return self._m.get(name, default)


class _FakeHandler:
    def __init__(self, *, command="POST", auth="", sudo_pw=""):
        self.command = command
        self.headers = _FakeHeaders({
            "Authorization": auth,
            "X-Sudo-Password": sudo_pw,
        })
        self.client_address = ("127.0.0.1", 0)


class _FakeVerifier:
    """Accepts only the pair (admin, correct-pw)."""
    def verify(self, user, pw):
        return user == "admin" and pw == "correct-pw"


class SudoGateTests(unittest.TestCase):
    def setUp(self):
        # Sudo gate is a no-op when STACK_ADMIN_PASSWORD is unset — the
        # "no-auth" mode. These tests exercise the gated path, so we
        # need a configured admin password for the duration.
        self._patch = mock.patch.dict(
            "os.environ",
            {"STACK_ADMIN_PASSWORD": "pw-for-gate-tests"},
            clear=False,
        )
        self._patch.start()
        self.addCleanup(self._patch.stop)
        self.gate = srv._SudoGate()

    def test_non_sensitive_path_always_allowed(self):
        h = _FakeHandler()
        self.assertTrue(self.gate.allows(h, "/api/services"))

    def test_sensitive_path_with_basic_auth_passes(self):
        """Basic auth sends password every request — already re-auth."""
        h = _FakeHandler(auth="Basic YWRtaW46cHc=")
        self.assertTrue(self.gate.allows(h, "/api/rotate-keys"))

    def test_sensitive_path_with_bearer_requires_sudo_header(self):
        h = _FakeHandler(auth="Bearer sometoken")
        self.assertFalse(self.gate.allows(h, "/api/rotate-keys"))

    def test_sensitive_path_with_correct_sudo_password_passes(self):
        with mock.patch.object(srv, "_build_auth_verifier",
                               return_value=_FakeVerifier()):
            h = _FakeHandler(
                auth="Bearer tok", sudo_pw="correct-pw",
            )
            self.assertTrue(self.gate.allows(h, "/api/rotate-keys"))

    def test_sensitive_path_with_wrong_sudo_password_fails(self):
        with mock.patch.object(srv, "_build_auth_verifier",
                               return_value=_FakeVerifier()):
            h = _FakeHandler(
                auth="Bearer tok", sudo_pw="wrong-pw",
            )
            self.assertFalse(self.gate.allows(h, "/api/rotate-keys"))

    def test_user_reset_password_triggers_sudo(self):
        h = _FakeHandler(auth="Bearer tok")
        self.assertFalse(
            self.gate.allows(h, "/api/users/u1/reset-password"),
        )

    def test_user_delete_triggers_sudo(self):
        h = _FakeHandler(auth="Bearer tok")
        self.assertFalse(
            self.gate.allows(h, "/api/users/u1/delete"),
        )

    def test_list_users_not_sensitive(self):
        """GET /api/users is a read; list doesn't trigger sudo."""
        h = _FakeHandler(auth="Bearer tok")
        self.assertTrue(self.gate.allows(h, "/api/users"))

    def test_extra_paths_from_env_extend_list(self):
        with mock.patch.dict(
            "os.environ",
            {"CONTROLLER_SUDO_EXTRA_PATHS": "/api/extra-nuke,/api/other"},
            clear=False,
        ):
            gate = srv._SudoGate()
            h = _FakeHandler(auth="Bearer tok")
            self.assertFalse(gate.allows(h, "/api/extra-nuke"))

    def test_get_request_never_triggers_sudo(self):
        h = _FakeHandler(command="GET", auth="Bearer tok")
        self.assertTrue(self.gate.allows(h, "/api/rotate-keys"))


if __name__ == "__main__":
    unittest.main()
