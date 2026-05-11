"""ResetAdminRunner — break-glass admin credential reset workflow.

ADR-0015 Phase 7i. Pre-Phase-7i ``ResetAdminCommand`` lived in
``cli/commands/reset_admin_main.py`` (199 LoC). Phase 7i moves
the workflow logic onto this class; the commands shim shrinks
to argparse + service-factory wiring + main.

The :class:`UserService` is constructor-injected (or passed to
:meth:`run`) so test stubs can substitute a mock without
patching the workflows module — the commands shim handles the
real-vs-mock dispatch by reading
``media_stack.cli.commands.reset_admin_main.UserServiceFactory``,
which tests patch directly.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from typing import Any


_DEFAULT_USERNAME = "admin"
_DEFAULT_EMAIL = "admin@local"
_DEFAULT_DISPLAY_NAME = "Media Stack Admin"
_DEFAULT_ROLE = "superadmin"
_ACTOR = "cli-reset-admin"


class ResetAdminRunner:
    """Workflow: write a new admin password into every UserProvider."""

    def parse_args(self, argv: list[str] | None = None) -> argparse.Namespace:
        parser = argparse.ArgumentParser(
            prog="bin/reset-admin.sh",
            description=(
                "Reset the stack admin credential. Writes the new "
                "password into all configured UserProviders (Authelia) "
                "and marks the controller row as rotated. "
                "Run inside the controller pod."
            ),
        )
        parser.add_argument(
            "--username",
            default=os.environ.get("STACK_ADMIN_USERNAME", _DEFAULT_USERNAME),
            help=(
                "Admin username to reset. Defaults to "
                "$STACK_ADMIN_USERNAME or 'admin'."
            ),
        )
        parser.add_argument(
            "--email",
            default=os.environ.get("STACK_ADMIN_EMAIL", _DEFAULT_EMAIL),
            help=(
                "Email to use if the admin row has to be created from "
                "scratch. Ignored when the user already exists."
            ),
        )
        password_group = parser.add_mutually_exclusive_group(required=True)
        password_group.add_argument(
            "--password",
            help=(
                "New password literal. Avoid this in shared shells — "
                "prefer --prompt or --password-stdin so it doesn't land "
                "in history."
            ),
        )
        password_group.add_argument(
            "--prompt",
            action="store_true",
            help="Read the new password interactively with no echo.",
        )
        password_group.add_argument(
            "--password-stdin",
            action="store_true",
            help=(
                "Read the new password from stdin (newline-terminated). "
                "Useful for piping from a secret manager."
            ),
        )
        return parser.parse_args(argv)

    def run(
        self, args: argparse.Namespace, service: Any,
    ) -> int:
        password = self._resolve_password(args)
        if not password:
            print("[ERR] reset-admin: empty password", file=sys.stderr)
            return 2

        user = self._find_or_create_admin(service, args.username, args.email)
        result = service.reset_password(
            user.id, password=password, actor=_ACTOR,
        )
        self._print_outcome(args.username, result)
        return 0

    def _resolve_password(self, args: argparse.Namespace) -> str:
        if args.password:
            return args.password
        if args.prompt:
            return getpass.getpass("New admin password: ").strip()
        # --password-stdin
        return sys.stdin.readline().rstrip("\n")

    def _find_or_create_admin(
        self, service: Any, username: str, email: str,
    ) -> Any:
        """Locate the admin row, or create one if the store is empty.

        Matches by username (case-insensitive). If nothing is found we
        create the user through the normal UserService path — which
        provisions it in the source-of-truth provider (Authelia) and
        populates ``provider_refs`` so the subsequent
        ``reset_password`` call actually reaches the provider instead
        of no-op'ing on a missing ref.
        """
        target = username.strip().lower()
        for user in service._store.list_all(include_deleted=False):
            if user.username.strip().lower() == target:
                return user
        # No row — create it. Use a placeholder password so the create
        # path succeeds; reset_password immediately overwrites it with
        # the real value the operator supplied.
        placeholder = "PLACEHOLDER_" + os.urandom(16).hex()
        created = service.create_user(
            email=email,
            username=username,
            display_name=_DEFAULT_DISPLAY_NAME,
            role_slug=_DEFAULT_ROLE,
            password=placeholder,
            actor=_ACTOR,
            skip_policy_check=True,
        )
        return service._store.get(created["id"])

    def _print_outcome(self, username: str, result: dict) -> None:
        providers = result.get("providers") or {}
        provider_summary = ", ".join(
            f"{name}={state}" for name, state in sorted(providers.items())
        ) or "(none)"
        print(
            f"[OK] reset-admin: {username} password rotated "
            f"(providers: {provider_summary}, source=rotated, "
            f"audit=reset_password)",
        )
        for name, state in providers.items():
            if state not in ("ok", "healed"):
                print(
                    f"[WARN] reset-admin: provider {name!r} returned "
                    f"{state!r} — credential may not be active for "
                    f"that backend.", file=sys.stderr,
                )


__all__ = ["ResetAdminRunner"]
