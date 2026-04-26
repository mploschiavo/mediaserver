"""InviteService — admin creates invites, users accept with a chosen password.

The controller never knows the new user's password until the user sets
it themselves via accept(). Fixes the out-of-band-password-handoff
weakness of create_user.
"""

from __future__ import annotations

import logging
from typing import Any

from media_stack.domain.auth.authz import Actor, requires_admin
from media_stack.core.auth.users.invite_store import InviteStore
from media_stack.domain.auth.users.models import Invite

_log = logging.getLogger("media_stack")


class InviteError(RuntimeError):
    pass


class InviteService:

    def __init__(
        self,
        *,
        invites: InviteStore,
        user_creator,  # callable matching UserWriteService.create_user
        audit,
    ) -> None:
        self._invites = invites
        self._create_user = user_creator
        self._audit = audit

    @requires_admin
    def create_invite(self, *, email: str, role_slug: str,
                      actor: Actor | str,
                      ttl_hours: int = 24) -> dict[str, Any]:
        if not email or not role_slug:
            raise InviteError("email and role_slug are required")
        actor_label = actor.audit_label if isinstance(actor, Actor) else str(actor)
        invite, token = self._invites.create(
            email=email, role_slug=role_slug, created_by=actor_label,
            ttl_hours=ttl_hours,
        )
        self._audit.append(
            actor=actor_label, action="invite_created", target=email, result="ok",
            detail={"invite_id": invite.id, "role": role_slug,
                    "expires_at": invite.expires_at, "ttl_hours": ttl_hours},
        )
        out = invite.to_dict()
        out["token"] = token  # returned ONCE; never stored
        out["token_hash"] = ""  # don't leak the hash either
        return out

    def accept(self, *, token: str, username: str, display_name: str,
               password: str, actor: str = "invitee") -> dict[str, Any]:
        # ``accept`` is intentionally undecorated: callers are
        # unauthenticated invitees holding a one-time token. The token
        # itself is the authorization — anyone with a valid one can
        # redeem it. The internal ``_create_user`` call is gated via
        # a system-actor (see below) so it passes its own
        # ``@requires_admin`` check.
        if not token or not username or not password:
            raise InviteError("token, username, password required")
        invite = self._invites.find_by_token(token)
        if invite is None:
            raise InviteError("invite not found or expired")
        if invite.accepted_at:
            raise InviteError("invite already accepted")
        if self._invites.is_expired(invite):
            raise InviteError("invite expired")

        # Delegate to the normal create flow (policy check, provider
        # provisioning, audit). System actor — the invite mechanism
        # itself is creating the user on behalf of the invitee.
        result = self._create_user(
            email=invite.email,
            username=username,
            display_name=display_name or username,
            role_slug=invite.role_slug,
            password=password,
            actor=Actor.system(f"invite:{invite.id}"),
        )
        self._invites.accept(invite.id)
        self._audit.append(
            actor=actor, action="invite_accepted", target=invite.email,
            result="ok",
            detail={"invite_id": invite.id, "user_id": result.get("id")},
        )
        # Don't echo the password back — user set it.
        result.pop("generated_password", None)
        return result

    def list_pending(self) -> list[dict[str, Any]]:
        out = []
        for inv in self._invites.list_pending():
            d = inv.to_dict()
            d["token_hash"] = ""  # never expose
            out.append(d)
        return out

    @requires_admin
    def revoke(self, invite_id: str, *, actor: Actor | str) -> dict[str, Any]:
        all_invites = {i.id: i for i in self._invites.list_all()}
        inv = all_invites.get(invite_id)
        if inv is None:
            raise InviteError(f"unknown invite: {invite_id}")
        actor_label = actor.audit_label if isinstance(actor, Actor) else str(actor)
        self._invites.revoke(invite_id)
        self._audit.append(
            actor=actor_label, action="invite_revoked", target=inv.email,
            result="ok", detail={"invite_id": invite_id},
        )
        return {"invite_id": invite_id, "status": "revoked"}
