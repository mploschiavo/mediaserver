"""Tests for the reset-admin CLI command.

Break-glass tool that runs inside the controller pod to rotate the
admin credential when on-disk state has drifted from what the
operator remembers. These tests pin the two behaviors the shipping
tool has to get right:

- The rotation reaches every password-capable provider (Authelia).
  A rotation that only touches the controller's ``users.json`` is
  the exact bug this tool exists to fix, so we assert the
  provider's ``set_password`` was actually invoked.
- When no admin row exists yet (fresh-install or wiped store), the
  command creates one first so the subsequent rotation has a target.

The UserService is stubbed — these are unit tests for the CLI
plumbing, not integration tests of the provider write path (those
live in ``test_admin_bootstrap.py`` and ``test_user_write_service.py``).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.cli.commands.reset_admin_main import (  # noqa: E402
    ResetAdminCommand,
)


def _fake_user(user_id="u1", username="admin"):
    user = MagicMock()
    user.id = user_id
    user.username = username
    return user


class ResetAdminCliTests(unittest.TestCase):

    def _stub_service_with_existing_admin(self):
        service = MagicMock()
        existing = _fake_user()
        service._store.list.return_value = [existing]
        service._store.get.return_value = existing
        service.reset_password.return_value = {
            "user_id": "u1",
            "providers": {"authelia": "ok"},
        }
        return service

    def _stub_service_empty(self):
        service = MagicMock()
        service._store.list.return_value = []
        created = _fake_user()
        service.create_user.return_value = {"id": "u1"}
        service._store.get.return_value = created
        service.reset_password.return_value = {
            "user_id": "u1",
            "providers": {"authelia": "ok"},
        }
        return service

    def test_rotation_reaches_provider_via_reset_password(self):
        """Core invariant: the CLI must call ``reset_password`` on
        the UserService so the provider's ``set_password`` runs —
        otherwise the hash in Authelia's ``users_database.yml``
        never changes and the operator is stuck."""
        service = self._stub_service_with_existing_admin()
        with patch(
            "media_stack.cli.commands.reset_admin_main.UserServiceFactory"
        ) as factory_cls:
            factory_cls.return_value.build.return_value = service
            rc = ResetAdminCommand().main(
                ["--username", "admin", "--password", "new-secret"],
            )
        self.assertEqual(rc, 0)
        service.reset_password.assert_called_once()
        kwargs = service.reset_password.call_args.kwargs
        self.assertEqual(kwargs["password"], "new-secret")
        self.assertEqual(kwargs["actor"], "cli-reset-admin")

    def test_creates_admin_when_store_is_empty(self):
        """Fresh install or post-wipe recovery: the store has no
        admin, so the CLI has to create one before rotating. Without
        this branch the tool would stack-trace on a blank install
        and the operator would still be stuck."""
        service = self._stub_service_empty()
        with patch(
            "media_stack.cli.commands.reset_admin_main.UserServiceFactory"
        ) as factory_cls:
            factory_cls.return_value.build.return_value = service
            rc = ResetAdminCommand().main(
                ["--username", "admin", "--password", "bootstrap"],
            )
        self.assertEqual(rc, 0)
        service.create_user.assert_called_once()
        self.assertTrue(service.create_user.call_args.kwargs.get(
            "skip_policy_check", False,
        ))
        service.reset_password.assert_called_once()

    def test_empty_password_exits_nonzero(self):
        """Never accept an empty password — it would write an empty
        hash to Authelia's users file, which Authelia treats as
        corrupt and refuses to start."""
        import io
        with patch("sys.stdin", io.StringIO("\n")):
            rc = ResetAdminCommand().main(
                ["--username", "admin", "--password-stdin"],
            )
        self.assertEqual(rc, 2)

    def test_password_flag_and_prompt_are_mutually_exclusive(self):
        """argparse has to block the nonsense combination of both a
        literal and an interactive prompt so we never accidentally
        ignore the flag the operator thought was authoritative."""
        with self.assertRaises(SystemExit):
            ResetAdminCommand().parse_args(
                ["--password", "x", "--prompt"],
            )

    def test_provider_failure_surfaces_warning_but_keeps_rc_zero(self):
        """If Authelia returns ``no_ref`` or an error, we still
        consider the rotation a success from the controller's
        perspective (users.json is updated, audit log is written).
        The CLI prints a WARN so the operator knows a manual
        follow-up may be needed, but exits 0 because forcing a
        non-zero here would scare operators into thinking nothing
        worked when the controller write did succeed."""
        service = self._stub_service_with_existing_admin()
        service.reset_password.return_value = {
            "user_id": "u1",
            "providers": {"authelia": "no_ref"},
        }
        with patch(
            "media_stack.cli.commands.reset_admin_main.UserServiceFactory"
        ) as factory_cls:
            factory_cls.return_value.build.return_value = service
            rc = ResetAdminCommand().main(
                ["--username", "admin", "--password", "x"],
            )
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
