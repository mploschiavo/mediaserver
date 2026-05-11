#!/usr/bin/env python3
"""Entry-point shim for ``bin/reset-admin.sh``.

ADR-0015 Phase 7i. Pre-Phase-7i this module held the full
``ResetAdminCommand`` (199 LoC). Phase 7i moved the workflow onto
:class:`ResetAdminRunner` under workflows/; this shim is argparse +
service-factory wiring + main.

Test-surface preservation:

* :class:`ResetAdminCommand` is an alias for :class:`ResetAdminRunner`
  so ``ResetAdminCommand().main(...)`` keeps working in
  :mod:`tests.unit.auth.test_reset_admin_cli`.
* :class:`UserServiceFactory` is imported at module scope so tests
  patching ``media_stack.cli.commands.reset_admin_main.UserServiceFactory``
  intercept the construction.

Break-glass recovery for the case where the admin password is lost or
the on-disk state has drifted (e.g. Authelia's ``users_database.yml``
holds a hash that doesn't match either the env seed or the
dashboard-rotated value).
"""

from __future__ import annotations

import sys

from media_stack.cli.workflows.reset_admin_runner import ResetAdminRunner
from media_stack.core.auth.users.user_service_factory import UserServiceFactory


class ResetAdminCommand(ResetAdminRunner):
    """Back-compat subclass: wires :class:`UserServiceFactory` for ``main()``.

    The test suite constructs ``ResetAdminCommand()`` directly and
    calls ``.main(argv)`` — that path now builds the service via the
    module-scope :class:`UserServiceFactory` (which tests patch).
    """

    def main(self, argv: list[str] | None = None) -> int:
        module = sys.modules[__name__]
        args = self.parse_args(argv)
        # Sample the (possibly patched) factory at call time so test
        # patches of ``reset_admin_main.UserServiceFactory`` take effect.
        password = self._resolve_password(args)
        if not password:
            print("[ERR] reset-admin: empty password", file=sys.stderr)
            return 2
        service = module.UserServiceFactory().build()
        return self.run(args, service)


# Module-level singleton + back-compat aliases.
_instance = ResetAdminCommand()
parse_args = _instance.parse_args
main = _instance.main
_resolve_password = _instance._resolve_password
_find_or_create_admin = _instance._find_or_create_admin
_print_outcome = _instance._print_outcome


__all__ = [
    "ResetAdminCommand",
    "ResetAdminRunner",
    "UserServiceFactory",
    "_find_or_create_admin",
    "_print_outcome",
    "_resolve_password",
    "main",
    "parse_args",
]


if __name__ == "__main__":
    raise SystemExit(main())
