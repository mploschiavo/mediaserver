"""Unit tests for the authz module.

Covers:
  * ``Actor`` construction, classmethod builders, derived properties
  * Every ``@requires_*`` decorator: accept + reject paths
  * ``@forbidden_for_impersonation`` composition order with ``@requires_admin``
  * ``AuthorizationError`` shape (reason + optional detail)
  * ``__authz__`` marker present on every wrapped method (ratchet relies on it)
"""

from __future__ import annotations

import pytest

from media_stack.core.auth.authz import (
    Actor,
    AuthorizationError,
    forbidden_for_impersonation,
    requires_admin,
    requires_authenticated,
    requires_role,
    requires_self_or_admin,
)


def admin_actor(**overrides: object) -> Actor:
    return Actor(username="admin", is_admin=True, **overrides)  # type: ignore[arg-type]


def user_actor(name: str = "alice", **overrides: object) -> Actor:
    return Actor(username=name, **overrides)  # type: ignore[arg-type]


def anon_actor() -> Actor:
    return Actor.anonymous()


def system_actor(label: str = "bootstrap") -> Actor:
    return Actor.system(label)


# --------------------------------------------------------------------------
# Actor
# --------------------------------------------------------------------------


class TestActor:
    def test_anonymous_has_no_identity(self) -> None:
        a = Actor.anonymous()
        assert a.username == ""
        assert not a.is_authenticated
        assert a.is_anonymous
        assert not a.is_admin
        assert not a.is_system

    def test_system_actor_is_admin_and_system(self) -> None:
        a = Actor.system("watchdog")
        assert a.is_admin
        assert a.is_system
        assert a.username == "watchdog"
        assert a.is_authenticated

    def test_system_actor_rejects_empty_label(self) -> None:
        with pytest.raises(ValueError):
            Actor.system("")

    def test_actor_is_frozen(self) -> None:
        a = user_actor()
        with pytest.raises(Exception):
            a.username = "other"  # type: ignore[misc]

    def test_audit_label_plain(self) -> None:
        assert user_actor("bob").audit_label == "bob"

    def test_audit_label_impersonation(self) -> None:
        a = user_actor("bob", is_impersonating="admin")
        assert a.audit_label == "admin -> bob"

    def test_audit_label_anonymous(self) -> None:
        assert Actor.anonymous().audit_label == "anonymous"

    def test_roles_default_is_empty_frozenset(self) -> None:
        assert user_actor().roles == frozenset()

    def test_client_ip_and_ua_default_empty(self) -> None:
        a = user_actor()
        assert a.client_ip == ""
        assert a.user_agent == ""


# --------------------------------------------------------------------------
# requires_authenticated
# --------------------------------------------------------------------------


class TestRequiresAuthenticated:
    @staticmethod
    @requires_authenticated
    def fn(*, actor: Actor) -> str:
        return "ok"

    def test_user_passes(self) -> None:
        assert TestRequiresAuthenticated.fn(actor=user_actor()) == "ok"

    def test_admin_passes(self) -> None:
        assert TestRequiresAuthenticated.fn(actor=admin_actor()) == "ok"

    def test_system_passes(self) -> None:
        assert TestRequiresAuthenticated.fn(actor=system_actor()) == "ok"

    def test_anonymous_denied(self) -> None:
        with pytest.raises(AuthorizationError) as exc:
            TestRequiresAuthenticated.fn(actor=anon_actor())
        assert exc.value.reason == "authentication_required"

    def test_missing_actor_denied(self) -> None:
        with pytest.raises(AuthorizationError) as exc:
            TestRequiresAuthenticated.fn()  # type: ignore[call-arg]
        assert exc.value.reason == "missing_actor"

    def test_positional_actor_denied(self) -> None:
        with pytest.raises(AuthorizationError) as exc:
            TestRequiresAuthenticated.fn(user_actor())  # type: ignore[misc]
        assert exc.value.reason == "missing_actor"

    def test_marker_set(self) -> None:
        assert TestRequiresAuthenticated.fn.__authz__ == "requires_authenticated"  # type: ignore[attr-defined]


# --------------------------------------------------------------------------
# requires_admin
# --------------------------------------------------------------------------


class TestRequiresAdmin:
    @staticmethod
    @requires_admin
    def fn(*, actor: Actor) -> str:
        return "ok"

    def test_admin_passes(self) -> None:
        assert TestRequiresAdmin.fn(actor=admin_actor()) == "ok"

    def test_system_passes(self) -> None:
        assert TestRequiresAdmin.fn(actor=system_actor()) == "ok"

    def test_non_admin_user_denied(self) -> None:
        with pytest.raises(AuthorizationError) as exc:
            TestRequiresAdmin.fn(actor=user_actor())
        assert exc.value.reason == "admin_required"

    def test_anonymous_denied(self) -> None:
        with pytest.raises(AuthorizationError) as exc:
            TestRequiresAdmin.fn(actor=anon_actor())
        assert exc.value.reason == "admin_required"

    def test_marker_set(self) -> None:
        assert TestRequiresAdmin.fn.__authz__ == "requires_admin"  # type: ignore[attr-defined]


# --------------------------------------------------------------------------
# requires_self_or_admin
# --------------------------------------------------------------------------


class TestRequiresSelfOrAdmin:
    @staticmethod
    @requires_self_or_admin(param="user_id")
    def fn(*, user_id: str, actor: Actor) -> str:
        return f"ok:{user_id}"

    def test_self_passes(self) -> None:
        result = TestRequiresSelfOrAdmin.fn(user_id="alice", actor=user_actor("alice"))
        assert result == "ok:alice"

    def test_admin_passes_for_other(self) -> None:
        result = TestRequiresSelfOrAdmin.fn(user_id="alice", actor=admin_actor())
        assert result == "ok:alice"

    def test_system_passes(self) -> None:
        result = TestRequiresSelfOrAdmin.fn(user_id="alice", actor=system_actor())
        assert result == "ok:alice"

    def test_other_user_denied(self) -> None:
        with pytest.raises(AuthorizationError) as exc:
            TestRequiresSelfOrAdmin.fn(user_id="alice", actor=user_actor("bob"))
        assert exc.value.reason == "self_or_admin_required"

    def test_anonymous_denied(self) -> None:
        with pytest.raises(AuthorizationError) as exc:
            TestRequiresSelfOrAdmin.fn(user_id="alice", actor=anon_actor())
        assert exc.value.reason == "authentication_required"

    def test_missing_target_denied(self) -> None:
        with pytest.raises(AuthorizationError) as exc:
            TestRequiresSelfOrAdmin.fn(actor=user_actor())  # type: ignore[call-arg]
        assert exc.value.reason == "missing_target"

    def test_marker_set(self) -> None:
        assert TestRequiresSelfOrAdmin.fn.__authz__ == "requires_self_or_admin(user_id)"  # type: ignore[attr-defined]

    def test_positional_target_self(self) -> None:
        # Target resolved from positional arg (real-world: method with
        # ``def reset_password(self, user_id, *, actor)`` and caller
        # that passes user_id positionally).
        @requires_self_or_admin(param="user_id")
        def fn(user_id: str, *, actor: Actor) -> str:
            return f"ok:{user_id}"

        assert fn("alice", actor=user_actor("alice")) == "ok:alice"

    def test_positional_target_admin(self) -> None:
        @requires_self_or_admin(param="user_id")
        def fn(user_id: str, *, actor: Actor) -> str:
            return f"ok:{user_id}"

        assert fn("alice", actor=admin_actor()) == "ok:alice"

    def test_positional_target_other_user_denied(self) -> None:
        @requires_self_or_admin(param="user_id")
        def fn(user_id: str, *, actor: Actor) -> str:
            return f"ok:{user_id}"

        with pytest.raises(AuthorizationError) as exc:
            fn("alice", actor=user_actor("bob"))
        assert exc.value.reason == "self_or_admin_required"

    def test_positional_target_on_method(self) -> None:
        # Bound-method case: param_names includes 'self', so user_id is
        # at index 1 (matching the actual positional position after
        # Python binds self).
        class Svc:
            @requires_self_or_admin(param="user_id")
            def do(self, user_id: str, *, actor: Actor) -> str:
                return f"touched:{user_id}"

        svc = Svc()
        assert svc.do("alice", actor=user_actor("alice")) == "touched:alice"
        with pytest.raises(AuthorizationError):
            svc.do("alice", actor=user_actor("bob"))


# --------------------------------------------------------------------------
# requires_role
# --------------------------------------------------------------------------


class TestRequiresRole:
    @staticmethod
    @requires_role("security_admin")
    def fn(*, actor: Actor) -> str:
        return "ok"

    def test_role_present_passes(self) -> None:
        actor = user_actor(roles=frozenset({"security_admin"}))
        assert TestRequiresRole.fn(actor=actor) == "ok"

    def test_admin_passes_even_without_named_role(self) -> None:
        assert TestRequiresRole.fn(actor=admin_actor()) == "ok"

    def test_system_passes_even_without_named_role(self) -> None:
        assert TestRequiresRole.fn(actor=system_actor()) == "ok"

    def test_role_absent_denied(self) -> None:
        with pytest.raises(AuthorizationError) as exc:
            TestRequiresRole.fn(actor=user_actor(roles=frozenset({"ops"})))
        assert exc.value.reason == "role_required"

    def test_anonymous_denied(self) -> None:
        with pytest.raises(AuthorizationError) as exc:
            TestRequiresRole.fn(actor=anon_actor())
        assert exc.value.reason == "role_required"

    def test_marker_set(self) -> None:
        assert TestRequiresRole.fn.__authz__ == "requires_role(security_admin)"  # type: ignore[attr-defined]


# --------------------------------------------------------------------------
# forbidden_for_impersonation (composed with requires_admin)
# --------------------------------------------------------------------------


class TestForbiddenForImpersonation:
    @staticmethod
    @requires_admin
    @forbidden_for_impersonation
    def sensitive(*, actor: Actor) -> str:
        return "ok"

    def test_real_admin_passes(self) -> None:
        assert TestForbiddenForImpersonation.sensitive(actor=admin_actor()) == "ok"

    def test_impersonating_admin_denied(self) -> None:
        impersonator = admin_actor(is_impersonating="real_admin")
        with pytest.raises(AuthorizationError) as exc:
            TestForbiddenForImpersonation.sensitive(actor=impersonator)
        assert exc.value.reason == "forbidden_for_impersonation"

    def test_non_admin_denied_with_admin_reason(self) -> None:
        # The outer @requires_admin fires before the inner
        # @forbidden_for_impersonation — a non-admin should be rejected
        # with admin_required, not impersonation.
        with pytest.raises(AuthorizationError) as exc:
            TestForbiddenForImpersonation.sensitive(actor=user_actor())
        assert exc.value.reason == "admin_required"

    def test_system_passes(self) -> None:
        # System actors are never impersonating by construction.
        assert TestForbiddenForImpersonation.sensitive(actor=system_actor()) == "ok"


# --------------------------------------------------------------------------
# Standalone forbidden_for_impersonation
# --------------------------------------------------------------------------


class TestForbiddenForImpersonationAlone:
    @staticmethod
    @forbidden_for_impersonation
    def fn(*, actor: Actor) -> str:
        return "ok"

    def test_marker_set(self) -> None:
        assert TestForbiddenForImpersonationAlone.fn.__authz__ == "forbidden_for_impersonation"  # type: ignore[attr-defined]

    def test_non_impersonating_passes(self) -> None:
        assert TestForbiddenForImpersonationAlone.fn(actor=user_actor()) == "ok"

    def test_impersonating_user_denied(self) -> None:
        impersonator = user_actor("target", is_impersonating="admin")
        with pytest.raises(AuthorizationError) as exc:
            TestForbiddenForImpersonationAlone.fn(actor=impersonator)
        assert exc.value.reason == "forbidden_for_impersonation"


# --------------------------------------------------------------------------
# AuthorizationError shape
# --------------------------------------------------------------------------


class TestAuthorizationError:
    def test_reason_only(self) -> None:
        e = AuthorizationError("denied")
        assert e.reason == "denied"
        assert e.detail == ""
        assert str(e) == "denied"

    def test_reason_and_detail(self) -> None:
        e = AuthorizationError("denied", "actor=bob")
        assert e.reason == "denied"
        assert e.detail == "actor=bob"
        assert str(e) == "denied: actor=bob"

    def test_inherits_exception(self) -> None:
        with pytest.raises(Exception):
            raise AuthorizationError("denied")


# --------------------------------------------------------------------------
# Decorator on a bound method (real-world usage on a service class)
# --------------------------------------------------------------------------


class TestDecoratorOnBoundMethod:
    """Verify decorators work when applied to service-class methods,
    which is how the actual migration will use them."""

    class FakeService:
        @requires_admin
        def do_admin_thing(self, x: int, *, actor: Actor) -> int:
            return x * 2

        @requires_self_or_admin(param="user_id")
        def do_user_thing(self, *, user_id: str, actor: Actor) -> str:
            return f"touched:{user_id}"

    def test_admin_method_admin_passes(self) -> None:
        svc = self.FakeService()
        assert svc.do_admin_thing(21, actor=admin_actor()) == 42

    def test_admin_method_user_denied(self) -> None:
        svc = self.FakeService()
        with pytest.raises(AuthorizationError):
            svc.do_admin_thing(21, actor=user_actor())

    def test_self_or_admin_self_passes(self) -> None:
        svc = self.FakeService()
        result = svc.do_user_thing(user_id="alice", actor=user_actor("alice"))
        assert result == "touched:alice"

    def test_self_or_admin_other_user_denied(self) -> None:
        svc = self.FakeService()
        with pytest.raises(AuthorizationError):
            svc.do_user_thing(user_id="alice", actor=user_actor("bob"))

    def test_marker_preserved_on_bound_method(self) -> None:
        svc = self.FakeService()
        assert svc.do_admin_thing.__authz__ == "requires_admin"  # type: ignore[attr-defined]
        assert svc.do_user_thing.__authz__ == "requires_self_or_admin(user_id)"  # type: ignore[attr-defined]
