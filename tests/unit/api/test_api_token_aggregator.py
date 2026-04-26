"""Unit tests for ``services.security.api_token_aggregator``.

These tests exercise the fan-out aggregator that merges controller
bearer tokens with per-provider API tokens (Jellyfin, \\*arrs) into
a uniform list of ``APITokenRecord`` for the session-visibility UI.

Covers:
    * Empty-inputs → empty list.
    * Merge + provider tagging for multiple providers.
    * Provider that raises during ``list_api_tokens`` is swallowed.
    * ``revoke`` happy path + unknown-token path (both audited).
    * ``revoke`` admin-only enforcement (non-admin → AuthorizationError).
    * ``to_dict`` round-trip.
    * Ordering: provider ascending, created_at descending within.
    * Protocol conformance guard in ``__init__``.
    * ``list_for_user`` with empty external_id_for_provider map.
    * Ratchet: ``APITokenRecord`` field-set is frozen against secret
      leakage.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.core.auth.authz import Actor, AuthorizationError  # noqa: E402
from media_stack.core.auth.users import audit_actions  # noqa: E402
from media_stack.core.auth.users.visibility_protocols import APIToken  # noqa: E402
from media_stack.services.security.api_token_aggregator import (  # noqa: E402
    APITokenAggregator,
    APITokenRecord,
    CONTROLLER_PROVIDER,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeControllerStore:
    def __init__(self, by_user: dict[str, list[APIToken]] | None = None,
                 revoke_ok: bool = True,
                 raise_on_list: bool = False) -> None:
        self._by_user = by_user or {}
        self._revoke_ok = revoke_ok
        self._raise_on_list = raise_on_list
        self.revoked: list[str] = []

    def list_by_user(self, username: str) -> list[APIToken]:
        if self._raise_on_list:
            raise RuntimeError("boom")
        return list(self._by_user.get(username, []))

    def revoke_token(self, token_id: str) -> bool:
        self.revoked.append(token_id)
        return self._revoke_ok


class _FakeProvider:
    """Minimal structural impl of ``APITokenProvider``."""

    def __init__(self, name: str, tokens_by_ext: dict[str, list[APIToken]] | None = None,
                 raise_on_list: bool = False, raise_on_revoke: bool = False) -> None:
        self.name = name
        self._tokens = tokens_by_ext or {}
        self._raise_on_list = raise_on_list
        self._raise_on_revoke = raise_on_revoke
        self.revoked: list[tuple[str, str]] = []

    def list_api_tokens(self, external_id: str) -> list[APIToken]:
        if self._raise_on_list:
            raise RuntimeError("list blew up")
        return list(self._tokens.get(external_id, []))

    def revoke_api_token(self, external_id: str, token_id: str) -> None:
        if self._raise_on_revoke:
            raise RuntimeError("revoke blew up")
        self.revoked.append((external_id, token_id))


class _RecordingAudit:
    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    def append(self, actor: str, action: str, target: str, result: str = "ok",
               ip: str = "", user_agent: str = "",
               detail: dict[str, Any] | None = None) -> dict[str, Any]:
        row = {
            "actor": actor, "action": action, "target": target,
            "result": result, "ip": ip, "user_agent": user_agent,
            "detail": dict(detail or {}),
        }
        self.entries.append(row)
        return row


def _tok(token_id: str, created_at: str, name: str = "",
         scopes: tuple[str, ...] = ()) -> APIToken:
    return APIToken(
        token_id=token_id, name=name or token_id,
        created_at=created_at, scopes=scopes,
    )


def _admin(username: str = "alice") -> Actor:
    return Actor(username=username, is_admin=True, client_ip="10.0.0.1",
                 user_agent="pytest/1.0")


def _user(username: str = "bob") -> Actor:
    return Actor(username=username, is_admin=False)


# ---------------------------------------------------------------------------
# APITokenRecord
# ---------------------------------------------------------------------------


class APITokenRecordTests(unittest.TestCase):

    def test_to_dict_round_trip(self) -> None:
        r = APITokenRecord(
            provider="jellyfin", token_id="tk-1", name="app",
            created_at="2026-01-01T00:00:00Z",
            last_used_at="2026-04-24T00:00:00Z",
            scopes=("read",), created_by="alice",
        )
        self.assertEqual(r.to_dict(), {
            "provider": "jellyfin",
            "token_id": "tk-1",
            "name": "app",
            "created_at": "2026-01-01T00:00:00Z",
            "last_used_at": "2026-04-24T00:00:00Z",
            "scopes": ["read"],
            "created_by": "alice",
        })

    def test_to_dict_returns_fresh_list(self) -> None:
        r = APITokenRecord(
            provider="sonarr", token_id="tk", name="s", created_at="",
            last_used_at="", scopes=("a", "b"),
        )
        d = r.to_dict()
        d["scopes"].append("c")
        self.assertEqual(r.scopes, ("a", "b"))

    def test_from_api_token_tags_provider(self) -> None:
        src = _tok("tk-9", "2026-02-02T00:00:00Z", scopes=("read",))
        r = APITokenRecord.from_api_token("prowlarr", src)
        self.assertEqual(r.provider, "prowlarr")
        self.assertEqual(r.token_id, "tk-9")
        self.assertEqual(r.scopes, ("read",))

    def test_apitokenrecord_has_no_secret_field(self) -> None:
        # Security ratchet — mirrors the freeze on APIToken. Adding a
        # field to this dataclass must be an explicit decision with
        # security review: any field that could carry a token secret
        # breaks the "metadata only" invariant of the session-
        # visibility surface.
        allowed = {
            "provider", "token_id", "name", "created_at",
            "last_used_at", "scopes", "created_by",
        }
        actual = {f.name for f in APITokenRecord.__dataclass_fields__.values()}
        self.assertEqual(
            actual, allowed,
            "APITokenRecord fields changed — review: adding a field "
            "that could carry a secret is a security bug",
        )


# ---------------------------------------------------------------------------
# APITokenAggregator.__init__ guards
# ---------------------------------------------------------------------------


class AggregatorInitTests(unittest.TestCase):

    def test_no_inputs_is_valid(self) -> None:
        agg = APITokenAggregator()
        self.assertEqual(agg.list_for_user(username="alice"), [])

    def test_rejects_non_provider_in_list(self) -> None:
        class _NotAProvider:
            # Missing .name, list_api_tokens, revoke_api_token entirely.
            pass
        with self.assertRaises(TypeError):
            APITokenAggregator(providers=[_NotAProvider()])  # type: ignore[list-item]

    def test_accepts_structural_provider(self) -> None:
        p = _FakeProvider("jellyfin")
        # No raise.
        APITokenAggregator(providers=[p])


# ---------------------------------------------------------------------------
# list_for_user
# ---------------------------------------------------------------------------


class ListForUserTests(unittest.TestCase):

    def test_empty_inputs_empty_list(self) -> None:
        agg = APITokenAggregator(
            controller_token_store=_FakeControllerStore(),
            providers=[_FakeProvider("jellyfin")],
        )
        self.assertEqual(agg.list_for_user(username="alice"), [])

    def test_merges_controller_and_providers_with_tags(self) -> None:
        controller = _FakeControllerStore(by_user={
            "alice": [_tok("c-1", "2026-04-01T00:00:00Z", scopes=("admin",))],
        })
        jelly = _FakeProvider("jellyfin", tokens_by_ext={
            "jelly-alice": [_tok("j-1", "2026-03-10T00:00:00Z")],
        })
        sonarr = _FakeProvider("sonarr", tokens_by_ext={
            "admin": [_tok("s-1", "2026-02-01T00:00:00Z")],
        })
        agg = APITokenAggregator(
            controller_token_store=controller,
            providers=[sonarr, jelly],
        )
        rows = agg.list_for_user(
            username="alice",
            external_id_for_provider={
                "jellyfin": "jelly-alice", "sonarr": "admin",
            },
        )
        providers = [r.provider for r in rows]
        # Provider-name ascending
        self.assertEqual(providers, ["controller", "jellyfin", "sonarr"])
        ids = {(r.provider, r.token_id) for r in rows}
        self.assertEqual(ids, {
            ("controller", "c-1"), ("jellyfin", "j-1"), ("sonarr", "s-1"),
        })

    def test_provider_raises_is_swallowed(self) -> None:
        good = _FakeProvider("jellyfin", tokens_by_ext={
            "ext": [_tok("j-1", "2026-03-10T00:00:00Z")],
        })
        bad = _FakeProvider("sonarr", raise_on_list=True)
        agg = APITokenAggregator(providers=[good, bad])
        rows = agg.list_for_user(
            username="alice",
            external_id_for_provider={"jellyfin": "ext", "sonarr": "admin"},
        )
        self.assertEqual([r.token_id for r in rows], ["j-1"])

    def test_controller_raises_is_swallowed(self) -> None:
        controller = _FakeControllerStore(raise_on_list=True)
        jelly = _FakeProvider("jellyfin", tokens_by_ext={
            "e": [_tok("j-1", "2026-03-10T00:00:00Z")],
        })
        agg = APITokenAggregator(
            controller_token_store=controller, providers=[jelly],
        )
        rows = agg.list_for_user(
            username="alice",
            external_id_for_provider={"jellyfin": "e"},
        )
        self.assertEqual([r.provider for r in rows], ["jellyfin"])

    def test_missing_external_id_yields_zero(self) -> None:
        jelly = _FakeProvider("jellyfin", tokens_by_ext={
            "would-be": [_tok("j-1", "2026-03-10T00:00:00Z")],
        })
        agg = APITokenAggregator(providers=[jelly])
        # Empty map → provider contributes zero rows, no raise.
        rows = agg.list_for_user(username="alice", external_id_for_provider={})
        self.assertEqual(rows, [])

    def test_ordering_within_provider_newest_first(self) -> None:
        jelly = _FakeProvider("jellyfin", tokens_by_ext={
            "e": [
                _tok("older", "2026-01-01T00:00:00Z"),
                _tok("newest", "2026-04-20T00:00:00Z"),
                _tok("middle", "2026-02-15T00:00:00Z"),
            ],
        })
        agg = APITokenAggregator(providers=[jelly])
        rows = agg.list_for_user(
            username="alice",
            external_id_for_provider={"jellyfin": "e"},
        )
        self.assertEqual([r.token_id for r in rows],
                         ["newest", "middle", "older"])

    def test_ordering_across_providers(self) -> None:
        controller = _FakeControllerStore(by_user={
            "alice": [
                _tok("c-old", "2025-12-01T00:00:00Z"),
                _tok("c-new", "2026-04-01T00:00:00Z"),
            ],
        })
        sonarr = _FakeProvider("sonarr", tokens_by_ext={
            "admin": [_tok("s-1", "2026-04-24T00:00:00Z")],
        })
        agg = APITokenAggregator(
            controller_token_store=controller, providers=[sonarr],
        )
        rows = agg.list_for_user(
            username="alice",
            external_id_for_provider={"sonarr": "admin"},
        )
        # Provider ascending; within controller, newest first.
        self.assertEqual(
            [(r.provider, r.token_id) for r in rows],
            [("controller", "c-new"), ("controller", "c-old"),
             ("sonarr", "s-1")],
        )

    def test_empty_created_at_sinks_within_provider(self) -> None:
        jelly = _FakeProvider("jellyfin", tokens_by_ext={
            "e": [
                _tok("unknown-ts", ""),
                _tok("dated", "2026-02-01T00:00:00Z"),
            ],
        })
        agg = APITokenAggregator(providers=[jelly])
        rows = agg.list_for_user(
            username="alice",
            external_id_for_provider={"jellyfin": "e"},
        )
        self.assertEqual([r.token_id for r in rows], ["dated", "unknown-ts"])

    def test_no_username_skips_controller(self) -> None:
        controller = _FakeControllerStore(by_user={"": []})
        agg = APITokenAggregator(controller_token_store=controller)
        # Anonymous lookup (no username) shouldn't call into the
        # controller's store — controller tokens are always per-user.
        self.assertEqual(agg.list_for_user(username=""), [])


# ---------------------------------------------------------------------------
# revoke
# ---------------------------------------------------------------------------


class RevokeTests(unittest.TestCase):

    def test_requires_admin(self) -> None:
        agg = APITokenAggregator(providers=[_FakeProvider("jellyfin")])
        with self.assertRaises(AuthorizationError) as ctx:
            agg.revoke(provider="jellyfin", token_id="x",
                       external_id="e", actor=_user())
        self.assertEqual(ctx.exception.reason, "admin_required")

    def test_revoke_controller_happy_path(self) -> None:
        controller = _FakeControllerStore(revoke_ok=True)
        audit = _RecordingAudit()
        agg = APITokenAggregator(
            controller_token_store=controller, audit_log=audit,
        )
        ok = agg.revoke(provider=CONTROLLER_PROVIDER, token_id="t-1",
                        actor=_admin())
        self.assertTrue(ok)
        self.assertEqual(controller.revoked, ["t-1"])
        self.assertEqual(len(audit.entries), 1)
        entry = audit.entries[0]
        self.assertEqual(entry["action"], audit_actions.SESSION_REVOKED)
        self.assertEqual(entry["target"], "t-1")
        self.assertEqual(entry["result"], "ok")
        self.assertEqual(entry["detail"]["provider"], CONTROLLER_PROVIDER)

    def test_revoke_controller_unknown_audits_as_not_found(self) -> None:
        controller = _FakeControllerStore(revoke_ok=False)
        audit = _RecordingAudit()
        agg = APITokenAggregator(
            controller_token_store=controller, audit_log=audit,
        )
        ok = agg.revoke(provider=CONTROLLER_PROVIDER, token_id="missing",
                        actor=_admin())
        self.assertFalse(ok)
        self.assertEqual(audit.entries[0]["result"], "not_found")

    def test_revoke_controller_not_configured(self) -> None:
        audit = _RecordingAudit()
        agg = APITokenAggregator(audit_log=audit)
        ok = agg.revoke(provider=CONTROLLER_PROVIDER, token_id="t",
                        actor=_admin())
        self.assertFalse(ok)
        self.assertEqual(
            audit.entries[0]["detail"]["reason"],
            "controller_store_not_configured",
        )

    def test_revoke_controller_store_raises(self) -> None:
        class _ExplodingStore:
            def list_by_user(self, username: str) -> list[APIToken]:
                return []

            def revoke_token(self, token_id: str) -> bool:
                raise RuntimeError("kaboom")
        audit = _RecordingAudit()
        agg = APITokenAggregator(
            controller_token_store=_ExplodingStore(), audit_log=audit,
        )
        ok = agg.revoke(provider=CONTROLLER_PROVIDER, token_id="t",
                        actor=_admin())
        self.assertFalse(ok)
        self.assertEqual(audit.entries[0]["detail"]["reason"], "exception")

    def test_revoke_provider_happy_path(self) -> None:
        jelly = _FakeProvider("jellyfin")
        audit = _RecordingAudit()
        agg = APITokenAggregator(providers=[jelly], audit_log=audit)
        ok = agg.revoke(provider="jellyfin", token_id="tk",
                        external_id="ext-1", actor=_admin())
        self.assertTrue(ok)
        self.assertEqual(jelly.revoked, [("ext-1", "tk")])
        self.assertEqual(audit.entries[0]["detail"]["external_id"], "ext-1")

    def test_revoke_unknown_provider_audits(self) -> None:
        audit = _RecordingAudit()
        agg = APITokenAggregator(audit_log=audit)
        ok = agg.revoke(provider="ghostarr", token_id="tk",
                        external_id="x", actor=_admin())
        self.assertFalse(ok)
        self.assertEqual(
            audit.entries[0]["detail"]["reason"], "unknown_provider",
        )

    def test_revoke_provider_missing_external_id(self) -> None:
        jelly = _FakeProvider("jellyfin")
        audit = _RecordingAudit()
        agg = APITokenAggregator(providers=[jelly], audit_log=audit)
        ok = agg.revoke(provider="jellyfin", token_id="tk", actor=_admin())
        self.assertFalse(ok)
        self.assertEqual(jelly.revoked, [])
        self.assertEqual(
            audit.entries[0]["detail"]["reason"], "missing_external_id",
        )

    def test_revoke_provider_raises_audits_as_exception(self) -> None:
        jelly = _FakeProvider("jellyfin", raise_on_revoke=True)
        audit = _RecordingAudit()
        agg = APITokenAggregator(providers=[jelly], audit_log=audit)
        ok = agg.revoke(provider="jellyfin", token_id="tk",
                        external_id="e", actor=_admin())
        self.assertFalse(ok)
        self.assertEqual(audit.entries[0]["detail"]["reason"], "exception")

    def test_revoke_without_audit_log_still_works(self) -> None:
        jelly = _FakeProvider("jellyfin")
        agg = APITokenAggregator(providers=[jelly])
        ok = agg.revoke(provider="jellyfin", token_id="tk",
                        external_id="e", actor=_admin())
        self.assertTrue(ok)
        self.assertEqual(jelly.revoked, [("e", "tk")])

    def test_audit_append_failure_does_not_leak(self) -> None:
        class _BrokenAudit:
            def append(self, *a: Any, **kw: Any) -> Any:
                raise RuntimeError("disk full")
        jelly = _FakeProvider("jellyfin")
        agg = APITokenAggregator(providers=[jelly], audit_log=_BrokenAudit())
        # Revoke should still report success.
        self.assertTrue(
            agg.revoke(provider="jellyfin", token_id="tk",
                       external_id="e", actor=_admin()),
        )

    def test_revoke_audit_uses_actor_audit_label_and_context(self) -> None:
        jelly = _FakeProvider("jellyfin")
        audit = _RecordingAudit()
        agg = APITokenAggregator(providers=[jelly], audit_log=audit)
        actor = Actor(username="carol", is_admin=True,
                      client_ip="192.168.1.2", user_agent="curl/8")
        agg.revoke(provider="jellyfin", token_id="tk",
                   external_id="e", actor=actor)
        entry = audit.entries[0]
        self.assertEqual(entry["actor"], "carol")
        self.assertEqual(entry["ip"], "192.168.1.2")
        self.assertEqual(entry["user_agent"], "curl/8")


if __name__ == "__main__":
    unittest.main()
