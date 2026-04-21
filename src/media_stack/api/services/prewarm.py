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
import threading
import time

_log = logging.getLogger("controller_api")


def warm_argon2() -> None:
    """Force the argon2-cffi backend to dlopen and initialise.
    A single throwaway hash is enough; subsequent ``hash()`` /
    ``verify()`` calls reuse the same backend."""
    try:
        from argon2 import PasswordHasher
        PasswordHasher().hash("warmup-throwaway-not-stored")
    except Exception as exc:  # noqa: BLE001
        _log.debug("[DEBUG] argon2 warmup skipped: %s", exc)


def warm_user_service() -> None:
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


def warm_basic_auth_verifier() -> None:
    """The basic-auth verifier instantiates its own argon2
    PasswordHasher; share the warmup so the first request that
    presents Basic credentials doesn't pay for it."""
    try:
        from media_stack.core.auth.basic_auth_verifier import (
            BasicAuthVerifier,
        )
        BasicAuthVerifier
    except Exception as exc:  # noqa: BLE001
        _log.debug("[DEBUG] basic-auth verifier warmup skipped: %s", exc)


def run_in_background() -> None:
    """Spawn a daemon thread that walks each warmer in order. The
    thread is daemon so a stuck warmer (broken disk, missing file)
    can't keep the controller from shutting down. Errors are
    swallowed at DEBUG — pre-warm is an optimisation, never a gate."""
    def _walk() -> None:
        t0 = time.monotonic()
        warm_argon2()
        warm_user_service()
        warm_basic_auth_verifier()
        _log.info(
            "controller pre-warm complete in %.0fms",
            (time.monotonic() - t0) * 1000,
        )

    threading.Thread(
        target=_walk, daemon=True, name="controller-prewarm",
    ).start()
