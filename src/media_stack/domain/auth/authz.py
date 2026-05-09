"""Authorization primitives for service-layer enforcement.

Every privileged service method MUST be decorated with one of the
``@requires_*`` decorators defined here. The handler's job is to
resolve an ``Actor`` from the request context and pass it as
``actor=Actor(...)`` to the service method. The decorator raises
``AuthorizationError`` on deny; the handler catches it and returns
HTTP 403.

A ratchet test enumerates public methods on authz-scoped service
classes and asserts each one carries an ``__authz__`` marker, so the
pattern cannot be silently dropped.

Design notes
------------
- Authz is service-layer, not route-layer. Services stay authoritative
  for their own rules; handlers never encode policy.
- ``Actor.system(label)`` represents internal callers (bootstrap,
  watchdogs, healers). System actors pass every check but carry the
  ``is_system`` flag so audit entries distinguish them from humans.
- ``Actor.is_impersonating`` is already carried so the future
  impersonation feature (see ``docs/roadmap/session-visibility-followups.md``)
  plugs in without touching call sites. For now it is always ``None``.
- Decorators must receive ``actor=`` as a keyword argument. Positional
  ``actor`` is rejected to keep the migration mechanical and the
  ratchet easy to read.
"""

from __future__ import annotations

import inspect
import logging
import sys
from dataclasses import dataclass
from functools import wraps
from typing import Any, Callable, TypeVar

F = TypeVar("F", bound=Callable[..., Any])

_log = logging.getLogger("media_stack.authz")

# Callsite counter for the ``actor=<str>`` migration. The ratchet
# (tests/unit/test_authz_decorator_ratchet.py) asserts this is monotonic
# non-increasing across releases. When every callsite has been
# converted to ``actor=Actor(...)``, the coercion branch in
# ``_extract_actor`` and this counter are deleted.
_LEGACY_STRING_ACTOR_CALLSITES: dict[str, int] = {}


class AuthorizationError(Exception):
    """Raised by ``@requires_*`` decorators when the actor is not permitted.

    ``reason`` is a stable machine-readable code ("admin_required",
    "self_or_admin_required", ...). ``detail`` is a human-readable
    addendum suitable for logging but never for the HTTP body (the
    handler redacts it to avoid leaking target identifiers).
    """

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class Actor:
    """Who is making this call.

    Built by the handler from session context. Carries everything the
    decorators need for policy and everything the audit log needs for
    forensics. Frozen so services can rely on it being immutable.
    """

    username: str
    roles: frozenset[str] = frozenset()
    is_admin: bool = False
    is_system: bool = False
    session_id: str | None = None
    source_provider: str = "controller"
    is_impersonating: str | None = None
    client_ip: str = ""
    user_agent: str = ""

    @classmethod
    def system(cls, label: str) -> "Actor":
        """Internal caller (bootstrap / watchdog / healer).

        System actors pass every authz check and are marked so in
        audit entries. Never construct one from user input.
        """
        if not label:
            raise ValueError("system actor requires a non-empty label")
        return cls(username=label, is_admin=True, is_system=True)

    @classmethod
    def anonymous(cls) -> "Actor":
        """Pre-login / unauthenticated actor. Fails every ``@requires_*``."""
        return cls(username="")

    @property
    def is_anonymous(self) -> bool:
        return not self.username

    @property
    def is_authenticated(self) -> bool:
        return bool(self.username)

    @property
    def audit_label(self) -> str:
        """String used as ``actor`` in audit.jsonl entries.

        For impersonation: ``"<real_admin> -> <target>"`` so a log
        reader sees both identities. Otherwise just the username.
        """
        if self.is_impersonating:
            return f"{self.is_impersonating} -> {self.username}"
        return self.username or "anonymous"


class AuthorizationDecorators:
    """Class-based collection of authz decorator helpers.

    All public callables in this module are instance methods on this
    class, then aliased at module level so existing imports
    (``from authz import requires_admin``) still work. Cross-method
    calls dispatch through ``sys.modules[__name__]`` so ``mock.patch``
    on the module-level alias intercepts as expected.
    """

    def _extract_actor(
        self, kwargs: dict, default: Any = inspect.Parameter.empty
    ) -> Actor:
        actor = kwargs.get("actor", default)
        # When the caller omitted actor and the wrapped function declares a
        # default, use it. Preserves legacy behaviour where methods had
        # ``actor: str = "system"`` defaults that tests implicitly relied on.
        if actor is inspect.Parameter.empty:
            actor = None
        if isinstance(actor, Actor):
            return actor
        if isinstance(actor, str):
            # Migration-era coercion: legacy callers pass ``actor="label"``
            # (or omit it and fall back to a string default). Coerce to a
            # system actor so the decorator doesn't reject them. Every such
            # callsite is tracked for the migration ratchet and deleted
            # once its caller is rewritten to pass ``Actor(...)``.
            label = actor or "unknown"
            _LEGACY_STRING_ACTOR_CALLSITES[label] = (
                _LEGACY_STRING_ACTOR_CALLSITES.get(label, 0) + 1
            )
            _log.debug(
                "authz: legacy string actor coerced to Actor.system(%r)", label,
            )
            # Mutate the kwargs so the wrapped method receives a real Actor
            # too — callees rely on actor.audit_label, not raw strings.
            coerced = Actor.system(label)
            kwargs["actor"] = coerced
            return coerced
        raise AuthorizationError(
            "missing_actor",
            "decorated method must be called with actor=Actor(...)",
        )

    def _actor_default_from_signature(self, fn: Callable[..., Any]) -> Any:
        """Return the ``actor`` parameter's default value from ``fn``'s
        signature, or ``inspect.Parameter.empty`` if it has no default.
        The decorator falls back to this when the caller omits actor.
        """
        try:
            param = inspect.signature(fn).parameters.get("actor")
        except (TypeError, ValueError):
            return inspect.Parameter.empty
        return param.default if param is not None else inspect.Parameter.empty

    def requires_authenticated(self, fn: F) -> F:
        """Caller must be a logged-in human or a system actor."""

        mod = sys.modules[__name__]
        default = mod._actor_default_from_signature(fn)

        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            actor = sys.modules[__name__]._extract_actor(kwargs, default=default)
            if not (actor.is_authenticated or actor.is_system):
                raise AuthorizationError("authentication_required")
            return fn(*args, **kwargs)

        wrapper.__authz__ = "requires_authenticated"  # type: ignore[attr-defined]
        return wrapper  # type: ignore[return-value]

    def requires_admin(self, fn: F) -> F:
        """Caller must be admin (``role.controller_admin`` true) or system."""

        mod = sys.modules[__name__]
        default = mod._actor_default_from_signature(fn)

        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            actor = sys.modules[__name__]._extract_actor(kwargs, default=default)
            if not actor.is_admin:
                raise AuthorizationError(
                    "admin_required", f"actor={actor.audit_label}"
                )
            return fn(*args, **kwargs)

        wrapper.__authz__ = "requires_admin"  # type: ignore[attr-defined]
        return wrapper  # type: ignore[return-value]

    def requires_self_or_admin(self, param: str) -> Callable[[F], F]:
        """Caller must be the target user (match on ``username``) or an admin.

        ``param`` names the parameter on the decorated method that identifies
        the target user — typically ``"user_id"`` or ``"username"``. The
        target is resolved from kwargs first, then from positional args via
        the wrapped function's signature so callers can pass either form.
        """

        def decorator(fn: F) -> F:
            sig = inspect.signature(fn)
            param_names = list(sig.parameters)
            mod = sys.modules[__name__]
            default = mod._actor_default_from_signature(fn)

            @wraps(fn)
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                actor = sys.modules[__name__]._extract_actor(
                    kwargs, default=default
                )
                if param in kwargs:
                    target: str | None = str(kwargs[param])
                else:
                    try:
                        idx = param_names.index(param)
                    except ValueError:
                        idx = -1
                    if 0 <= idx < len(args):
                        target = str(args[idx])
                    else:
                        target = None
                if target is None:
                    raise AuthorizationError(
                        "missing_target",
                        f"@requires_self_or_admin(param={param!r}) "
                        f"requires {param} to be provided",
                    )
                if actor.is_admin:
                    return fn(*args, **kwargs)
                if not actor.is_authenticated:
                    raise AuthorizationError("authentication_required")
                if actor.username == target:
                    return fn(*args, **kwargs)
                raise AuthorizationError(
                    "self_or_admin_required",
                    f"actor={actor.audit_label} target={target}",
                )

            wrapper.__authz__ = f"requires_self_or_admin({param})"  # type: ignore[attr-defined]
            return wrapper  # type: ignore[return-value]

        return decorator

    def requires_role(self, role: str) -> Callable[[F], F]:
        """Caller must carry ``role`` in ``actor.roles`` or be admin/system."""

        def decorator(fn: F) -> F:
            mod = sys.modules[__name__]
            default = mod._actor_default_from_signature(fn)

            @wraps(fn)
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                actor = sys.modules[__name__]._extract_actor(
                    kwargs, default=default
                )
                if actor.is_admin or role in actor.roles:
                    return fn(*args, **kwargs)
                raise AuthorizationError(
                    "role_required",
                    f"role={role} actor={actor.audit_label}",
                )

            wrapper.__authz__ = f"requires_role({role})"  # type: ignore[attr-defined]
            return wrapper  # type: ignore[return-value]

        return decorator

    def forbidden_for_impersonation(self, fn: F) -> F:
        """Block this method when the caller is an impersonation session.

        Compose with ``@requires_admin`` for sensitive actions (password
        change, MFA reset, emergency revoke) so even an impersonating
        admin is denied.
        """

        mod = sys.modules[__name__]
        default = mod._actor_default_from_signature(fn)

        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            actor = sys.modules[__name__]._extract_actor(kwargs, default=default)
            if actor.is_impersonating:
                raise AuthorizationError(
                    "forbidden_for_impersonation",
                    f"original={actor.is_impersonating} as={actor.username}",
                )
            return fn(*args, **kwargs)

        wrapper.__authz__ = "forbidden_for_impersonation"  # type: ignore[attr-defined]
        return wrapper  # type: ignore[return-value]


_INSTANCE = AuthorizationDecorators()

# Module-level aliases — keep every public name importable as before.
# Private helpers are aliased too because the decorators dispatch
# through ``sys.modules[__name__]`` so ``mock.patch`` on these names
# intercepts in tests.
_extract_actor = _INSTANCE._extract_actor
_actor_default_from_signature = _INSTANCE._actor_default_from_signature
requires_authenticated = _INSTANCE.requires_authenticated
requires_admin = _INSTANCE.requires_admin
requires_self_or_admin = _INSTANCE.requires_self_or_admin
requires_role = _INSTANCE.requires_role
forbidden_for_impersonation = _INSTANCE.forbidden_for_impersonation
