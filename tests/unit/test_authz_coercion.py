"""Legacy string-actor coercion tests.

Split out of ``test_authz.py`` to keep that file under the
400-line ratchet. Exercises the migration-era ``str -> Actor.system``
coercion path in ``core.auth.authz._extract_actor``. When every
callsite has been converted to ``actor=Actor(...)`` and the
coercion branch is removed, this file can be deleted along with
the counter in ``authz.py``.
"""

from __future__ import annotations

import pytest

from media_stack.core.auth.authz import (
    Actor,
    AuthorizationError,
    requires_admin,
    requires_self_or_admin,
)


class TestLegacyStringActorCoercion:
    """During the actor-str -> Actor migration, callers that still pass
    ``actor="some_label"`` are coerced to ``Actor.system(label)`` so
    the decorator doesn't reject them. This keeps the codebase green
    while the ratchet drives legacy callsites to zero.
    """

    @staticmethod
    @requires_admin
    def admin_fn(*, actor: Actor) -> Actor:
        # Return the received actor so tests can inspect the coercion.
        return actor

    @staticmethod
    @requires_self_or_admin(param="user_id")
    def self_or_admin_fn(*, user_id: str, actor: Actor) -> Actor:
        return actor

    def test_string_actor_coerced_to_system(self) -> None:
        received = TestLegacyStringActorCoercion.admin_fn(
            actor="admin-bootstrap",
        )
        assert isinstance(received, Actor)
        assert received.is_system
        assert received.is_admin
        assert received.username == "admin-bootstrap"

    def test_empty_string_actor_coerced_to_unknown(self) -> None:
        received = TestLegacyStringActorCoercion.admin_fn(actor="")
        assert received.is_system
        assert received.username == "unknown"

    def test_string_actor_passes_admin_check(self) -> None:
        # String actors coerce to system actors, which are admin.
        result = TestLegacyStringActorCoercion.admin_fn(
            actor="legacy-caller",
        )
        assert isinstance(result, Actor)

    def test_string_actor_passes_self_or_admin(self) -> None:
        received = TestLegacyStringActorCoercion.self_or_admin_fn(
            user_id="alice", actor="system",
        )
        assert received.is_system

    def test_non_string_non_actor_denied(self) -> None:
        with pytest.raises(AuthorizationError) as exc:
            TestLegacyStringActorCoercion.admin_fn(actor=123)  # type: ignore[arg-type]
        assert exc.value.reason == "missing_actor"

    def test_callsite_counter_increments(self) -> None:
        from media_stack.core.auth import authz as authz_mod

        before = authz_mod._LEGACY_STRING_ACTOR_CALLSITES.get(
            "ratchet-probe", 0,
        )
        TestLegacyStringActorCoercion.admin_fn(actor="ratchet-probe")
        TestLegacyStringActorCoercion.admin_fn(actor="ratchet-probe")
        after = authz_mod._LEGACY_STRING_ACTOR_CALLSITES.get(
            "ratchet-probe", 0,
        )
        assert after == before + 2
