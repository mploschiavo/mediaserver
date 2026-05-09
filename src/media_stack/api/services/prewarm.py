"""Boot-time pre-warm of expensive lazy-loaded subsystems.

The first password rotation after a controller restart was visibly
slow (1-3 seconds, often longer than subsequent rotations). The
dominant cause is one-time-per-process initialisation that runs
on the request thread:

- The ``argon2-cffi`` C backend dlopens and runs setup the first
  time ``PasswordHasher()`` is instantiated.
- The audit-log hash-chain cache (see ``AuditLog._last_hash``) is
  cold; the first ``append()`` scans the entire log file. On
  small fresh installs this is fast, on long-running ones it's
  not — and it always falls on the unlucky first request.
- The user store loads ``users.json`` from disk on first read.
- The provider registry resolves Authelia's ``users_database.yml``.

Pre-warming these at boot moves the cost off the user's request
path. The pre-warm runs in a daemon thread so a slow disk doesn't
delay ``/healthz`` returning ok.

This module is deliberately thin: each warmer is a one-line call
into the relevant subsystem. The point is to *trigger* lazy init,
not to do anything new — adding logic here would just create a
new place for boot bugs to hide."""

from __future__ import annotations

import logging
import sys
import threading
import time

_log = logging.getLogger("controller_api")


class PrewarmService:
    """Bundle of boot-time warmers for the controller API.

    Each ``warm_*`` method triggers one-time-per-process
    initialisation of an expensive subsystem, then swallows any
    failure at DEBUG. ``run_in_background`` walks them all on a
    daemon thread so the boot path never blocks on a slow warmer."""

    def warm_argon2(self) -> None:
        """Force the argon2-cffi backend to dlopen and initialise.
        A single throwaway hash is enough; subsequent ``hash()`` /
        ``verify()`` calls reuse the same backend."""
        try:
            from argon2 import PasswordHasher
            PasswordHasher().hash("warmup-throwaway-not-stored")
        except Exception as exc:  # noqa: BLE001
            _log.debug("[DEBUG] argon2 warmup skipped: %s", exc)

    def warm_user_service(self) -> None:
        """Build the user service singleton once so the first
        ``/api/users/.../reset-password`` doesn't pay for the user
        store load + provider registry resolve."""
        try:
            from media_stack.core.auth.users.user_service_factory import (
                build_default_service,
            )
            svc = build_default_service()
            if svc is None:
                return
            # Touch the audit log so its hash-chain cache is primed.
            # _last_hash() reads the file end-to-end on a cold cache.
            try:
                svc._audit._last_hash()  # noqa: SLF001
            except Exception as exc:  # noqa: BLE001
                _log.debug("[DEBUG] audit chain warmup skipped: %s", exc)
            # Touch the policy so the salt + history rules are loaded.
            try:
                getattr(svc, "_policy", None)
            except Exception as exc:  # noqa: BLE001
                _log.debug("[DEBUG] policy warmup skipped: %s", exc)
        except Exception as exc:  # noqa: BLE001
            _log.debug("[DEBUG] user service warmup skipped: %s", exc)

    def warm_basic_auth_verifier(self) -> None:
        """The basic-auth verifier instantiates its own argon2
        PasswordHasher; share the warmup so the first request that
        presents Basic credentials doesn't pay for it."""
        try:
            from media_stack.core.auth.basic_auth_verifier import (
                BasicAuthVerifier,
            )
            # Reference the class to confirm the import resolves;
            # the verifier itself is constructed per-request.
            BasicAuthVerifier
        except Exception as exc:  # noqa: BLE001
            _log.debug("[DEBUG] basic-auth verifier warmup skipped: %s", exc)

    def run_in_background(self) -> None:
        """Spawn a daemon thread that walks each warmer in order.
        The thread is daemon so a stuck warmer (broken disk,
        missing file) can't keep the controller from shutting down.
        Errors are swallowed at DEBUG — pre-warm is an
        optimisation, never a gate."""
        # Dispatch through sys.modules so tests can mock.patch the
        # individual warmers via their module-level aliases.
        mod = sys.modules[__name__]

        def _walk() -> None:
            t0 = time.monotonic()
            mod.warm_argon2()
            mod.warm_user_service()
            mod.warm_basic_auth_verifier()
            _log.info(
                "controller pre-warm complete in %.0fms",
                (time.monotonic() - t0) * 1000,
            )

        threading.Thread(
            target=_walk, daemon=True, name="controller-prewarm",
        ).start()


_INSTANCE = PrewarmService()

warm_argon2 = _INSTANCE.warm_argon2
warm_user_service = _INSTANCE.warm_user_service
warm_basic_auth_verifier = _INSTANCE.warm_basic_auth_verifier
run_in_background = _INSTANCE.run_in_background
