"""Ratchet: every authz-scoped service method must be decorated.

Each entry in ``_AUTHZ_SCOPED_CLASSES`` names a service class whose
**public** methods take privileged action and therefore MUST be
decorated with one of ``core.auth.authz``'s ``@requires_*`` decorators.
Private helpers (leading underscore), properties, and classmethods
without an ``actor`` parameter are ignored.

Reasoning. Inline ``if actor.is_admin`` checks drift. Declarative
decorators don't. A method that forgets the check is silent —
catastrophically so for a security feature. This ratchet makes the
forgetting loud.

Adding a new service? Put it in ``_AUTHZ_SCOPED_CLASSES``. Adding a
new method that genuinely needs no authz (e.g. a pure helper)? Put
it in ``_ALLOWED_UNDECORATED`` with a one-line note. The allowlist
must be reviewed at every merge.
"""

from __future__ import annotations

import inspect
import unittest

from media_stack.core.auth.users.invite_service import InviteService
from media_stack.core.auth.users.user_service import (
    UserReconcileService,
    UserWriteService,
)

# Classes whose public methods are authz-scoped.
_AUTHZ_SCOPED_CLASSES: tuple[type, ...] = (
    UserWriteService,
    UserReconcileService,
    InviteService,
)

# Methods explicitly exempted from decoration. Each entry is
# ``(ClassName, method_name, reason)``. Keep the list short; every
# entry is a code-review question.
_ALLOWED_UNDECORATED: frozenset[tuple[str, str]] = frozenset({
    # Token-gated pre-login flow. Anyone with a valid one-time invite
    # token may call accept(); the token itself is the authorization.
    # The internal create_user call uses Actor.system to pass its own
    # @requires_admin check. Documented in invite_service.accept.
    ("InviteService", "accept"),
    # Read-only listing of invites — exposed via admin-only UI; the
    # calling handler is gated on admin. No per-user targeting.
    # TODO: decorate with @requires_admin once the service takes an
    # Actor (it currently takes no actor kwarg at all).
    ("InviteService", "list_pending"),
})


def _looks_decorated(method: object) -> bool:
    return bool(getattr(method, "__authz__", None))


def _takes_actor(method: object) -> bool:
    try:
        sig = inspect.signature(method)
    except (TypeError, ValueError):
        return False
    return "actor" in sig.parameters


class DecoratorAuthzRatchet(unittest.TestCase):
    """Every public, actor-taking method must be decorated."""

    def test_every_actor_method_is_decorated(self) -> None:
        violations: list[str] = []
        for cls in _AUTHZ_SCOPED_CLASSES:
            for name in dir(cls):
                if name.startswith("_"):
                    continue
                attr = getattr(cls, name, None)
                if not callable(attr):
                    continue
                if not _takes_actor(attr):
                    # Methods without an actor parameter are not
                    # authz-scoped in the sense this ratchet enforces.
                    continue
                if (cls.__name__, name) in _ALLOWED_UNDECORATED:
                    continue
                if not _looks_decorated(attr):
                    violations.append(
                        f"{cls.__name__}.{name} takes actor but is "
                        "missing an @requires_* decorator",
                    )
        self.assertFalse(
            violations,
            "Undecorated authz-scoped methods:\n  - " + "\n  - ".join(violations),
        )

    def test_allowlist_only_names_real_methods(self) -> None:
        """Prevent the allowlist from holding stale entries after a
        rename or a method is removed."""
        index = {cls.__name__: cls for cls in _AUTHZ_SCOPED_CLASSES}
        for class_name, method_name in _ALLOWED_UNDECORATED:
            cls = index.get(class_name)
            self.assertIsNotNone(
                cls, f"allowlist names unknown class: {class_name}",
            )
            assert cls is not None  # for type checker
            self.assertTrue(
                hasattr(cls, method_name),
                f"allowlist names unknown method: "
                f"{class_name}.{method_name}",
            )

    def test_decorated_methods_expose_authz_string(self) -> None:
        """The ``__authz__`` marker is what this ratchet inspects.
        Every decorator in ``core.auth.authz`` sets it; if that
        contract breaks the ratchet becomes a rubber stamp."""
        seen_markers: set[str] = set()
        for cls in _AUTHZ_SCOPED_CLASSES:
            for name in dir(cls):
                if name.startswith("_"):
                    continue
                attr = getattr(cls, name, None)
                if not callable(attr):
                    continue
                marker = getattr(attr, "__authz__", None)
                if marker:
                    seen_markers.add(marker)
        self.assertTrue(
            seen_markers,
            "no @requires_* markers found on any scanned service — "
            "either every method is on the allowlist (wrong) or the "
            "decorators stopped setting __authz__ (regression)",
        )


if __name__ == "__main__":
    unittest.main()
