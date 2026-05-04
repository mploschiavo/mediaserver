"""Unit tests for the session-visibility GET dispatcher.

Covers every endpoint wired by
``src/media_stack/api/services/security_get_handlers.py`` plus the
``security-read`` rate-limit gate in
``api/routes/sessions_security_get.py`` (lifted out of the
retired ``handlers_get.py`` during ADR-0007 Phase 2 Phase E).

For each endpoint the test matrix covers:
  * happy-path 200 + shape
  * 403 when the service raises ``AuthorizationError``
  * 400 on malformed query-string
  * 429 on rate-limit exhaustion (admin reads only)
  * empty-result shape
  * 401/403 when the caller is anonymous or lacks admin

Contract tests, not integration tests — we stub the services so the
boundaries are clean and the tests run in milliseconds.
"""

from __future__ import annotations

import io
import json
import sys
import unittest
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.core.auth.authz import Actor, AuthorizationError  # noqa: E402


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _FakeHandler:
    """Minimal stand-in for ``ControllerAPIHandler``.

    Only the surface the helper needs: ``path``, ``headers``,
    ``_json_response``. Mirrors the idiom in
    ``tests/unit/test_api_server_handlers.py`` but lighter — the
    helper never touches send_response / rfile / wfile directly.
    """

    def __init__(self, path: str = "/", headers: dict | None = None) -> None:
        self.path = path
        self.headers = BaseHTTPRequestHandler.MessageClass()
        for k, v in (headers or {}).items():
            self.headers[k] = v
        self.client_address = ("10.0.0.5", 12345)
        self.status: int | None = None
        self.body: dict | None = None
        self.wfile = io.BytesIO()
        self.rfile = io.BytesIO()

    def _json_response(self, status: int, payload: dict) -> None:
        self.status = int(status)
        # Round-trip through JSON to catch non-serialisable payloads.
        self.body = json.loads(json.dumps(payload))


@dataclass
class _FakeDTO:
    """Generic DTO stand-in — mirrors SessionDTO.to_dict / FailedLoginCluster /
    ConcurrentSessionAlert / NewLocationAlert / BanRecord / APITokenRecord.
    """

    payload: dict

    def to_dict(self) -> dict:
        return dict(self.payload)


class _FakeAggregator:
    def __init__(self) -> None:
        self.list_all_return: list = []
        self.list_for_user_return: list = []
        self.raise_on_list_all: Exception | None = None
        self.raise_on_list_for_user: Exception | None = None
        self.last_actor: Actor | None = None

    def list_all(self, *, actor: Actor):
        self.last_actor = actor
        if self.raise_on_list_all is not None:
            raise self.raise_on_list_all
        return list(self.list_all_return)

    def list_for_user(self, *, username: str, actor: Actor):
        self.last_actor = actor
        if self.raise_on_list_for_user is not None:
            raise self.raise_on_list_for_user
        return list(self.list_for_user_return)


class _FakeReportService:
    def __init__(self) -> None:
        self.failed_login_clusters_return: list = []
        self.new_location_alerts_return: list = []
        self.concurrent_spikes_return: list = []
        self.login_history_return: list = []
        self.raise_exc: Exception | None = None
        self.received_kwargs: dict | None = None

    def failed_login_clusters(self, *, actor, since_hours, min_attempts):
        self.received_kwargs = {
            "actor": actor, "since_hours": since_hours,
            "min_attempts": min_attempts,
        }
        if self.raise_exc is not None:
            raise self.raise_exc
        return list(self.failed_login_clusters_return)

    def new_location_alerts(self, *, actor, lookback_days, since_hours):
        self.received_kwargs = {
            "actor": actor, "lookback_days": lookback_days,
            "since_hours": since_hours,
        }
        if self.raise_exc is not None:
            raise self.raise_exc
        return list(self.new_location_alerts_return)

    def concurrent_session_spikes(self, *, actor, threshold):
        self.received_kwargs = {"actor": actor, "threshold": threshold}
        if self.raise_exc is not None:
            raise self.raise_exc
        return list(self.concurrent_spikes_return)

    def login_history_for_user(self, *, username, actor, limit=100):
        self.received_kwargs = {
            "username": username, "actor": actor, "limit": limit,
        }
        if self.raise_exc is not None:
            raise self.raise_exc
        return list(self.login_history_return)


class _FakeTokenStore:
    def __init__(self) -> None:
        self.tokens: list = []

    def list_all(self, owner_username: str = ""):
        return [t for t in self.tokens if t.owner_username == owner_username]


@dataclass
class _FakeToken:
    id: str
    owner_username: str
    name: str = ""
    created_at: str = ""

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name,
                "owner_username": self.owner_username,
                "created_at": self.created_at}


class _FakeBanStore:
    def __init__(self) -> None:
        self.user_bans: list = []
        self.ip_bans: list = []
        self.last_user_kwargs: dict | None = None
        self.last_ip_kwargs: dict | None = None

    def list_user_bans(self, include_expired: bool = False):
        self.last_user_kwargs = {"include_expired": include_expired}
        return list(self.user_bans)

    def list_ip_bans(self, include_expired: bool = False):
        self.last_ip_kwargs = {"include_expired": include_expired}
        return list(self.ip_bans)


class _FakeAuditLog:
    def __init__(self) -> None:
        self.head_return = {
            "height": 0, "hash": "", "ts": "", "ok": True,
        }

    def head(self):
        return dict(self.head_return)


@dataclass
class _FakeMFAState:
    enrolled: bool = False
    enrolled_methods: tuple = ()
    last_used_method: str = ""
    last_used_at: str = ""
    required: bool = False

    def to_dict(self) -> dict:
        return {
            "enrolled": self.enrolled,
            "enrolled_methods": list(self.enrolled_methods),
            "last_used_method": self.last_used_method,
            "last_used_at": self.last_used_at,
            "required": self.required,
        }


class _FixedActorResolver:
    def __init__(self, actor: Actor) -> None:
        self.actor = actor

    def resolve(self, handler, body=None):
        return self.actor


# ---------------------------------------------------------------------------
# Helper factory
# ---------------------------------------------------------------------------


def _build_helper(
    *,
    actor: Actor | None = None,
    aggregator: _FakeAggregator | None = None,
    reports: _FakeReportService | None = None,
    tokens: _FakeTokenStore | None = None,
    bans: _FakeBanStore | None = None,
    audit: _FakeAuditLog | None = None,
    mfa: _FakeMFAState | None = None,
):
    from media_stack.api.services.security_get_handlers import (
        _SessionVisibilityGetHelper,
    )
    actor = actor if actor is not None else Actor(
        username="alice", is_admin=True,
    )
    aggregator = aggregator or _FakeAggregator()
    reports = reports or _FakeReportService()
    tokens = tokens or _FakeTokenStore()
    bans = bans or _FakeBanStore()
    audit = audit or _FakeAuditLog()
    mfa_state = mfa or _FakeMFAState()
    return _SessionVisibilityGetHelper(
        actor_resolver=_FixedActorResolver(actor),
        aggregator_getter=lambda: aggregator,
        report_getter=lambda: reports,
        token_store_getter=lambda: tokens,
        ban_store_getter=lambda: bans,
        audit_getter=lambda: audit,
        mfa_getter=lambda username: mfa_state,
    ), {
        "actor": actor, "agg": aggregator, "reports": reports,
        "tokens": tokens, "bans": bans, "audit": audit, "mfa": mfa_state,
    }


# ---------------------------------------------------------------------------
# Admin endpoints — happy path
# ---------------------------------------------------------------------------


class TestActiveSessionsHappy(unittest.TestCase):

    def test_admin_gets_sessions_list(self):
        helper, deps = _build_helper()
        deps["agg"].list_all_return = [
            _FakeDTO({"provider": "controller", "session_id": "s1",
                      "username": "alice"}),
        ]
        h = _FakeHandler(path="/api/sessions/active")
        helper.dispatch(h, "/api/sessions/active")
        self.assertEqual(h.status, 200)
        self.assertEqual(h.body, {"sessions": [
            {"provider": "controller", "session_id": "s1", "username": "alice"},
        ]})

    def test_empty_result_synthesises_caller_session(self):
        # Under SSO the controller has no native sessions and the
        # provider impls all degrade to []. Rather than render the
        # misleading "no live sessions" empty state, the handler
        # surfaces the caller's own identity as a synthetic row so
        # the operator at least sees themselves on the page they're
        # currently looking at.
        helper, deps = _build_helper(
            actor=Actor(
                username="alice", is_admin=True,
                client_ip="203.0.113.10",
                user_agent="Mozilla/5.0 (X11; Linux x86_64)",
            ),
        )
        deps["agg"].list_all_return = []
        h = _FakeHandler(path="/api/sessions/active")
        helper.dispatch(h, "/api/sessions/active")
        self.assertEqual(h.status, 200)
        self.assertIsNotNone(h.body)
        sessions = h.body["sessions"]
        self.assertEqual(len(sessions), 1)
        synth = sessions[0]
        self.assertEqual(synth["username"], "alice")
        self.assertEqual(synth["provider"], "controller")
        self.assertEqual(synth["client_ip"], "203.0.113.10")
        self.assertEqual(
            synth["client"], "Mozilla/5.0 (X11; Linux x86_64)",
        )
        # Synthetic rows are read-only — we don't own the underlying
        # cookie, the operator revokes via the Authelia portal.
        self.assertFalse(synth["revokable"])
        self.assertEqual(synth["session_id"], "")

    def test_empty_result_anonymous_actor_returns_empty(self):
        # If somehow an anonymous actor reaches the dispatcher (it
        # shouldn't, the @requires_admin gate would raise first), the
        # synth fallback must NOT manufacture a phantom row — empty
        # list is the right behaviour.
        helper, deps = _build_helper(actor=Actor.anonymous())
        deps["agg"].list_all_return = []
        h = _FakeHandler(path="/api/sessions/active")
        helper.dispatch(h, "/api/sessions/active")
        self.assertEqual(h.status, 200)
        self.assertEqual(h.body, {"sessions": []})

    def test_non_empty_aggregator_skips_synth(self):
        # The synth row only fires when the aggregator returned
        # nothing — once the provider impls start producing rows the
        # synthetic placeholder must vanish so we don't double-count
        # the caller.
        helper, deps = _build_helper()
        deps["agg"].list_all_return = [
            _FakeDTO({"provider": "jellyfin", "session_id": "jf-1",
                      "username": "alice"}),
        ]
        h = _FakeHandler(path="/api/sessions/active")
        helper.dispatch(h, "/api/sessions/active")
        self.assertEqual(h.status, 200)
        self.assertEqual(len(h.body["sessions"]), 1)
        self.assertEqual(h.body["sessions"][0]["provider"], "jellyfin")


class TestActiveSessionsAuthz(unittest.TestCase):

    def test_403_when_service_raises_authz_error(self):
        helper, deps = _build_helper()
        deps["agg"].raise_on_list_all = AuthorizationError(
            "admin_required", "actor=alice",
        )
        h = _FakeHandler(path="/api/sessions/active")
        helper.dispatch(h, "/api/sessions/active")
        self.assertEqual(h.status, 403)
        self.assertEqual(h.body["error"], "admin_required")

    def test_401_when_actor_is_anonymous_and_service_says_so(self):
        helper, deps = _build_helper(actor=Actor.anonymous())
        deps["agg"].raise_on_list_all = AuthorizationError(
            "authentication_required",
        )
        h = _FakeHandler(path="/api/sessions/active")
        helper.dispatch(h, "/api/sessions/active")
        self.assertEqual(h.status, 401)


# ---------------------------------------------------------------------------
# User login-history (admin)
# ---------------------------------------------------------------------------


class TestUserLoginHistory(unittest.TestCase):

    def test_happy_path_parses_limit_and_forwards(self):
        helper, deps = _build_helper()
        deps["reports"].login_history_return = [{"action": "login_success"}]
        h = _FakeHandler(path="/api/users/alice/login-history?limit=50")
        helper.dispatch(h, "/api/users/alice/login-history")
        self.assertEqual(h.status, 200)
        self.assertEqual(h.body, {"entries": [{"action": "login_success"}]})
        self.assertEqual(deps["reports"].received_kwargs["username"], "alice")
        self.assertEqual(deps["reports"].received_kwargs["limit"], 50)

    def test_default_limit_applied_when_missing(self):
        helper, deps = _build_helper()
        h = _FakeHandler(path="/api/users/bob/login-history")
        helper.dispatch(h, "/api/users/bob/login-history")
        self.assertEqual(h.status, 200)
        self.assertEqual(deps["reports"].received_kwargs["limit"], 100)

    def test_malformed_limit_returns_400(self):
        helper, _ = _build_helper()
        h = _FakeHandler(path="/api/users/alice/login-history?limit=abc")
        helper.dispatch(h, "/api/users/alice/login-history")
        self.assertEqual(h.status, 400)
        self.assertEqual(h.body["error"], "bad_request")

    def test_403_when_service_raises(self):
        helper, deps = _build_helper()
        deps["reports"].raise_exc = AuthorizationError("admin_required")
        h = _FakeHandler(path="/api/users/alice/login-history?limit=5")
        helper.dispatch(h, "/api/users/alice/login-history")
        self.assertEqual(h.status, 403)


# ---------------------------------------------------------------------------
# Security report endpoints
# ---------------------------------------------------------------------------


class TestFailedLoginClusters(unittest.TestCase):

    def test_happy_path_with_query_params(self):
        helper, deps = _build_helper()
        deps["reports"].failed_login_clusters_return = [
            _FakeDTO({"ip_prefix": "10.0.0.0/24", "attempt_count": 9}),
        ]
        h = _FakeHandler(
            path="/api/security/failed-logins?since_hours=6&min_attempts=3",
        )
        helper.dispatch(h, "/api/security/failed-logins")
        self.assertEqual(h.status, 200)
        self.assertEqual(h.body["clusters"][0]["attempt_count"], 9)
        self.assertEqual(deps["reports"].received_kwargs["since_hours"], 6)
        self.assertEqual(deps["reports"].received_kwargs["min_attempts"], 3)

    def test_empty_result_shape(self):
        helper, _ = _build_helper()
        h = _FakeHandler(path="/api/security/failed-logins")
        helper.dispatch(h, "/api/security/failed-logins")
        self.assertEqual(h.status, 200)
        self.assertEqual(h.body, {"clusters": []})

    def test_malformed_query_string_is_400(self):
        helper, _ = _build_helper()
        h = _FakeHandler(
            path="/api/security/failed-logins?since_hours=not-a-number",
        )
        helper.dispatch(h, "/api/security/failed-logins")
        self.assertEqual(h.status, 400)

    def test_403_propagates(self):
        helper, deps = _build_helper()
        deps["reports"].raise_exc = AuthorizationError(
            "admin_required", "actor=bob",
        )
        h = _FakeHandler(path="/api/security/failed-logins")
        helper.dispatch(h, "/api/security/failed-logins")
        self.assertEqual(h.status, 403)


class TestNewLocations(unittest.TestCase):

    def test_happy_path(self):
        helper, deps = _build_helper()
        deps["reports"].new_location_alerts_return = [
            _FakeDTO({"username": "alice", "ip_prefix": "1.2.3.0/24"}),
        ]
        h = _FakeHandler(
            path="/api/security/new-locations?lookback_days=30&since_hours=12",
        )
        helper.dispatch(h, "/api/security/new-locations")
        self.assertEqual(h.status, 200)
        self.assertEqual(h.body["alerts"][0]["username"], "alice")
        self.assertEqual(deps["reports"].received_kwargs["lookback_days"], 30)

    def test_malformed_lookback_days_is_400(self):
        helper, _ = _build_helper()
        h = _FakeHandler(
            path="/api/security/new-locations?lookback_days=xyz",
        )
        helper.dispatch(h, "/api/security/new-locations")
        self.assertEqual(h.status, 400)

    def test_empty_alerts_list(self):
        helper, _ = _build_helper()
        h = _FakeHandler(path="/api/security/new-locations")
        helper.dispatch(h, "/api/security/new-locations")
        self.assertEqual(h.body, {"alerts": []})


class TestConcurrentSpikes(unittest.TestCase):

    def test_happy_path_with_threshold(self):
        helper, deps = _build_helper()
        deps["reports"].concurrent_spikes_return = [
            _FakeDTO({"username": "alice", "count": 6}),
        ]
        h = _FakeHandler(path="/api/security/concurrent?threshold=3")
        helper.dispatch(h, "/api/security/concurrent")
        self.assertEqual(h.status, 200)
        self.assertEqual(deps["reports"].received_kwargs["threshold"], 3)

    def test_invalid_threshold_is_400(self):
        helper, _ = _build_helper()
        h = _FakeHandler(path="/api/security/concurrent?threshold=abc")
        helper.dispatch(h, "/api/security/concurrent")
        self.assertEqual(h.status, 400)


# ---------------------------------------------------------------------------
# Bans
# ---------------------------------------------------------------------------


class TestUserBans(unittest.TestCase):

    def test_admin_gets_list(self):
        helper, deps = _build_helper()
        deps["bans"].user_bans = [
            _FakeDTO({"username": "baduser", "reason": "credential_stuffing"}),
        ]
        h = _FakeHandler(path="/api/bans/users")
        helper.dispatch(h, "/api/bans/users")
        self.assertEqual(h.status, 200)
        self.assertEqual(len(h.body["bans"]), 1)
        self.assertFalse(deps["bans"].last_user_kwargs["include_expired"])

    def test_include_expired_flag_forwarded(self):
        helper, deps = _build_helper()
        h = _FakeHandler(path="/api/bans/users?include_expired=1")
        helper.dispatch(h, "/api/bans/users")
        self.assertTrue(deps["bans"].last_user_kwargs["include_expired"])

    def test_non_admin_gets_403(self):
        helper, _ = _build_helper(
            actor=Actor(username="alice", is_admin=False),
        )
        h = _FakeHandler(path="/api/bans/users")
        helper.dispatch(h, "/api/bans/users")
        self.assertEqual(h.status, 403)

    def test_anonymous_gets_401(self):
        helper, _ = _build_helper(actor=Actor.anonymous())
        h = _FakeHandler(path="/api/bans/users")
        helper.dispatch(h, "/api/bans/users")
        self.assertEqual(h.status, 401)

    def test_empty_list_shape(self):
        helper, _ = _build_helper()
        h = _FakeHandler(path="/api/bans/users")
        helper.dispatch(h, "/api/bans/users")
        self.assertEqual(h.body, {"bans": []})


class TestIpBans(unittest.TestCase):

    def test_admin_gets_list(self):
        helper, deps = _build_helper()
        deps["bans"].ip_bans = [_FakeDTO({"cidr": "10.0.0.0/24"})]
        h = _FakeHandler(path="/api/bans/ips")
        helper.dispatch(h, "/api/bans/ips")
        self.assertEqual(h.status, 200)
        self.assertEqual(h.body["bans"][0]["cidr"], "10.0.0.0/24")

    def test_non_admin_gets_403(self):
        helper, _ = _build_helper(
            actor=Actor(username="alice", is_admin=False),
        )
        h = _FakeHandler(path="/api/bans/ips")
        helper.dispatch(h, "/api/bans/ips")
        self.assertEqual(h.status, 403)


# ---------------------------------------------------------------------------
# Audit log head
# ---------------------------------------------------------------------------


class TestAuditLogHead(unittest.TestCase):

    def test_admin_gets_head(self):
        helper, deps = _build_helper()
        deps["audit"].head_return = {
            "height": 42, "hash": "abc", "ts": "2026-01-02T03:04:05+00:00",
            "ok": True,
        }
        h = _FakeHandler(path="/api/audit-log/head")
        helper.dispatch(h, "/api/audit-log/head")
        self.assertEqual(h.status, 200)
        self.assertEqual(h.body["height"], 42)
        self.assertEqual(h.body["hash"], "abc")
        self.assertTrue(h.body["ok"])

    def test_non_admin_gets_403(self):
        helper, _ = _build_helper(
            actor=Actor(username="x", is_admin=False),
        )
        h = _FakeHandler(path="/api/audit-log/head")
        helper.dispatch(h, "/api/audit-log/head")
        self.assertEqual(h.status, 403)


# ---------------------------------------------------------------------------
# Self-service endpoints
# ---------------------------------------------------------------------------


class TestMySessions(unittest.TestCase):

    def test_authenticated_caller_gets_their_sessions(self):
        helper, deps = _build_helper(
            actor=Actor(username="alice", is_admin=False),
        )
        deps["agg"].list_for_user_return = [
            _FakeDTO({"provider": "controller", "session_id": "s1",
                      "username": "alice"}),
        ]
        h = _FakeHandler(path="/api/me/sessions")
        helper.dispatch(h, "/api/me/sessions")
        self.assertEqual(h.status, 200)
        self.assertEqual(len(h.body["sessions"]), 1)
        self.assertIn("current_session_id", h.body)

    def test_empty_aggregate_synthesises_caller_session(self):
        # Mirrors the SSO-empty fallback on /api/sessions/active: when
        # the cross-provider aggregate is empty for the caller (the
        # SSO case where the controller has no native row and the
        # provider impls all degrade to []) we surface the operator's
        # own identity as a synthetic row so the /me page doesn't
        # render the misleading "No active sessions" empty state while
        # they're staring at it.
        helper, deps = _build_helper(
            actor=Actor(
                username="alice", is_admin=False,
                client_ip="203.0.113.10",
                user_agent="Mozilla/5.0 (X11; Linux x86_64)",
            ),
        )
        deps["agg"].list_for_user_return = []
        h = _FakeHandler(path="/api/me/sessions")
        helper.dispatch(h, "/api/me/sessions")
        self.assertEqual(h.status, 200)
        sessions = h.body["sessions"]
        self.assertEqual(len(sessions), 1)
        synth = sessions[0]
        self.assertEqual(synth["username"], "alice")
        self.assertEqual(synth["client_ip"], "203.0.113.10")
        self.assertFalse(synth["revokable"])
        self.assertEqual(synth["session_id"], "")
        self.assertEqual(h.body["current_session_id"], "")

    def test_unauthenticated_gets_401(self):
        helper, _ = _build_helper(actor=Actor.anonymous())
        h = _FakeHandler(path="/api/me/sessions")
        helper.dispatch(h, "/api/me/sessions")
        self.assertEqual(h.status, 401)


class TestMyTokens(unittest.TestCase):

    def test_caller_gets_only_their_own_tokens(self):
        helper, deps = _build_helper(
            actor=Actor(username="alice", is_admin=False),
        )
        deps["tokens"].tokens = [
            _FakeToken(id="t1", owner_username="alice", name="laptop"),
            _FakeToken(id="t2", owner_username="bob", name="nope"),
        ]
        h = _FakeHandler(path="/api/me/tokens")
        helper.dispatch(h, "/api/me/tokens")
        self.assertEqual(h.status, 200)
        owners = {t["owner_username"] for t in h.body["tokens"]}
        self.assertEqual(owners, {"alice"})

    def test_no_token_hash_field_in_response(self):
        """SECURITY: the response MUST NOT include ``token_hash`` — only
        metadata is surfaced to the caller."""
        helper, deps = _build_helper(
            actor=Actor(username="alice", is_admin=False),
        )
        deps["tokens"].tokens = [_FakeToken(id="t1", owner_username="alice")]
        h = _FakeHandler(path="/api/me/tokens")
        helper.dispatch(h, "/api/me/tokens")
        for tok in h.body["tokens"]:
            self.assertNotIn("token_hash", tok)

    def test_anonymous_gets_401(self):
        helper, _ = _build_helper(actor=Actor.anonymous())
        h = _FakeHandler(path="/api/me/tokens")
        helper.dispatch(h, "/api/me/tokens")
        self.assertEqual(h.status, 401)

    def test_legacy_dataclass_repr_owner_is_surfaced(self):
        """Legacy regression: tokens minted before the
        ``handlers_post.token_create`` Bug-1 fix have a corrupted
        ``owner_username`` like ``"Actor(username='alice', ...)"``.
        The GET handler must still surface them to the rightful owner
        instead of pretending they don't exist — otherwise the
        operator sees ``tokens count = 0`` even after issuing.
        """
        legacy_repr = (
            "Actor(username='alice', roles=frozenset({'admin'}), "
            "is_admin=True, is_system=False, session_id=None, "
            "source_provider='controller', is_impersonating=None, "
            "client_ip='10.0.0.1', user_agent='curl')"
        )
        helper, deps = _build_helper(
            actor=Actor(username="alice", is_admin=False),
        )
        # ``_FakeTokenStore.list_all`` returns every token when no
        # owner is supplied, mirroring the real ``ApiTokenStore``.
        store = _LegacyAwareTokenStore()
        store.tokens = [
            _FakeToken(id="legacy", owner_username=legacy_repr, name="ci"),
            _FakeToken(id="other", owner_username=(
                "Actor(username='bob', is_admin=False)"
            )),
        ]
        helper._token_store_getter = lambda: store
        h = _FakeHandler(path="/api/me/tokens")
        helper.dispatch(h, "/api/me/tokens")
        self.assertEqual(h.status, 200)
        ids = {t["id"] for t in h.body["tokens"]}
        self.assertIn("legacy", ids, "alice's legacy token must surface")
        self.assertNotIn("other", ids, "bob's legacy token must NOT leak")


class _LegacyAwareTokenStore:
    """Token-store stub whose ``list_all()`` mirrors the real
    ``ApiTokenStore``: no arg → return everything; explicit owner →
    exact-match filter. The base ``_FakeTokenStore`` short-circuits
    the empty-owner case to ``[]``, which masks the legacy-repr
    fallback path under test.
    """

    def __init__(self) -> None:
        self.tokens: list = []

    def list_all(self, owner_username: str = ""):
        if not owner_username:
            return list(self.tokens)
        return [t for t in self.tokens if t.owner_username == owner_username]


class TestMyMfaState(unittest.TestCase):

    def test_happy_path(self):
        mfa = _FakeMFAState(
            enrolled=True, enrolled_methods=("totp",),
            last_used_method="totp", last_used_at="2026-01-02T00:00:00Z",
            required=False,
        )
        helper, _ = _build_helper(
            actor=Actor(username="alice", is_admin=False), mfa=mfa,
        )
        h = _FakeHandler(path="/api/me/mfa-state")
        helper.dispatch(h, "/api/me/mfa-state")
        self.assertEqual(h.status, 200)
        self.assertTrue(h.body["enrolled"])
        self.assertEqual(h.body["enrolled_methods"], ["totp"])
        self.assertEqual(h.body["required"], False)

    def test_empty_state_for_non_enrolled(self):
        helper, _ = _build_helper(
            actor=Actor(username="alice", is_admin=False),
        )
        h = _FakeHandler(path="/api/me/mfa-state")
        helper.dispatch(h, "/api/me/mfa-state")
        self.assertEqual(h.body["enrolled"], False)
        self.assertEqual(h.body["enrolled_methods"], [])

    def test_anonymous_gets_401(self):
        helper, _ = _build_helper(actor=Actor.anonymous())
        h = _FakeHandler(path="/api/me/mfa-state")
        helper.dispatch(h, "/api/me/mfa-state")
        self.assertEqual(h.status, 401)


class TestMyLoginHistory(unittest.TestCase):

    def test_happy_path_scoped_to_self(self):
        helper, deps = _build_helper(
            actor=Actor(username="alice", is_admin=False),
        )
        deps["reports"].login_history_return = [
            {"action": "login_success", "target": "alice"},
        ]
        h = _FakeHandler(path="/api/me/login-history?limit=10")
        helper.dispatch(h, "/api/me/login-history")
        self.assertEqual(h.status, 200)
        self.assertEqual(deps["reports"].received_kwargs["username"], "alice")
        self.assertEqual(deps["reports"].received_kwargs["limit"], 10)

    def test_empty_entries_shape(self):
        helper, _ = _build_helper(
            actor=Actor(username="alice", is_admin=False),
        )
        h = _FakeHandler(path="/api/me/login-history")
        helper.dispatch(h, "/api/me/login-history")
        self.assertEqual(h.body, {"entries": []})

    def test_malformed_limit_is_400(self):
        helper, _ = _build_helper(
            actor=Actor(username="alice", is_admin=False),
        )
        h = _FakeHandler(path="/api/me/login-history?limit=abc")
        helper.dispatch(h, "/api/me/login-history")
        self.assertEqual(h.status, 400)

    def test_anonymous_gets_401(self):
        helper, _ = _build_helper(actor=Actor.anonymous())
        h = _FakeHandler(path="/api/me/login-history")
        helper.dispatch(h, "/api/me/login-history")
        self.assertEqual(h.status, 401)


# ---------------------------------------------------------------------------
# Rate-limit gate (api/routes/sessions_security_get.py)
# ---------------------------------------------------------------------------


def _build_security_read_gate():
    """Construct a fresh ``_SecurityReadGate`` per test so bucket
    state from one case never leaks into another. The gate's
    ``_DEFAULT_CAPACITY`` / ``_DEFAULT_REFILL_PER_SECOND`` mirror
    the legacy ``handlers_get._security_read_limiter`` parameters
    verbatim."""
    from media_stack.api.routes.sessions_security_get import (
        _SecurityReadGate,
    )
    return _SecurityReadGate()


class TestSecurityReadLimiter(unittest.TestCase):
    """The security-read bucket now lives on
    ``_SecurityReadGate._limiter`` (lifted from ``handlers_get.py``
    during ADR-0007 Phase 2 Phase E). Drain the limiter directly to
    pin the same per-client capacity / per-bucket isolation behavior
    the legacy bucket guaranteed."""

    def setUp(self) -> None:
        self.gate = _build_security_read_gate()
        self.capacity = self.gate._DEFAULT_CAPACITY
        self.bucket = self.gate._BUCKET_NAME

    def test_bucket_allows_up_to_capacity(self):
        allowed_count = 0
        for _ in range(self.capacity):
            if self.gate._limiter.allow(
                client_id="127.0.0.1", bucket=self.bucket,
            ):
                allowed_count += 1
        self.assertEqual(allowed_count, self.capacity)

    def test_bucket_denies_after_capacity(self):
        for _ in range(self.capacity):
            self.gate._limiter.allow(
                client_id="192.0.2.1", bucket=self.bucket,
            )
        # One more should be denied.
        self.assertFalse(self.gate._limiter.allow(
            client_id="192.0.2.1", bucket=self.bucket,
        ))

    def test_bucket_is_per_client(self):
        for _ in range(self.capacity):
            self.gate._limiter.allow(
                client_id="203.0.113.1", bucket=self.bucket,
            )
        # A different IP still has its own credit line.
        self.assertTrue(self.gate._limiter.allow(
            client_id="203.0.113.2", bucket=self.bucket,
        ))


class TestSecurityReadLimiterIntegration(unittest.TestCase):
    """Simulate the dispatcher: 429 is returned for the last admin
    read once the bucket is drained."""

    def setUp(self) -> None:
        self.gate = _build_security_read_gate()

    def test_429_when_bucket_exhausted_for_admin_read(self):
        # Drain the bucket for our synthetic client.
        for _ in range(self.gate._DEFAULT_CAPACITY):
            self.gate._limiter.allow(
                client_id="10.10.10.10", bucket=self.gate._BUCKET_NAME,
            )
        # Ask once more — should be denied.
        ok = self.gate._limiter.allow(
            client_id="10.10.10.10", bucket=self.gate._BUCKET_NAME,
        )
        self.assertFalse(ok)


# ---------------------------------------------------------------------------
# Route table + dispatch
# ---------------------------------------------------------------------------


class TestDispatchRouting(unittest.TestCase):

    def test_unknown_path_returns_404(self):
        helper, _ = _build_helper()
        h = _FakeHandler(path="/api/sessions/unknown")
        helper.dispatch(h, "/api/sessions/unknown")
        self.assertEqual(h.status, 404)

    def test_user_login_history_parametric_route_matches(self):
        helper, deps = _build_helper()
        h = _FakeHandler(path="/api/users/charlie/login-history")
        helper.dispatch(h, "/api/users/charlie/login-history")
        self.assertEqual(h.status, 200)
        self.assertEqual(deps["reports"].received_kwargs["username"], "charlie")

    def test_user_login_history_rejects_slashes_in_user_id(self):
        """A nested path (e.g. ``/api/users/a/b/login-history``) should
        not be accepted as a valid user_id because ``/`` inside the
        middle segment is ambiguous."""
        helper, _ = _build_helper()
        h = _FakeHandler(path="/api/users/foo/bar/login-history")
        helper.dispatch(h, "/api/users/foo/bar/login-history")
        self.assertEqual(h.status, 404)

    def test_route_table_has_all_admin_endpoints(self):
        helper, _ = _build_helper()
        table = helper._route_table()
        self.assertIn("/api/sessions/active", table)
        self.assertIn("/api/security/failed-logins", table)
        self.assertIn("/api/security/new-locations", table)
        self.assertIn("/api/security/concurrent", table)
        self.assertIn("/api/bans/users", table)
        self.assertIn("/api/bans/ips", table)
        self.assertIn("/api/audit-log/head", table)

    def test_route_table_has_all_self_service_endpoints(self):
        helper, _ = _build_helper()
        table = helper._route_table()
        self.assertIn("/api/me/sessions", table)
        self.assertIn("/api/me/tokens", table)
        self.assertIn("/api/me/mfa-state", table)
        self.assertIn("/api/me/login-history", table)


# ---------------------------------------------------------------------------
# Internal-error safety net
# ---------------------------------------------------------------------------


class TestInternalErrorSafety(unittest.TestCase):

    def test_unexpected_exception_becomes_500_not_bubble(self):
        helper, deps = _build_helper()
        deps["reports"].raise_exc = RuntimeError("boom")
        h = _FakeHandler(path="/api/security/failed-logins")
        helper.dispatch(h, "/api/security/failed-logins")
        self.assertEqual(h.status, 500)
        self.assertEqual(h.body["error"], "internal_error")


# ---------------------------------------------------------------------------
# Admin-endpoint wiring on api/routes/sessions_security_get.py
# (limiter + dispatch glue — lifted from handlers_get.py)
# ---------------------------------------------------------------------------


class TestHandlersGetWiring(unittest.TestCase):
    """Confirm the route module on ``sessions_security_get.py``
    routes admin-read paths through the gate + helper without
    importing it blindly. Mirrors the legacy
    ``handlers_get._SECURITY_READ_PATHS`` / ``_sessviz_handler``
    wiring after the Phase E retirement."""

    def test_security_read_paths_set_is_complete(self):
        from media_stack.api.routes.sessions_security_get import (
            _SECURITY_READ_PATHS,
        )
        expected = {
            "/api/sessions/active",
            "/api/audit-log/head",
            "/api/bans/users",
            "/api/bans/ips",
            "/api/security/failed-logins",
            "/api/security/new-locations",
            "/api/security/concurrent",
        }
        self.assertEqual(set(_SECURITY_READ_PATHS), expected)

    def test_dispatcher_singleton_present(self):
        # The session-visibility helper is now reached via the
        # ``_SessionsViewerAdapter`` that the route module
        # constructs by default — verify the adapter exists and has
        # the ``dispatch`` entry-point the legacy
        # ``_sessviz_handler`` exposed.
        from media_stack.api.routes.sessions_security_get import (
            _SessionsViewerAdapter,
        )
        adapter = _SessionsViewerAdapter()
        self.assertIsNotNone(adapter)
        self.assertTrue(hasattr(adapter, "dispatch"))


if __name__ == "__main__":
    unittest.main()
