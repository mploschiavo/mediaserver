"""POST route handlers — extracted from ControllerAPIHandler.do_POST().

Every public function receives the ControllerAPIHandler instance as its
first argument so it can call response helpers and access ``self.state``.
"""

from __future__ import annotations


from media_stack.core.logging_utils import log_swallowed
import base64
import ipaddress
import json as _dumps_mod
import logging
import os
import socket
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from media_stack.api.actor_resolver import ActorResolver as _ActorResolver
from media_stack.api.session_singletons import (
    SESSION_COOKIE_NAME,
    session_store as _session_store,
    trusted_proxy_auth as _trusted_proxy_auth,
)
from media_stack.core.observability.security_counters import security_counters

try:
    from media_stack.cli.commands import generate_envoy_config_main as _envoy_gen_main
except ImportError:
    _envoy_gen_main = None
from media_stack.api.tls_factory import build_default_tls_service
from media_stack.core.edge.tls_certificate_service import (
    TlsCertificateServiceError,
)

_dumps = _dumps_mod.dumps

from media_stack.core.auth.authz import Actor
from media_stack.core.auth.csrf import CsrfProtector
from media_stack.core.auth.rate_limiter import RateLimiter
from media_stack.core.auth.users.audit_actions import (
    LOGIN_BLOCKED,
    LOGIN_FAILURE,
    LOGIN_RATE_LIMITED,
    LOGIN_SUCCESS,
    LOGOUT,
)
from media_stack.core.auth.users.models import UserState
from media_stack.core.auth.users.safe_yaml_edit import SafeYamlEditor
from media_stack.core.auth.users.user_service import UserServiceError
from media_stack.core.auth.users.user_service_factory import (
    build_default_api_token_store,
    build_default_auth_verifier,
    build_default_invite_service,
    build_default_service,
    resolve_default_roles_path,
)

# Global per-IP rate limit applied to EVERY POST. Wider bucket than the
# user-mgmt one because it covers all mutating traffic, not just
# sensitive ops. Anyone exceeding this is clearly not a human.
_GLOBAL_POST_CAPACITY = 30
_GLOBAL_POST_REFILL = 3.0  # 3 tokens/sec sustained
_global_post_limiter = RateLimiter(
    capacity=_GLOBAL_POST_CAPACITY,
    refill_per_second=_GLOBAL_POST_REFILL,
)
_USER_MGMT_BUCKET_CAPACITY = 10
_USER_MGMT_REFILL_PER_SECOND = 1.0
_user_mgmt_limiter = RateLimiter(
    capacity=_USER_MGMT_BUCKET_CAPACITY,
    refill_per_second=_USER_MGMT_REFILL_PER_SECOND,
)
# Per-account rate limit for password reset — prevents an attacker from
# brute-forcing reset endpoints by rotating IPs.
_PW_RESET_BUCKET_CAPACITY = 3
_PW_RESET_REFILL_PER_SECOND = 0.05  # ~1 token per 20s = slow, deliberate
_pw_reset_limiter = RateLimiter(
    capacity=_PW_RESET_BUCKET_CAPACITY,
    refill_per_second=_PW_RESET_REFILL_PER_SECOND,
)
_csrf = CsrfProtector()
# CSRF is ON by default for BROWSER requests (detected via Cookie
# header). API clients using basic auth WITHOUT a Cookie header are
# exempt — they can't be CSRF'd because the attacker can't set the
# basic-auth header cross-origin. CSRF_ENFORCE=1 forces it on for
# everyone (including API clients); CSRF_ENFORCE=0 disables it entirely.
_CSRF_MODE = (os.getenv("CSRF_ENFORCE", "") or "").strip()
_CSRF_DEFAULT_ON_FOR_BROWSERS = _CSRF_MODE != "0"
# _CSRF_ENFORCE=True forces strict CSRF for every request (including
# header-less API clients). This is what the test suite patches to
# verify strict-mode behavior. With it False (default), we fall back to
# the smart default: strict for browsers (Cookie header present),
# exempt for API clients.
_CSRF_ENFORCE = _CSRF_MODE == "1"

from .services import admin as admin_svc
from .services import config as config_svc
from .services import disk as disk_svc
from .services import health as health_svc
from .services import ops as ops_svc
from .services.media_integrity_handlers import (
    _instance as _media_integrity_handlers,
)
from .services.security_post_handlers import (
    _security_post_handlers,
)

if TYPE_CHECKING:
    from .server import ControllerAPIHandler

logger = logging.getLogger("controller_api")

_ERR_LEN = 99


class _HandlerActorResolverFactory:
    """Lazy wrapper so the test harness's ``patch(...)`` of
    ``build_default_service`` / ``_trusted_proxy_auth`` still takes
    effect. A bare ``ActorResolver(build_service=build_default_service)``
    captures the import-time name and sails past the patched symbol.
    """

    def resolve(self, handler, body: dict) -> Actor:
        impl = _ActorResolver(
            build_service=build_default_service,
            client_ip_for=_trusted_proxy_auth.client_ip,
        )
        return impl.resolve(handler, body or {})


_actor_resolver = _HandlerActorResolverFactory()


class _WebhookUrlValidator:
    """Reject webhook URLs that could be used for SSRF.

    Blocks private, loopback, link-local, multicast, and reserved IP
    ranges so an attacker can't add cloud-metadata endpoints (e.g. the
    169.254.x.x link-local range), the controller itself via 127.0.0.1,
    or in-cluster service IPs as webhook targets. DNS resolution is
    performed against every address the hostname maps to, defeating DNS
    rebinding where a public hostname points at an internal IP.
    """

    _INVALID_SCHEME_MSG = "Invalid webhook URL — must be http:// or https://"

    def validate(self, url: str) -> str | None:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return self._INVALID_SCHEME_MSG
        hostname = parsed.hostname or ""
        if not hostname:
            return "Invalid webhook URL — missing hostname"
        try:
            infos = socket.getaddrinfo(hostname, None)
        except socket.gaierror:
            return f"webhook URL hostname does not resolve: {hostname}"
        for info in infos:
            addr = info[4][0]
            try:
                ip = ipaddress.ip_address(addr)
            except ValueError:
                continue
            if (ip.is_private or ip.is_loopback or ip.is_link_local
                    or ip.is_multicast or ip.is_reserved or ip.is_unspecified):
                return (f"webhook URL resolves to a blocked address ({addr}); "
                        "private, loopback, link-local, and multicast ranges "
                        "are not allowed")
        return None


_webhook_url_validator = _WebhookUrlValidator()


class _WebhookHmacVerifier:
    """Verifies GitHub-style ``X-Hub-Signature-256: sha256=<hex>`` on
    incoming webhooks.

    Behaviour:
      - ``WEBHOOK_HMAC_SECRET`` unset → pass-through (backward compat).
      - secret set, header missing  → reject.
      - secret set, header present  → constant-time compare. Mismatch → reject.

    The verifier consumes the request body once and returns both the
    parsed JSON AND a boolean for the signature check so the caller
    can short-circuit before running any side-effects.
    """

    _HEADER = "X-Hub-Signature-256"

    def __init__(self) -> None:
        self._env = os.environ

    def verify_and_parse(self, handler) -> tuple[dict, bool]:
        secret = self._env.get("WEBHOOK_HMAC_SECRET", "").strip()
        if not secret:
            # No secret configured — fall through to the handler's normal
            # JSON-body reader. This preserves backward compatibility with
            # tests/scripts that mock _read_json_body(), and avoids
            # touching rfile unless HMAC is actually required.
            return handler._read_json_body() or {}, True
        raw = self._read_raw_body(handler)
        signature_ok = self._verify_signature(handler, raw, secret)
        if not signature_ok:
            security_counters.incr("hmac_fail")
            return {}, False
        return self._parse_json(raw), True

    def _read_raw_body(self, handler) -> bytes:
        length = int(handler.headers.get("Content-Length", 0) or 0)
        if length <= 0:
            return b""
        length = min(length, 1 * (2 ** 20))  # cap at 1 MiB like _read_json_body
        try:
            return handler.rfile.read(length)
        except Exception as exc:  # noqa: BLE001
            logging.getLogger("media_stack").debug(
                "[DEBUG] webhook body read failed: %s", exc,
            )
            return b""

    def _verify_signature(self, handler, body: bytes, secret: str) -> bool:
        import hmac
        import hashlib
        header_val = ""
        try:
            header_val = handler.headers.get(self._HEADER, "") or ""
        except AttributeError:
            return False
        if not header_val.lower().startswith("sha256="):
            return False
        provided = header_val.split("=", 1)[1].strip()
        expected = hmac.new(
            secret.encode("utf-8"), body, hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(provided, expected)

    def _parse_json(self, raw: bytes) -> dict:
        if not raw:
            return {}
        try:
            return _dumps_mod.loads(raw)
        except (ValueError, TypeError):
            return {}


_webhook_hmac_verifier = _WebhookHmacVerifier()
_read_body_with_hmac = _webhook_hmac_verifier.verify_and_parse


class _SessionLoginHelper:
    """Handles the cookie-login flow.

    POST /api/auth/login takes {"username": ..., "password": ...} and,
    on a valid credential (via BasicAuthVerifier), mints an opaque
    session token. The token is returned as a Set-Cookie header
    (``ms_session=...; HttpOnly; Secure; SameSite=Strict; Path=/``).

    POST /api/auth/logout reads the ms_session cookie and revokes it.

    Every branch — success, bad-credentials, missing fields, rate
    limited, IP/user banned (when a BanStore is wired) — writes an
    entry to the hash-chained audit log. Forensic playback of a
    break-in hinges on these entries existing; without them, only
    the winning request was recorded, not the 10 000 brute-force
    attempts that preceded it.
    """

    def __init__(self, *, ban_store=None) -> None:
        self._env = os.environ
        # BanStore integration is a follow-up (tracked in
        # docs/security-a11y-contract.md). When present, an IP or
        # username listed as banned short-circuits the login with a
        # LOGIN_BLOCKED audit entry. When ``None``, the check is a
        # no-op. The injected object is expected to expose
        # ``is_ip_banned(ip) -> bool`` and ``is_user_banned(u) -> bool``.
        self._ban_store = ban_store

    def login(self, handler) -> None:
        body = handler._read_json_body() or {}
        username = str(body.get("username", "")).strip()
        password = str(body.get("password", ""))
        if not username or not password:
            # Missing fields aren't strictly a "login failure" in the
            # credential sense but are still a signal of form-tampering
            # / malformed client code. Record as LOGIN_FAILURE with a
            # reason so a dashboard can split them.
            self._audit_login_event(
                handler, LOGIN_FAILURE, username=username,
                reason="missing_fields",
            )
            handler._json_response(
                HTTPStatus.BAD_REQUEST,
                {"error": "username and password required"},
            )
            return
        if self._ban_hit(username, handler):
            self._audit_login_event(
                handler, LOGIN_BLOCKED, username=username,
                reason="ban_list",
            )
            handler._json_response(
                HTTPStatus.FORBIDDEN,
                {"error": "login blocked"},
            )
            return
        if not self._verify_credentials(username, password):
            self._audit_login_event(
                handler, LOGIN_FAILURE, username=username,
                reason="bad_credentials",
            )
            handler._json_response(
                HTTPStatus.UNAUTHORIZED,
                {"error": "invalid credentials"},
            )
            return
        _sess, plaintext = _session_store.create(owner_username=username)
        self._audit_login_event(
            handler, LOGIN_SUCCESS, username=username,
            reason="cookie_mint",
        )
        self._send_cookie_response(handler, plaintext, expires=False)

    def logout(self, handler) -> None:
        cookie_raw = ""
        headers = getattr(handler, "headers", None)
        if headers is not None:
            try:
                cookie_raw = headers.get("Cookie", "") or ""
            except AttributeError:
                cookie_raw = ""
        revoked_for: str = ""
        for chunk in cookie_raw.split(";"):
            k, _, v = chunk.strip().partition("=")
            if k == SESSION_COOKIE_NAME and v:
                token = v.strip()
                # Resolve the owner BEFORE revoking so the audit
                # entry can name the account even though ``revoke``
                # returns only a bool.
                try:
                    sess = _session_store.get(token)
                    if sess is not None:
                        revoked_for = str(
                            getattr(sess, "owner_username", "") or ""
                        )
                except Exception:  # noqa: BLE001
                    pass
                _session_store.revoke(token)
        self._audit_login_event(
            handler, LOGOUT, username=revoked_for, reason="cookie_revoke",
        )
        self._send_cookie_response(handler, "", expires=True)

    def _ban_hit(self, username: str, handler) -> bool:
        """True when the BanStore marks this IP or user as banned.

        Without a BanStore wired in (the default), always returns
        False — the upstream IP-lockout tracker still runs as a
        coarser defence.
        """
        store = self._ban_store
        if store is None:
            return False
        try:
            ip = self._client_ip(handler)
            if ip and getattr(store, "is_ip_banned", lambda _: False)(ip):
                return True
            if username and getattr(store, "is_user_banned", lambda _: False)(username):
                return True
        except Exception as exc:  # noqa: BLE001
            logging.getLogger("media_stack").debug(
                "[DEBUG] BanStore check raised: %s", exc,
            )
        return False

    def _audit_login_event(
        self, handler, action: str,
        *, username: str, reason: str,
    ) -> None:
        """Write an AuthEvent to the hash-chained audit log.

        Safe to call before / during / after the cookie mint. Never
        raises — an audit-log outage must not be able to block a
        login from going through."""
        try:
            svc = build_default_service()
        except Exception as exc:  # noqa: BLE001
            logging.getLogger("media_stack").debug(
                "[DEBUG] audit_login_event: service build: %s", exc,
            )
            return
        try:
            actor_label = username if (
                action == LOGIN_SUCCESS and username
            ) else (username or "anonymous")
            svc._audit.append(
                actor=actor_label,
                action=action,
                target=username or "unknown",
                result="ok" if action == LOGIN_SUCCESS else "fail",
                ip=self._client_ip(handler),
                user_agent=self._user_agent(handler),
                detail={"reason": reason, "provider": "controller"},
            )
        except Exception as exc:  # noqa: BLE001
            logging.getLogger("media_stack").debug(
                "[DEBUG] audit_login_event: append raised: %s", exc,
            )

    @staticmethod
    def _client_ip(handler) -> str:
        """Real client IP for the login audit + ban paths.

        Delegates to ``trusted_proxy_auth.client_ip`` so a login that
        arrives via Envoy/Authelia gets banned at the attacker's IP,
        not at the proxy hop. See
        ``session_singletons.TrustedProxyAuth.client_ip`` for the
        exact resolution rules and strict-fallback semantics.
        """
        try:
            return _trusted_proxy_auth.client_ip(handler)
        except Exception:  # noqa: BLE001
            return ""

    @staticmethod
    def _user_agent(handler) -> str:
        try:
            return str(handler.headers.get("User-Agent", "") or "")
        except Exception:  # noqa: BLE001
            return ""

    def _verify_credentials(self, username: str, password: str) -> bool:
        """Check the username/password against the controller user store
        (store-backed first, then env-var fallback). Returns True on a
        successful match."""
        try:
            verifier = build_default_auth_verifier()
        except Exception as exc:  # noqa: BLE001
            logging.getLogger("media_stack").debug(
                "[DEBUG] _verify_credentials: verifier build: %s", exc,
            )
            verifier = None
        if verifier is not None:
            try:
                if verifier.verify(username, password):
                    return True
            except Exception as exc:  # noqa: BLE001
                logging.getLogger("media_stack").debug(
                    "[DEBUG] _verify_credentials: verify raised: %s", exc,
                )
        fb_user = self._env.get("STACK_ADMIN_USERNAME", "admin")
        fb_pass = self._env.get("STACK_ADMIN_PASSWORD", "")
        return bool(fb_pass) and username == fb_user and password == fb_pass

    def _send_cookie_response(self, handler, plaintext: str, *,
                              expires: bool) -> None:
        if expires:
            cookie = (f"{SESSION_COOKIE_NAME}=; HttpOnly; Secure; "
                      "SameSite=Strict; Path=/; Max-Age=0")
            body_obj = {"logged_out": True}
        else:
            cookie = (f"{SESSION_COOKIE_NAME}={plaintext}; HttpOnly; "
                      "Secure; SameSite=Strict; Path=/")
            body_obj = {"session": "established"}
        payload = _dumps(body_obj).encode()
        handler.send_response(HTTPStatus.OK)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(payload)))
        handler.send_header("Set-Cookie", cookie)
        handler.end_headers()
        handler.wfile.write(payload)


_session_login_helper = _SessionLoginHelper()
_handle_login = _session_login_helper.login
_handle_logout = _session_login_helper.logout


class _TlsCertHandler:
    """Install / regenerate the edge TLS certificate used by Envoy.

    After a successful write we automatically trigger an Envoy reload
    so the new cert takes effect without the operator having to run
    a separate restart step. The reload is best-effort — its result
    is returned in the response so the UI can surface any failure.

    Both endpoints are mutating + sudo-gated upstream in server.py.
    """

    def __init__(self) -> None:
        self._env = os.environ

    def install(self, handler) -> None:
        body = handler._read_json_body() or {}
        cert_pem = str(body.get("cert_pem", "") or "").strip()
        key_pem = str(body.get("key_pem", "") or "").strip()
        if not cert_pem or not key_pem:
            handler._json_response(
                HTTPStatus.BAD_REQUEST,
                {"error": "cert_pem and key_pem required"},
            )
            return
        try:
            info = build_default_tls_service().install(cert_pem, key_pem)
        except TlsCertificateServiceError as exc:
            handler._json_response(
                HTTPStatus.BAD_REQUEST, {"error": str(exc)[:_ERR_LEN]},
            )
            return
        reload = self._reload_envoy()
        handler._json_response(HTTPStatus.OK, {
            "installed": True, "envoy_reload": reload, **info.to_dict(),
        })

    def regenerate(self, handler) -> None:
        body = handler._read_json_body() or {}
        hostnames = body.get("hostnames") or None
        days = int(body.get("days", 0) or 73 * 5)  # default 365
        try:
            info = build_default_tls_service().regenerate(
                hostnames=hostnames if isinstance(hostnames, list) else None,
                days=days,
            )
        except TlsCertificateServiceError as exc:
            handler._json_response(
                HTTPStatus.BAD_REQUEST, {"error": str(exc)[:_ERR_LEN]},
            )
            return
        reload = self._reload_envoy()
        handler._json_response(HTTPStatus.OK, {
            "regenerated": True, "envoy_reload": reload, **info.to_dict(),
        })

    def _reload_envoy(self) -> dict:
        """Regenerate the Envoy config + restart Envoy so the new
        cert is picked up. Both steps are best-effort.

        The regen step matters: the running Envoy may be holding a
        stale config generated before the cert existed (plain HTTP
        listener). Simply restarting Envoy would reload that same
        stale config. By regenerating first we guarantee the config
        on disk references the new cert before Envoy re-reads it.
        """
        regen_result = self._regenerate_envoy_config()
        try:
            restart = admin_svc.restart_service("envoy")
        except Exception as exc:  # noqa: BLE001
            logger.warning("TLS install: envoy restart failed: %s", exc)
            restart = {"status": "error",
                       "detail": str(exc)[:_ERR_LEN]}
        return {"regen": regen_result, **restart}

    def _regenerate_envoy_config(self) -> str:
        """Run the compose-path Envoy config generator in-process.

        Only meaningful on deployments that write to the envoy.yaml
        file Envoy reads (compose). In K8s the config comes from a
        ConfigMap; we skip gracefully when the runtime indicates K8s.
        """
        if _envoy_gen_main is None:
            return "skipped (generator module unavailable)"
        if self._env.get("K8S_NAMESPACE", "").strip():
            return "skipped (k8s runtime; config is ConfigMap-managed)"
        envoy_yaml_dir = Path(
            self._env.get("CONFIG_ROOT", "/srv-config")) / "envoy"
        if not envoy_yaml_dir.is_dir():
            return "skipped (no envoy config dir mounted)"
        try:
            _envoy_gen_main.main([])
            return "ok"
        except Exception as exc:  # noqa: BLE001
            logger.warning("TLS reload: envoy config regen failed: %s", exc)
            return f"error: {str(exc)[:_ERR_LEN]}"


_tls_handler = _TlsCertHandler()


# ---------------------------------------------------------------------------
# Known actions
# ---------------------------------------------------------------------------

# Core actions that aren't declared in any contract.
#
# Six actions used to live here too — post-setup, discover-indexers,
# restart-apps, push-indexers, envoy-config, validate-credentials —
# but they were migrated to the job framework so they appear in the
# Job tree alongside per-app jobs. They're now declared in
# ``contracts/services/core.yaml`` and discovered via
# ``discover_jobs_from_contracts``; ``_build_known_actions`` merges
# both sources, so they remain valid ``/actions/{name}`` targets.
#
# The three left here are orchestration entry points without a
# meaningful "phase" — declaring them as jobs would be busywork:
#   - bootstrap: the root of the tree (parent of every other job)
#   - configure-media-server: composite that runs every media-server
#     leaf job; parented in the tree as a phase group
#   - reconcile: re-runs the entire bootstrap pipeline; effectively
#     an alias for "bootstrap" with the cancel flag cleared
_CORE_ACTIONS = {
    "bootstrap", "reconcile", "configure-media-server",
}


# KNOWN_ACTIONS initialized after class (needs _build_known_actions)


# ---------------------------------------------------------------------------
# User-management helper (invites, bulk import, roles, session revocation)
# ---------------------------------------------------------------------------


class _UserMgmtPostHelper:
    """Extracted POST handlers for invites, bulk import, role editing, and
    cross-provider session revocation. Kept out of PostRequestHandler so
    the main request router stays under the class-method ratchet.
    """

    _ALLOWED_ROLE_FIELDS = frozenset({
        "name", "description", "sso_groups",
        "propagate_to_service_admins", "require_2fa",
        "controller_admin", "provider_payloads",
    })

    def token_create(self, body: dict, actor: str) -> dict:
        """Mint an API token.

        Two modes:
          - default ``kind=long_lived`` → one token, optional TTL.
          - ``kind=refresh_pair`` → mints an access+refresh pair with a
            shared family_id. Both plaintexts are returned once;
            subsequent requests use the access token, and the refresh
            exchanges at POST /api/tokens/refresh for a rotated pair.
        """
        store = build_default_api_token_store()
        owner = (str(body.get("owner_username", actor)).strip() or actor)
        name = str(body.get("name", "")).strip() or "api-token"
        scope = str(body.get("scope", "admin")).strip() or "admin"
        kind = str(body.get("kind", "long_lived")).strip()
        if kind == "refresh_pair":
            (access, a_plain), (refresh, r_plain) = store.mint_pair(
                owner_username=owner, name=name, scope=scope,
            )
            return {
                "access": {**access.to_dict(), "token": a_plain},
                "refresh": {**refresh.to_dict(), "token": r_plain},
            }
        ttl_seconds = int(body.get("ttl_seconds", 0) or 0)
        token, plaintext = store.create(
            owner_username=owner, name=name, scope=scope,
            ttl_seconds=max(0, ttl_seconds),
        )
        # Plaintext returned ONCE — never persisted, never logged.
        return {**token.to_dict(), "token": plaintext}

    def token_refresh(self, body: dict, actor: str) -> dict:
        """Exchange a refresh token for a rotated (access, refresh)
        pair. The old refresh is revoked in the same step — a replay
        of the old refresh returns an error (and the caller should
        treat that as a leak signal).
        """
        store = build_default_api_token_store()
        refresh_plain = str(body.get("refresh_token", "")).strip()
        if not refresh_plain:
            raise UserServiceError("refresh_token required")
        result = store.rotate(refresh_plain)
        if result is None:
            raise UserServiceError(
                "refresh token invalid, expired, or already rotated; "
                "sign in again",
            )
        (access, a_plain), (new_refresh, r_plain) = result
        return {
            "access": {**access.to_dict(), "token": a_plain},
            "refresh": {**new_refresh.to_dict(), "token": r_plain},
        }

    def token_revoke(self, path: str, actor: str) -> dict:
        token_id = path.rsplit("/", 1)[-1]
        ok = build_default_api_token_store().revoke(token_id)
        return {"token_id": token_id, "revoked": ok, "actor": actor}

    def token_family_revoke(self, body: dict, actor: str) -> dict:
        """Revoke every live token sharing the given family_id."""
        family_id = str(body.get("family_id", "")).strip()
        if not family_id:
            raise UserServiceError("family_id required")
        killed = build_default_api_token_store().revoke_family(family_id)
        return {"family_id": family_id, "revoked_count": killed,
                "actor": actor}

    def invite_create(self, body: dict, actor: str) -> dict:
        inv_svc = build_default_invite_service()
        return inv_svc.create_invite(
            email=str(body.get("email", "")).strip(),
            role_slug=str(body.get("role_slug", "")).strip(),
            ttl_hours=int(body.get("ttl_hours", 0) or 24),
            actor=actor,
        )

    def invite_accept(self, body: dict) -> dict:
        inv_svc = build_default_invite_service()
        return inv_svc.accept(
            token=str(body.get("token", "")),
            username=str(body.get("username", "")).strip(),
            display_name=str(body.get("display_name", "")).strip(),
            password=str(body.get("password", "")),
        )

    def invite_revoke(self, path: str, actor: str) -> dict:
        invite_id = path.rsplit("/", 1)[-1]
        return build_default_invite_service().revoke(invite_id, actor=actor)

    def bulk_import(self, svc, body: dict, actor: str) -> dict:
        rows = body.get("users") or []
        if not isinstance(rows, list):
            raise UserServiceError("users must be a list")
        imported: list[dict] = []
        errors: list[str] = []
        for row in rows:
            try:
                result = svc.create_user(
                    email=str(row.get("email", "")).strip(),
                    username=str(row.get("username", "")).strip(),
                    display_name=str(row.get("display_name", "")).strip(),
                    role_slug=str(row.get("role_slug", "adult")).strip() or "adult",
                    actor=actor,
                )
                row_out: dict = {
                    "email": result["email"],
                    "user_id": result["id"],
                }
                # Service emits the ticket fields; copy them per-row
                # so the caller's JSON has one retrieval handle per
                # imported account. Absent when an admin-supplied
                # password was provided for the row (no ticket needed).
                if "password_ticket" in result:
                    row_out["password_ticket"] = result["password_ticket"]
                if "ticket_expires_at" in result:
                    row_out["ticket_expires_at"] = result["ticket_expires_at"]
                imported.append(row_out)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{row.get('email', '')}: {str(exc)[:_ERR_LEN]}")
        return {"imported": imported, "errors": errors,
                "count": len(imported)}

    def role_update(self, path: str, body: dict, actor: str) -> dict:
        slug = path.rsplit("/", 1)[-1]
        if not slug:
            raise UserServiceError("role slug required")
        roles_path = self._resolve_roles_path()
        allowed = self._ALLOWED_ROLE_FIELDS

        def _mutator(current: dict) -> dict:
            roles = dict(current.get("roles") or {})
            existing = dict(roles.get(slug) or {})
            for k, v in (body or {}).items():
                if k in allowed:
                    existing[k] = v
            roles[slug] = existing
            new = dict(current)
            new["roles"] = roles
            return new

        SafeYamlEditor(roles_path).edit(_mutator)
        build_default_service()._roles.reload()
        return {"role": slug, "updated": True, "actor": actor}

    def revoke_sessions(self, svc, user_id: str, actor: str) -> dict:
        user = svc._store.get(user_id)
        if user is None:
            return {"user_id": user_id, "error": "not found"}
        results: dict[str, str] = {}
        for provider in svc._providers:
            results[provider.name] = self._revoke_on_provider(provider, user)
        svc._audit.append(
            actor=actor, action="revoke_sessions", target=user.email,
            result="ok", detail={"user_id": user_id, "providers": results},
        )
        return {"user_id": user_id, "providers": results}

    def _revoke_on_provider(self, provider, user) -> str:
        external_id = user.provider_refs.get(provider.name)
        if not external_id:
            return "no_ref"
        revoke = getattr(provider, "revoke_sessions", None)
        if revoke is None:
            return "unsupported"
        try:
            revoke(external_id)
            return "ok"
        except Exception as exc:  # noqa: BLE001
            return f"error: {str(exc)[:_ERR_LEN]}"

    def _resolve_roles_path(self) -> Path:
        return resolve_default_roles_path()


_user_mgmt_helper = _UserMgmtPostHelper()


def _strip_legacy_plaintext(result: dict | None) -> dict | None:
    """Belt-and-braces: swap any ``generated_password`` still present
    in a service result for a single-use retrieval ticket.

    The ticket swap happens inside ``UserWriteService`` now, so in
    production this function is a near-no-op. It's retained because
    (a) test fixtures commonly mock services to return the legacy
    shape — we want the HTTP response to honor the NEW contract
    regardless of the fixture — and (b) any future caller that
    accidentally falls back to the pre-migration shape gets
    automatically upgraded to a ticket instead of leaking plaintext.
    """
    if not isinstance(result, dict):
        return result
    plaintext = result.pop("generated_password", None)
    if plaintext:
        user_id = str(result.get("user_id") or result.get("id") or "")
        if user_id:
            from media_stack.core.auth.users.password_ticket_store import (
                mint_ticket_fields as _mint_ticket_fields,
            )
            result.update(_mint_ticket_fields(user_id, str(plaintext)))
    return result


# ---------------------------------------------------------------------------
# Main dispatcher
# ---------------------------------------------------------------------------

class PostRequestHandler:
    """Wraps POST request routing logic."""

    _CSRF_EXEMPT_POST_PATHS = frozenset({
        # Arr webhook is from trusted internal services with a shared
        # secret elsewhere; it has no Cookie header and doesn't need CSRF.
        "/webhooks/arr",
        # Login establishes the session; before it runs there's no
        # cookie to compare against, so CSRF can't apply.
        "/api/auth/login",
        # Logout is idempotent (just revokes the cookie); same reason.
        "/api/auth/logout",
        # Refresh token itself is the credential; programmatic clients
        # won't have a Cookie header to CSRF against.
        "/api/tokens/refresh",
    })

    def handle(self, handler: ControllerAPIHandler) -> None:  # noqa: C901
        """Route a POST request to the appropriate handler function."""

        # Apply rate-limit + CSRF to every mutating request before we
        # touch any business logic. This covers /api/rotate-keys,
        # /api/reset-password, /api/restart/*, /actions/*, /config,
        # /webhooks, and everything else — not just user-mgmt.
        if not self._global_preflight(handler):
            return

        # POST /api/auth/login — mint a session cookie
        if handler.path == "/api/auth/login":
            _handle_login(handler)
            return
        # POST /api/auth/logout — revoke the session cookie
        if handler.path == "/api/auth/logout":
            _handle_logout(handler)
            return
        # POST /api/tls/certificate — install a PEM cert+key bundle
        if handler.path == "/api/tls/certificate":
            _tls_handler.install(handler)
            return
        # POST /api/tls/certificate/regenerate — self-signed refresh
        if handler.path == "/api/tls/certificate/regenerate":
            _tls_handler.regenerate(handler)
            return

        # Session-visibility security POSTs: admin session revoke, user
        # / IP bans, emergency revoke, self-service endpoints. The
        # helper reads the ``Idempotency-Key`` header and threads it
        # into the BanStore + cache for retry-safety. Strip the query
        # string for route-matching (defensive — these endpoints do not
        # currently take query params, but the dispatch-strips-query
        # ratchet enforces consistency across every handler).
        _sec_path_clean = handler.path.split("?", 1)[0]
        if _security_post_handlers.matches(_sec_path_clean):
            self._handle_security_post(handler)
            return

        # Media-integrity POSTs: reconcile + enforce-config + resolve-review.
        # Admin-only. Strip the query string before route-matching:
        # ``reconcile?dry_run=1`` must match the same route as
        # ``reconcile``. The dry_run flag is parsed from the raw path.
        #
        # As of v1.0.184 these now route through ``JobRunner.run`` so
        # every invocation (manual or scheduled) lands in the unified
        # ``/api/jobs.history[]`` feed. The response shape is preserved
        # for backwards compat with UI v1.3.x — auth, idempotency, CSRF,
        # admin gating, and concurrency (409) still flow through
        # ``MediaIntegrityHandlers``; the wrapper shims the service
        # call onto ``run_job(...)`` for the happy path so history is
        # written exactly once.
        _mi_path_clean = handler.path.split("?", 1)[0]
        if _media_integrity_handlers.matches_post(_mi_path_clean):
            if not self._preflight(handler):
                return
            body = handler._read_json_body() or {}
            actor = _actor_resolver.resolve(handler, body)
            _dispatch_media_integrity_via_job(
                handler, handler.path, body, actor,
            )
            return

        # POST /run -- backward-compatible alias
        if handler.path == "/run":
            handler._handle_action("bootstrap")
            return

        # POST /api/stack/upgrade — trigger in-place compose upgrade.
        # Gated behind STACK_UPDATE_ALLOW_INPLACE on the controller
        # container; without that, the service returns
        # ``{accepted: false, error: ...}`` with instructions.
        if handler.path == "/api/stack/upgrade":
            from .services import stack_update as su_svc
            body = handler._read_json_body() or {}
            handler._json_response(
                200, su_svc.start_upgrade(body.get("target")),
            )
            return

        # POST /api/restart/{service}
        if handler.path.startswith("/api/restart/"):
            svc = handler.path[len("/api/restart/"):]
            from .services.registry import SERVICE_MAP
            if svc not in SERVICE_MAP and svc != "controller":
                handler._json_response(400, {"error": f"Unknown service '{svc}'", "known": sorted(SERVICE_MAP.keys())})
                return
            handler._json_response(200, admin_svc.restart_service(svc))
            return

        # POST /api/batch-restart
        if handler.path == "/api/batch-restart":
            body = handler._read_json_body()
            services = body.get("services", [])
            if not services:
                handler._json_response(400, {"error": "services list required"})
                return
            handler._json_response(200, admin_svc.batch_restart(services))
            return

        # POST /api/rotate-keys
        if handler.path == "/api/rotate-keys":
            body = handler._read_json_body() or {}
            target = body.get("services")  # optional list of service IDs
            handler._json_response(200, admin_svc.rotate_keys(target))
            return

        # POST /api/reset-password
        if handler.path == "/api/reset-password":
            body = handler._read_json_body()
            new_password = body.get("password", "")
            if not new_password or len(new_password) < 4:
                handler._json_response(400, {"error": "password field required (min 4 chars)"})
                return
            target = body.get("services")  # optional list of service IDs
            handler._json_response(200, admin_svc.reset_password(new_password, target))
            return

        # POST /api/credentials -- ad-hoc credential revalidation
        if handler.path == "/api/credentials":
            body = handler._read_json_body() or {}
            target = body.get("services")  # optional list of service IDs
            handler._json_response(200, health_svc.probe_credentials(target))
            return

        # POST /api/services/{id}/api-key -- manually set or discover a service API key
        if handler.path.startswith("/api/services/") and handler.path.endswith("/api-key"):
            _handle_service_api_key_post(handler)
            return

        # POST /api/services/{id}/reset -- hard-reset a service (restart + re-discover key + re-run preflight)
        if handler.path.startswith("/api/services/") and handler.path.endswith("/reset"):
            svc_id = handler.path.split("/")[3]
            body = handler._read_json_body()
            handler._json_response(200, admin_svc.hard_reset_service(svc_id, body or {}))
            return

        # POST /api/log-level -- change log level at runtime (no restart needed)
        if handler.path == "/api/log-level":
            body = handler._read_json_body() or {}
            level = body.get("level", "").upper()
            if level not in ("DEBUG", "INFO", "WARN", "ERROR"):
                handler._json_response(400, {
                    "error": f"Invalid log level '{level}'",
                    "valid": ["DEBUG", "INFO", "WARN", "ERROR"],
                })
                return
            from media_stack.services.runtime_platform import set_log_level, log
            new_level = set_log_level(level)
            log(f"[INFO] Log level changed to {new_level}")
            # Persist so it survives restarts
            handler.state.update_config({"_log_level": new_level})
            handler._json_response(200, {"level": new_level})
            return

        # POST /api/auth/config -- update auth configuration
        if handler.path == "/api/auth/config":
            body = handler._read_json_body()
            if not body:
                handler._json_response(400, {"error": "JSON body required"})
                return
            from .services.auth_config import AuthConfigService
            result = AuthConfigService().update_auth_config(body, handler.action_trigger)
            status = 200 if "error" not in result else 400
            handler._json_response(status, result)
            return

        # POST /api/auth/parse-oidc -- parse uploaded OIDC provider JSON
        if handler.path == "/api/auth/parse-oidc":
            body = handler._read_json_body()
            if not body:
                handler._json_response(400, {"error": "JSON body required"})
                return
            from media_stack.core.auth.oidc_config_parser import parse_oidc_config
            result = parse_oidc_config(body)
            # Strip raw echo to keep response small
            result.pop("raw", None)
            handler._json_response(200, result)
            return

        # POST /api/routing
        if handler.path == "/api/routing":
            body = handler._read_json_body()
            if not body:
                handler._json_response(400, {"error": "JSON body required"})
                return
            handler._json_response(200, config_svc.update_routing(body, handler.action_trigger))
            return

        # POST /api/password-policy — update min_length/require_classes/
        # history_len. Changes take effect on the next user create or
        # password reset (the next UserService rebuild picks up the
        # file). Sudo-gated so a stolen session can't weaken the
        # policy for the whole install.
        if handler.path == "/api/password-policy":
            body = handler._read_json_body()
            if not body:
                handler._json_response(
                    HTTPStatus.BAD_REQUEST,
                    {"error": "JSON body required"},
                )
                return
            from media_stack.api.services.password_policy_config import (
                PasswordPolicyConfig,
            )
            try:
                new_values = PasswordPolicyConfig().save_values(body)
                handler._json_response(HTTPStatus.OK, {
                    "status": "updated", "policy": new_values,
                })
            except OSError as exc:
                handler._json_response(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"error": f"write failed: {str(exc)[:80]}"},
                )
            return

        # POST /api/restore -- restore config from backup JSON
        if handler.path == "/api/restore":
            body = handler._read_json_body()
            if not body or "service_configs" not in body:
                handler._json_response(400, {"error": "backup JSON with service_configs required"})
                return
            handler._json_response(200, config_svc.restore_backup(body, handler.state))
            return

        # POST /api/media-server/reset -- hard-reset media server credentials via DB
        if handler.path == "/api/media-server/reset" or admin_svc.is_media_server_reset_path(handler.path):
            body = handler._read_json_body()
            username = body.get("username", os.environ.get("STACK_ADMIN_USERNAME", "admin"))
            password = body.get("password", os.environ.get("STACK_ADMIN_PASSWORD", "media-stack"))
            if not password or len(password) < 4:
                handler._json_response(400, {"error": "password required (min 4 chars)"})
                return
            handler._json_response(200, admin_svc.jellyfin_hard_reset(username, password))
            return

        # POST /api/gpu/enable -- auto-configure GPU transcoding in Jellyfin
        if handler.path == "/api/gpu/enable":
            handler._json_response(200, ops_svc.enable_gpu_transcoding())
            return

        # POST /api/snapshot -- take a config snapshot now
        if handler.path == "/api/snapshot":
            handler._json_response(200, ops_svc.take_snapshot())
            return

        # POST /api/auto-heal/run -- run a heal cycle right now
        if handler.path == "/api/auto-heal/run":
            from .services import auto_heal as autoheal_svc
            handler._json_response(200, autoheal_svc.run_cycle())
            return

        # POST /api/auto-heal/enabled -- toggle on/off
        if handler.path == "/api/auto-heal/enabled":
            from .services import auto_heal as autoheal_svc
            body = handler._read_json_body() or {}
            handler._json_response(
                200,
                autoheal_svc.set_enabled(bool(body.get("enabled", True))),
            )
            return

        # POST /api/guardrails
        if handler.path == "/api/guardrails":
            body = handler._read_json_body()
            if not body:
                handler._json_response(400, {"error": "JSON body required"})
                return
            handler._json_response(200, disk_svc.update_guardrails(body))
            return

        # POST /api/guardrails/{id}            — update threshold
        # POST /api/guardrails/{id}/test       — dry-run evaluation
        # POST /api/guardrails/{id}/disable    — soft-disable toggle
        if handler.path.startswith("/api/guardrails/"):
            from media_stack.services import guardrails as _guardrails_pkg
            registry = _guardrails_pkg.default()
            tail = handler.path[len("/api/guardrails/"):]
            parts = tail.split("/", 1)
            rule_id = parts[0]
            sub = parts[1] if len(parts) > 1 else ""
            body = handler._read_json_body() or {}
            if registry.get(rule_id) is None:
                handler._json_response(
                    404, {"error": f"unknown guardrail: {rule_id}"},
                )
                return
            if sub == "":
                threshold = body.get("threshold")
                if not isinstance(threshold, dict):
                    handler._json_response(
                        400,
                        {"error": "body must include 'threshold' object"},
                    )
                    return
                handler._json_response(
                    200, registry.update_threshold(rule_id, threshold),
                )
                return
            if sub == "test":
                # Dry-run: collect a fresh state and run only the
                # named rule. The registry's evaluate_one returns a
                # Trigger or None — we lift the relevant fields into
                # the wire shape the UI displays.
                from media_stack.services.guardrails.state_collector import (
                    collect_state,
                )
                snapshot = collect_state()
                snapshot[f"_threshold:{rule_id}"] = registry.threshold_for(rule_id)
                trigger = registry.evaluate_one(rule_id, snapshot)
                if trigger is None:
                    handler._json_response(200, {
                        "would_trigger": False,
                        "severity": None,
                        "current_value": None,
                        "threshold": registry.threshold_for(rule_id),
                    })
                    return
                handler._json_response(200, {
                    "would_trigger": True,
                    "severity": trigger.severity,
                    "current_value": trigger.current_value,
                    "threshold": trigger.threshold,
                    "description": trigger.description,
                })
                return
            if sub == "disable":
                disabled = bool(body.get("disabled", True))
                handler._json_response(
                    200, registry.set_disabled(rule_id, disabled),
                )
                return
            handler._json_response(404, {"error": "unknown guardrail subpath"})
            return

        # POST /api/libraries
        if handler.path == "/api/libraries":
            body = handler._read_json_body()
            libraries = body.get("libraries", [])
            if not isinstance(libraries, list):
                handler._json_response(400, {"error": "libraries must be an array"})
                return
            result = config_svc.update_libraries(libraries)
            if "error" not in result and handler.action_trigger:
                handler.action_trigger("configure-libraries", {})
                result["action"] = "configure-libraries queued"
            handler._json_response(200, result)
            return

        # POST /api/download-categories
        if handler.path == "/api/download-categories":
            body = handler._read_json_body()
            categories = body.get("categories", {})
            if not isinstance(categories, dict):
                handler._json_response(400, {"error": "categories must be an object {name: path}"})
                return
            handler._json_response(200, config_svc.update_download_categories(categories))
            return

        # POST /api/metadata-settings
        if handler.path == "/api/metadata-settings":
            body = handler._read_json_body()
            handler._json_response(200, config_svc.update_metadata_settings(
                body.get("language", ""), body.get("country", ""),
            ))
            return

        # POST /api/discovery-lists
        if handler.path == "/api/discovery-lists":
            body = handler._read_json_body()
            lists = body.get("lists")
            if not isinstance(lists, list):
                handler._json_response(400, {"error": "lists array required"})
                return
            result = config_svc.update_discovery_lists(lists)
            if "error" not in result and handler.action_trigger:
                handler.action_trigger("bootstrap", {})
                result["action"] = "bootstrap queued"
            handler._json_response(200, result)
            return

        # POST /api/display-preferences
        if handler.path == "/api/display-preferences":
            body = handler._read_json_body()
            # Update the playback display_preferences section in the per-app config
            from media_stack.services.app_config_service import load_app_config, save_app_config
            ms_id = config_svc._media_server_id()
            if not ms_id:
                handler._json_response(400, {"error": "No media server configured"})
                return
            app_cfg = load_app_config(ms_id)
            playback = app_cfg.setdefault("playback", {})
            dp = playback.setdefault("display_preferences", {})
            if "show_backdrop" in body:
                dp["show_backdrop"] = bool(body["show_backdrop"])
            if "custom_prefs" in body and isinstance(body["custom_prefs"], dict):
                dp["custom_prefs"] = body["custom_prefs"]
            if "per_library_prefs" in body and isinstance(body["per_library_prefs"], dict):
                dp["per_library_prefs"] = body["per_library_prefs"]
            result = save_app_config(ms_id, app_cfg)
            if "error" not in result and handler.action_trigger:
                handler.action_trigger("configure-playback", {})
                result["action"] = "configure-playback queued"
            handler._json_response(200, result)
            return

        # POST /api/quality-profiles/toggle
        if handler.path == "/api/quality-profiles/toggle":
            body = handler._read_json_body()
            from media_stack.services.apps.servarr.quality_preset_service import toggle_quality, toggle_upgrade
            if "quality" in body:
                handler._json_response(200, toggle_quality(
                    body["service"], int(body["profile_id"]), body["quality"], bool(body["enabled"])
                ))
            elif "upgradeAllowed" in body:
                handler._json_response(200, toggle_upgrade(
                    body["service"], int(body["profile_id"]), bool(body["upgradeAllowed"])
                ))
            else:
                handler._json_response(400, {"error": "quality or upgradeAllowed required"})
            return

        # User management — delegated to class method.
        if (handler.path == "/api/users"
                or handler.path.startswith("/api/users/")
                or handler.path in ("/api/users-reconcile/import",
                                    "/api/users-reconcile/unlink",
                                    "/api/invites",
                                    "/api/invites/accept",
                                    "/api/users-bulk-import",
                                    "/api/tokens")
                or handler.path.startswith("/api/invites/")
                or handler.path.startswith("/api/roles/")
                or handler.path.startswith("/api/tokens/")):
            self._handle_user_mgmt(handler)
            return

        # POST /api/custom-formats/import — import TRASHguides custom formats
        if handler.path == "/api/custom-formats/import":
            body = handler._read_json_body()
            service_id = body.get("service", "")
            index_url = body.get("index_url", "")
            if not service_id or not index_url:
                handler._json_response(400, {"error": "service and index_url required"})
                return
            from media_stack.services.apps.servarr.quality_preset_service import import_trash_custom_formats
            handler._json_response(200, import_trash_custom_formats(service_id, index_url))
            return

        # POST /api/download-client-settings
        if handler.path == "/api/download-client-settings":
            body = handler._read_json_body()
            from .services import content as content_svc_dl
            handler._json_response(200, content_svc_dl.update_download_client_settings(body))
            return

        # POST /api/livetv-sources
        if handler.path == "/api/livetv-sources":
            body = handler._read_json_body()
            result = config_svc.update_livetv_sources(
                tuners=body.get("tuners"), guides=body.get("guides"),
                tuner_url=body.get("tuner_url", ""), guide_url=body.get("guide_url", ""),
                load_all_tuners=body.get("load_all_tuners"),
            )
            # Auto-trigger targeted Live TV reconfigure (not full bootstrap)
            if "error" not in result and handler.action_trigger:
                handler.action_trigger("configure-livetv", {})
                result["action"] = "configure-livetv queued"
            handler._json_response(200, result)
            return

        # POST /api/indexers/{id}/toggle
        if handler.path.startswith("/api/indexers/") and handler.path.endswith("/toggle"):
            parts = handler.path.split("/")
            try:
                indexer_id = int(parts[3])
            except (IndexError, ValueError):
                handler._json_response(400, {"error": "Invalid indexer ID"})
                return
            body = handler._read_json_body()
            from .services import content as content_svc_toggle
            handler._json_response(200, content_svc_toggle.toggle_indexer(indexer_id, bool(body.get("enable", True))))
            return

        # DELETE /api/indexers/{id}
        if handler.path.startswith("/api/indexers/") and handler.path.count("/") == 3:
            parts = handler.path.split("/")
            try:
                indexer_id = int(parts[3])
            except (IndexError, ValueError):
                handler._json_response(400, {"error": "Invalid indexer ID"})
                return
            body = handler._read_json_body()
            if body.get("_method") == "DELETE":
                from .services import content as content_svc_del
                handler._json_response(200, content_svc_del.delete_indexer(indexer_id))
                return

        # POST /webhooks/arr — receives Sonarr/Radarr webhook on download/import.
        # Triggers Jellyfin library scan so new content appears immediately.
        # Optional HMAC verification (WEBHOOK_HMAC_SECRET); without it,
        # anyone who can reach the endpoint can trigger scans.
        if handler.path == "/webhooks/arr":
            body, hmac_ok = _read_body_with_hmac(handler)
            if not hmac_ok:
                handler._json_response(
                    HTTPStatus.FORBIDDEN,
                    {"error": "webhook signature missing or invalid"},
                )
                return
            event = body.get("eventType", "unknown")
            title = ""
            if body.get("movie"):
                title = body["movie"].get("title", "")
            elif body.get("series"):
                title = body["series"].get("title", "")
            elif body.get("episodes"):
                eps = body["episodes"]
                title = eps[0].get("title", "") if eps else ""
            import media_stack.services.runtime_platform as _rp
            _rp.log(f"[INFO] Arr webhook: {event} — {title or 'unknown'}")
            # Scan on import/download events
            if event in ("Download", "EpisodeFileDelete", "MovieFileDelete", "MovieAdded", "SeriesAdd", "Grab"):
                try:
                    from .services.health import discover_api_keys
                    from .services.registry import SERVICE_MAP
                    api_key = discover_api_keys().get("jellyfin", "")
                    ms = SERVICE_MAP.get("jellyfin")
                    if ms and api_key:
                        import urllib.request
                        # Jellyfin 10.11 accepts ``X-Emby-Token`` in
                        # place of the legacy ``?api_key=`` query
                        # parameter. Moving the credential into a
                        # header keeps it out of access logs and
                        # proxy telemetry. See
                        # tests/unit/test_api_key_not_in_url_query_ratchet.py
                        urllib.request.urlopen(urllib.request.Request(
                            f"http://{ms.host}:{ms.port}/Library/Refresh",
                            method="POST",
                            headers={"X-Emby-Token": api_key},
                        ), timeout=5)
                        _rp.log(f"[OK] Jellyfin scan triggered by arr webhook ({event})")
                except Exception as exc:
                    _rp.log(f"[WARN] Jellyfin scan from webhook failed: {exc}")
            handler._json_response(200, {"status": "ok", "event": event})
            return

        # POST /api/import-lists/{service}/{id}/toggle
        if handler.path.startswith("/api/import-lists/") and handler.path.endswith("/toggle"):
            parts = handler.path.split("/")
            if len(parts) >= 5:
                svc_id = parts[3]
                try:
                    list_id = int(parts[4])
                except ValueError:
                    handler._json_response(400, {"error": "Invalid list ID"})
                    return
                body = handler._read_json_body()
                enabled = body.get("enabled", True)
                from .services import content as content_svc_toggle
                handler._json_response(200, content_svc_toggle.toggle_import_list(svc_id, list_id, enabled))
                return

        # POST /api/import-lists/{service}/{id}/delete
        if handler.path.startswith("/api/import-lists/") and handler.path.endswith("/delete"):
            parts = handler.path.split("/")
            if len(parts) >= 5:
                svc_id = parts[3]
                try:
                    list_id = int(parts[4])
                except ValueError:
                    handler._json_response(400, {"error": "Invalid list ID"})
                    return
                from .services import content as content_svc_list
                handler._json_response(200, content_svc_list.delete_import_list(svc_id, list_id))
                return

        # POST /api/schedules
        if handler.path == "/api/schedules":
            body = handler._read_json_body()
            from .services import scheduler as sched_svc
            handler._json_response(200, sched_svc.add_schedule(
                body.get("action", ""), int(body.get("interval_seconds", 0)),
                body.get("label", ""),
            ))
            return

        # POST /api/schedules/{id}/delete
        if handler.path.startswith("/api/schedules/") and handler.path.endswith("/delete"):
            parts = handler.path.split("/")
            try:
                sched_id = int(parts[3])
            except (IndexError, ValueError):
                handler._json_response(400, {"error": "Invalid schedule ID"})
                return
            from .services import scheduler as sched_svc_del
            handler._json_response(200, sched_svc_del.remove_schedule(sched_id))
            return

        # POST /api/validate-migration
        if handler.path == "/api/validate-migration":
            body = handler._read_json_body()
            handler._json_response(200, disk_svc.validate_migration_target(body.get("target_path", "")))
            return

        # POST /api/custom-service
        if handler.path == "/api/custom-service":
            body = handler._read_json_body()
            if not body:
                handler._json_response(400, {"error": "JSON body required"})
                return
            handler._json_response(200, config_svc.add_custom_service(body))
            return

        # POST /api/profile
        if handler.path == "/api/profile":
            body = handler._read_json_body()
            content = body.get("content", "")
            if not content:
                handler._json_response(400, {"error": "content field required"})
                return
            handler._json_response(200, config_svc.save_profile(content, handler.reload_config))
            return

        # POST /api/envvars
        if handler.path == "/api/envvars":
            body = handler._read_json_body()
            key = body.get("key", "")
            value = body.get("value", "")
            if not key:
                handler._json_response(400, {"error": "key field required"})
                return
            # Platform prefixes + service-derived prefixes from the registry
            _PLATFORM_PREFIXES = ("BOOTSTRAP_", "STACK_", "K8S_", "CONTROLLER_", "PUID", "PGID", "TZ")
            from .services.registry import SERVICES as _env_svcs
            _svc_prefixes = {s.api_key_env.split("_")[0] + "_" for s in _env_svcs if s.api_key_env}
            _allowed = set(_PLATFORM_PREFIXES) | _svc_prefixes
            if not any(key.startswith(p) for p in _allowed):
                handler._json_response(400, {"error": f"env var must start with a known prefix (BOOTSTRAP_, STACK_, K8S_, CONTROLLER_, or a registered service prefix)"})
                return
            handler._json_response(200, config_svc.set_envvar(key, value))
            return

        # POST /api/envvars/delete — symmetric with the set above.
        # Same prefix allowlist so the dashboard can't remove
        # arbitrary host env vars (PATH, HOME, etc.) by accident
        # or by spoofed admin request. Same single-process scope —
        # persistence is the deployment's job, not this endpoint's.
        if handler.path == "/api/envvars/delete":
            body = handler._read_json_body()
            key = body.get("key", "")
            if not key:
                handler._json_response(400, {"error": "key field required"})
                return
            _PLATFORM_PREFIXES = ("BOOTSTRAP_", "STACK_", "K8S_", "CONTROLLER_", "PUID", "PGID", "TZ")
            from .services.registry import SERVICES as _env_svcs
            _svc_prefixes = {s.api_key_env.split("_")[0] + "_" for s in _env_svcs if s.api_key_env}
            _allowed = set(_PLATFORM_PREFIXES) | _svc_prefixes
            if not any(key.startswith(p) for p in _allowed):
                handler._json_response(400, {"error": "env var must start with a known prefix (BOOTSTRAP_, STACK_, K8S_, CONTROLLER_, or a registered service prefix)"})
                return
            handler._json_response(200, config_svc.delete_envvar(key))
            return

        # POST /webhooks/test  or  POST /api/webhooks/test
        if handler.path in ("/webhooks/test", "/api/webhooks/test"):
            handler._json_response(200, handler._test_webhook())
            return

        # POST /cancel, POST /actions/cancel, POST /api/actions/cancel
        # -- cancel running action. /api/actions/* is the canonical
        # path for SPA-issued requests (the UI's nginx only proxies
        # /api/*); the bare /actions/* paths are kept as legacy
        # aliases for direct curl / older operators.
        if handler.path in ("/cancel", "/actions/cancel", "/api/actions/cancel"):
            cancelled = handler.state.cancel_action()
            handler._json_response(200, {
                "status": "cancel_requested" if cancelled else "no_action_running",
                "current_action": handler.state.current_action.to_dict() if handler.state.current_action else None,
            })
            return

        # POST /actions/{name}  or  POST /api/actions/{name}
        for prefix in ("/api/actions/", "/actions/"):
            if handler.path.startswith(prefix):
                action_name = handler.path[len(prefix):]
                if action_name not in KNOWN_ACTIONS:
                    handler._json_response(404, {"error": f"unknown action '{action_name}'", "known": sorted(KNOWN_ACTIONS)})
                    return
                handler._handle_action(action_name)
                return

        # POST /config
        if handler.path == "/config":
            body = handler._read_json_body()
            if not body:
                handler._json_response(400, {"error": "JSON body required"})
                return
            updated = handler.state.update_config(body)
            logger.info("Config updated: %s", body)
            handler._json_response(200, {"status": "updated", "config": updated})
            return

        # POST /webhooks  or  POST /api/webhooks (canonical for SPA;
        # nginx only proxies /api/* — the bare /webhooks path stays
        # for direct curl / arr-side webhook receivers).
        if handler.path in ("/webhooks", "/api/webhooks"):
            body = handler._read_json_body()
            url = body.get("url", "").strip()
            if url:
                err = _webhook_url_validator.validate(url)
                if err is not None:
                    handler._json_response(400, {"error": err})
                    return
                handler.state.webhook_urls.add(url)
                # Persist webhooks so they survive restarts
                handler.state.update_config({"_webhook_urls": list(handler.state.webhook_urls)})
            handler._json_response(200, {"webhook_urls": list(handler.state.webhook_urls)})
            return

        handler._json_response(404, {"error": "not found"})

    def _handle_security_post(
        self, handler: ControllerAPIHandler,
    ) -> None:
        """Dispatch session-visibility POST endpoints.

        The ``Idempotency-Key`` request header is honoured by
        ``SecurityPostHandlers.dispatch`` via the shared
        :class:`IdempotencyCache`; a repeat within TTL returns the
        cached response body without re-firing any side effect. The
        literal ``handler.headers.get("Idempotency-Key", "")`` is
        read inside ``SecurityPostHandlers._idem_key`` — the
        idempotency ratchet's AST scan accepts any receiver of
        ``.headers.get("Idempotency-Key", ...)``.
        """
        if not self._preflight(handler):
            return
        body = handler._read_json_body() or {}
        actor = _actor_resolver.resolve(handler, body)
        _security_post_handlers.dispatch(
            handler, handler.path, body, actor,
        )

    def _handle_user_mgmt(self, handler: ControllerAPIHandler) -> None:
        """Dispatch user-management POST endpoints."""
        if not self._preflight(handler):
            return
        body = handler._read_json_body()
        # Resolve is_admin from the caller's role rather than passing a
        # blanket True. The resolver also plumbs the trusted-proxy
        # client IP and user-agent onto the Actor so downstream audit
        # entries tie back to the real client, not the Envoy hop. Legacy
        # bootstrap (env-var admin, missing role catalog) falls back to
        # is_admin=True — see ActorResolver.resolve.
        actor = _actor_resolver.resolve(handler, body or {})
        svc = build_default_service()
        try:
            result = self._dispatch_user_mgmt(handler.path, svc, body, actor)
            if result is None:
                handler._json_response(
                    HTTPStatus.BAD_REQUEST, {"error": "invalid user request"},
                )
                return
            handler._json_response(HTTPStatus.OK, result)
        except UserServiceError as exc:
            handler._json_response(
                HTTPStatus.BAD_REQUEST, {"error": str(exc)[:_ERR_LEN]},
            )
        except Exception as exc:  # noqa: BLE001
            handler._json_response(
                HTTPStatus.BAD_REQUEST, {"error": str(exc)[:_ERR_LEN]},
            )

    def _global_preflight(self, handler) -> bool:
        """Rate-limit + CSRF gate applied to every POST.

        The user-mgmt-specific bucket (_user_mgmt_limiter) still runs
        inside _preflight for /api/users/** so those stay tight; this
        is the wider global envelope."""
        client_id = self._client_ip(handler)
        if not _global_post_limiter.allow(client_id=client_id,
                                           bucket="global-post"):
            # Audit login-path rate limiting specifically so brute-
            # force attempts that trip the global bucket still show up
            # as LOGIN_RATE_LIMITED in the auth-event timeline.
            if handler.path == "/api/auth/login":
                _session_login_helper._audit_login_event(
                    handler, LOGIN_RATE_LIMITED,
                    username="", reason="global_post_bucket",
                )
            handler._json_response(
                HTTPStatus.TOO_MANY_REQUESTS,
                {"error": "rate limit exceeded; slow down"},
            )
            return False
        if handler.path in self._CSRF_EXEMPT_POST_PATHS:
            return True
        if not self._check_csrf(handler):
            security_counters.incr("csrf_fail")
            handler._json_response(
                HTTPStatus.FORBIDDEN,
                {"error": "CSRF token missing or invalid"},
            )
            return False
        return True

    def _client_ip(self, handler) -> str:
        """Per-IP bucket key for the global POST + user-mgmt rate
        limiters. Uses the trusted-proxy resolver so an attacker
        behind Envoy doesn't share a bucket with every other client
        on the proxy hop (which would either lock out everyone or
        let them all slip under the limit at once).
        """
        ip = ""
        try:
            ip = _trusted_proxy_auth.client_ip(handler)
        except Exception:  # noqa: BLE001
            ip = ""
        return ip or "-"

    def _preflight(self, handler) -> bool:
        # User-mgmt-specific tighter limit (CSRF already handled globally).
        if not self._check_rate_limit(handler):
            handler._json_response(
                HTTPStatus.TOO_MANY_REQUESTS,
                {"error": "rate limit exceeded; slow down"},
            )
            return False
        return True

    def _dispatch_user_mgmt(self, path: str, svc, body: dict, actor: str):
        if path == "/api/users-reconcile/import":
            return svc.import_orphan(
                provider_name=str(body.get("provider_name", "")),
                external_id=str(body.get("external_id", "")),
                role_slug=str(body.get("role_slug", "")),
                actor=actor,
            )
        if path == "/api/users-reconcile/unlink":
            return svc.unlink_ghost(
                user_id=str(body.get("user_id", "")),
                provider_name=str(body.get("provider_name", "")),
                actor=actor,
            )
        if path == "/api/invites":
            return _user_mgmt_helper.invite_create(body, actor)
        if path == "/api/invites/accept":
            return _user_mgmt_helper.invite_accept(body)
        if path.startswith("/api/invites/"):
            return _user_mgmt_helper.invite_revoke(path, actor)
        if path == "/api/users-bulk-import":
            return _user_mgmt_helper.bulk_import(svc, body, actor)
        if path.startswith("/api/roles/"):
            return _user_mgmt_helper.role_update(path, body, actor)
        if path == "/api/tokens":
            return _user_mgmt_helper.token_create(body, actor)
        if path == "/api/tokens/refresh":
            return _user_mgmt_helper.token_refresh(body, actor)
        if path == "/api/tokens/revoke-family":
            return _user_mgmt_helper.token_family_revoke(body, actor)
        if path.startswith("/api/tokens/"):
            return _user_mgmt_helper.token_revoke(path, actor)
        if path == "/api/users":
            return self._user_create(svc, body, actor)
        return self._user_action(path, svc, body, actor)

    def _check_rate_limit(self, handler) -> bool:
        client_id = self._client_ip(handler)
        if not _user_mgmt_limiter.allow(client_id=client_id, bucket="user-mgmt"):
            return False
        # Reset-password gets a separate, tighter per-ACCOUNT bucket so
        # an attacker rotating IPs still trips the throttle on the
        # target user_id.
        parts = handler.path.split("/")
        if len(parts) >= 5 and parts[4] == "reset-password":
            target_uid = parts[3]
            if not _pw_reset_limiter.allow(client_id=target_uid,
                                            bucket="pw-reset"):
                return False
        return True

    def _check_csrf(self, handler) -> bool:
        """CSRF enforcement — smart default + Origin/Referer cross-check.

        Requests that include a session cookie are assumed to come from a
        browser and must present a matching X-CSRF-Token header. Requests
        without a cookie are API clients using basic-auth from a script;
        they're not CSRF-vulnerable and are allowed through unless
        CSRF_ENFORCE=1 forces strict mode.

        When a browser request is subject to CSRF, we ALSO verify the
        Origin (or falling back to Referer) header is same-origin. This
        defends against a subclass of token-theft attacks — an attacker
        who steals a token still needs to forge requests from the same
        origin as the dashboard, which same-origin policy prevents.
        """
        if _CSRF_MODE == "0":
            return True
        headers = getattr(handler, "headers", None)
        if headers is None:
            return True
        try:
            cookie_header = headers.get("Cookie", "")
            csrf_header = headers.get(_csrf.header_name, "")
            origin = headers.get("Origin", "") or ""
            referer = headers.get("Referer", "") or ""
            host = headers.get("Host", "") or ""
        except AttributeError:
            return True
        has_cookie = isinstance(cookie_header, str) and bool(cookie_header.strip())
        if not (_CSRF_ENFORCE or has_cookie):
            return True
        if not _csrf.verify(cookie_header=cookie_header,
                             header_value=csrf_header):
            return False
        if not self._origin_matches_host(origin, referer, host):
            security_counters.incr("origin_reject")
            return False
        return True

    def _origin_matches_host(self, origin: str, referer: str, host: str) -> bool:
        """Defense-in-depth: reject POSTs whose Origin/Referer doesn't
        match the Host header. Missing Origin/Referer is tolerated so
        older browsers + server-to-server clients still work; it's the
        stolen-token case we're trying to block.
        """
        if not host:
            return True
        for candidate in (origin, referer):
            if not candidate:
                continue
            try:
                parsed = urlparse(candidate)
            except ValueError:
                return False
            netloc = parsed.netloc or ""
            if not netloc:
                return False
            # Strip :port from both sides so http://host:9100 vs
            # Host: host:9100 compare cleanly.
            if netloc.split(":")[0].lower() != host.split(":")[0].lower():
                return False
        return True

    def _user_create(self, svc, body: dict, actor: Actor) -> dict:
        result = svc.create_user(
            email=str(body.get("email", "")).strip(),
            username=str(body.get("username", "")).strip(),
            display_name=str(body.get("display_name", "")).strip(),
            role_slug=str(body.get("role_slug", "")).strip(),
            password=str(body.get("password", "") or ""),
            actor=actor,
        )
        return _strip_legacy_plaintext(result) or {}

    def _user_action(self, path: str, svc, body: dict, actor: Actor) -> dict | None:
        parts = path.split("/")
        if len(parts) < 4:
            raise UserServiceError("user id required")
        user_id = parts[3]
        action = parts[4] if len(parts) >= 5 else ""
        if action == "" and body.get("_method") == "DELETE":
            action = "delete"
        dispatch = {
            "role": lambda: svc.set_role(
                user_id, str(body.get("role_slug", "")).strip(), actor=actor),
            "state": lambda: svc.set_state(
                user_id, UserState(str(body.get("state", "active"))), actor=actor),
            "reset-password": lambda: _strip_legacy_plaintext(
                svc.reset_password(
                    user_id, password=str(body.get("password", "") or ""),
                    actor=actor,
                ),
            ),
            "delete": lambda: svc.delete_user(user_id, actor=actor),
            "revoke-sessions": lambda: _user_mgmt_helper.revoke_sessions(
                svc, user_id, actor),
        }
        fn = dispatch.get(action)
        if fn is None:
            raise UserServiceError(f"unknown action: {action}")
        return fn()

    @staticmethod
    def _build_known_actions() -> frozenset[str]:
        """Build KNOWN_ACTIONS from core actions + contract-discovered
        jobs + their declared aliases.

        Aliases are first-class accept-list entries: ``POST
        /actions/reconcile`` returns 202, run_job resolves the
        alias to ``bootstrap`` and walks the same tree. Without
        this merge, hitting the alias would 404 even though it's
        declared in the contract.
        """
        actions = set(_CORE_ACTIONS)
        try:
            from media_stack.cli.commands.job_framework import (
                discover_jobs_from_contracts,
                discover_job_aliases,
            )
            for job in discover_jobs_from_contracts():
                actions.add(job["name"])
            for alias in discover_job_aliases():
                actions.add(alias)
        except Exception as exc:
            log_swallowed(exc)
        return frozenset(actions)

    @staticmethod
    def _handle_service_api_key_post(handler: ControllerAPIHandler) -> None:
        parts = handler.path.split("/")
        svc_id = parts[3] if len(parts) >= 5 else ""
        from media_stack.api.services.registry import SERVICE_MAP, read_api_key_from_file, read_api_key_via_http
        svc = SERVICE_MAP.get(svc_id)
        if not svc or not svc.api_key_env:
            handler._json_response(404, {"error": f"Service '{svc_id}' not found or has no API key"})
            return
        body = handler._read_json_body() or {}
        manual_key = str(body.get("api_key", "")).strip()
        if manual_key:
            os.environ[svc.api_key_env] = manual_key
            admin_svc.persist_keys_to_secret({svc.api_key_env: manual_key})
            handler._json_response(200, {"status": "set", "service": svc_id, "env": svc.api_key_env})
            return
        # Auto-discover: try file, then HTTP
        config_root = os.environ.get("CONFIG_ROOT", "/srv-config")
        key = read_api_key_from_file(svc_id, config_root)
        source = "config_file"
        if not key:
            key = read_api_key_via_http(svc_id)
            source = "http"
        if key:
            os.environ[svc.api_key_env] = key
            admin_svc.persist_keys_to_secret({svc.api_key_env: key})
            handler._json_response(200, {"status": "discovered", "service": svc_id, "source": source})
        else:
            handler._json_response(404, {"error": f"Could not discover API key for {svc_id}. Provide it manually via api_key field."})


_instance = PostRequestHandler()
handle = _instance.handle


# ---------------------------------------------------------------------------
# Helper functions for complex route handlers
# ---------------------------------------------------------------------------
_build_known_actions = _instance._build_known_actions
_handle_service_api_key_post = _instance._handle_service_api_key_post
KNOWN_ACTIONS = _build_known_actions()


# ---------------------------------------------------------------------------
# Media-integrity POST → Job framework wrapper
# ---------------------------------------------------------------------------
#
# Backwards-compatible shim: the SPA still calls
# ``POST /api/media-integrity/{reconcile,enforce-config,resolve-review}``
# but every invocation now flows through ``JobRunner.run`` so the
# unified ``/api/jobs.history[]`` reflects the run.
#
# The original handler (``MediaIntegrityHandlers``) still owns:
#   - admin gating + 401/403 mapping
#   - idempotency cache (Idempotency-Key header)
#   - 409 mapping for ``MediaIntegrityInProgress``
#   - body validation (``app``/``release_id``/winner-* for resolve-review)
#
# The shim plugs ``run_job`` between dispatch_post and the underlying
# service method so the history line is written exactly once. The
# response shape is preserved (raw service result on the happy path)
# so the SPA's existing fetch handlers don't break.
# ---------------------------------------------------------------------------

_MI_PATH_TO_JOB = {
    "/api/media-integrity/reconcile": "media-integrity:reconcile",
    "/api/media-integrity/enforce-config": "media-integrity:enforce-config",
    "/api/media-integrity/resolve-review": "media-integrity:resolve-review",
}


def _dispatch_media_integrity_via_job(handler, path, body, actor):
    """Route a media-integrity POST through ``JobRunner`` while
    preserving the legacy response shape.

    Inlines the gating the legacy handler did (admin check,
    idempotency cache, 409 mapping, body validation for
    resolve-review), then dispatches the heavy work through
    ``run_job(...)`` so the unified ``/api/jobs.history[]`` reflects
    the call. The response body is the raw service payload (not the
    JobRunner summary) so UI v1.3.x's existing fetch handlers don't
    break.
    """
    from media_stack.cli.commands.job_framework import run_job
    from media_stack.services.media_integrity.service import (
        MediaIntegrityInProgress,
    )

    bare_path = path.split("?", 1)[0]
    job_name = _MI_PATH_TO_JOB.get(bare_path)
    if job_name is None:
        # Defensive fall-through to the legacy handler (it returns 404).
        _media_integrity_handlers.dispatch_post(handler, path, body, actor)
        return

    service = getattr(_media_integrity_handlers, "_service", None)
    if service is None:
        handler._json_response(
            HTTPStatus.SERVICE_UNAVAILABLE,
            {"error": "media-integrity service not configured"},
        )
        return

    # Auth gating mirrors MediaIntegrityHandlers._require_admin.
    if not getattr(actor, "is_admin", False):
        handler._json_response(
            HTTPStatus.FORBIDDEN, {"error": "admin required"},
        )
        return

    # Idempotency cache reuse — the legacy handler's cache is the
    # source of truth so repeat POSTs within TTL still replay the
    # cached payload (with no JobRunner side effects).
    idem_key = ""
    headers_obj = getattr(handler, "headers", None)
    if headers_obj is not None:
        try:
            idem_key = str(
                headers_obj.get("Idempotency-Key", "") or "",
            ).strip()
        except Exception:
            idem_key = ""
    actor_label = getattr(actor, "audit_label", None) or "user"
    cache = getattr(_media_integrity_handlers, "_cache", None)
    if cache is not None and idem_key:
        cached = cache.get(actor_label, idem_key)
        if cached is not None:
            handler._json_response(HTTPStatus.OK, cached)
            return

    # Branch on endpoint. ``reconcile`` honours ``?dry_run=1``: dry
    # runs stay read-only (no JobRunner / no history entry) since
    # the framework only owns committed runs.
    raw_qs = path.partition("?")[2]
    query = _parse_query_string(raw_qs)

    try:
        if bare_path == "/api/media-integrity/reconcile":
            dry_run = query.get("dry_run", "") in ("1", "true", "yes")
            if dry_run:
                payload = service.reconcile(
                    actor=actor_label, dry_run=True,
                )
            else:
                payload = _run_mi_job_and_extract(
                    run_job, job_name, actor_label, "reconcile",
                )
        elif bare_path == "/api/media-integrity/enforce-config":
            payload = _run_mi_job_and_extract(
                run_job, job_name, actor_label, "enforce",
            )
        elif bare_path == "/api/media-integrity/resolve-review":
            payload = _resolve_review_via_job(
                run_job, body or {}, actor_label,
            )
        else:  # pragma: no cover — _MI_PATH_TO_JOB guards this
            handler._json_response(
                HTTPStatus.NOT_FOUND, {"error": "not found"},
            )
            return
    except MediaIntegrityInProgress:
        handler._json_response(
            HTTPStatus.CONFLICT, {"error": "already in progress"},
        )
        return
    except ValueError as exc:
        handler._json_response(
            HTTPStatus.BAD_REQUEST, {"error": str(exc)[:_ERR_LEN]},
        )
        return

    if cache is not None and idem_key:
        cache.put(actor_label, idem_key, payload)
    handler._json_response(HTTPStatus.OK, payload)


def _parse_query_string(raw: str) -> dict:
    """Minimal parser mirroring media_integrity_handlers' ``_parse_query``
    — single-value pairs, last-write wins."""
    if not raw:
        return {}
    from urllib.parse import parse_qsl
    return {k: v for k, v in parse_qsl(raw, keep_blank_values=True)}


def _run_mi_job_and_extract(
    run_job_fn, job_name: str, actor_label: str, payload_key: str,
):
    """Invoke a media-integrity job via run_job and return the raw
    service payload (legacy response shape).

    Translates ``status: skipped`` with an "already in progress"
    reason back into the ``MediaIntegrityInProgress`` exception so
    the caller can map it to HTTP 409 — the legacy handler's
    contract."""
    from media_stack.services.media_integrity.service import (
        MediaIntegrityInProgress,
    )
    result = run_job_fn(job_name, source="manual", actor=actor_label)
    jobs = (result or {}).get("jobs") or {}
    entry = jobs.get(job_name) or {}
    if payload_key in entry:
        return entry[payload_key]
    status = entry.get("status")
    if status in ("skipped", "prereq_not_met"):
        skip_msg = entry.get("skipped") or entry.get("reason") or ""
        if "already in progress" in str(skip_msg):
            raise MediaIntegrityInProgress(job_name)
        return {"status": "skipped", "reason": skip_msg}
    if status == "error":
        return {"error": entry.get("error", "job failed")}
    # Fallback — unexpected shape. Surface the JobRunner summary so
    # the caller has *something* to render.
    return result


def _resolve_review_via_job(run_job_fn, body: dict, actor_label: str):
    """Validate body params, stash them on TLS, then run the job.

    Mirrors MediaIntegrityHandlers._run_resolve_review's validation
    so 400s come out before the job dispatches."""
    app = str(body.get("app", "") or "").strip()
    release_id = str(body.get("release_id", "") or "").strip()
    if not app:
        raise ValueError("app is required")
    if not release_id:
        raise ValueError("release_id is required")
    winner_file_id = body.get("winner_file_id")
    winner_sub_path = body.get("winner_sub_path")
    if winner_file_id is None and winner_sub_path is None:
        raise ValueError("winner_file_id or winner_sub_path required")
    params = {
        "_mi_review_app": app,
        "_mi_review_release_id": release_id,
        "_mi_review_winner_file_id": winner_file_id,
        "_mi_review_winner_sub_path": winner_sub_path,
        "_mi_review_release_kind": body.get("release_kind"),
        "_mi_review_language": body.get("language"),
        "_mi_review_forced": bool(body.get("forced", False)),
        "_mi_review_hi": bool(body.get("hi", False)),
    }
    with _mi_review_params(params):
        return _run_mi_job_and_extract(
            run_job_fn,
            "media-integrity:resolve-review",
            actor_label,
            "resolve_review",
        )


# Thread-local carrier for resolve-review parameters. The Job framework
# constructs its own ``JobContext`` inside ``run_job``; we can't pass
# kwargs through, so we stash them on a context-managed thread local
# and have ``media_integrity_resolve_review`` read them off the active
# JobContext at handler-call time.
import contextlib as _contextlib
import threading as _threading

_MI_REVIEW_TLS = _threading.local()


@_contextlib.contextmanager
def _mi_review_params(params: dict):
    """Stash resolve-review parameters for the duration of one
    run_job invocation. The job handler
    (``media_stack.services.media_integrity.job_handlers``) reads them
    via the module-level helper rather than from JobContext attrs, so
    no JobContext monkey-patching is needed. Reset on exit so a leak
    between requests can't poison a later run."""
    from media_stack.services.media_integrity import job_handlers as _jh
    prev = getattr(_MI_REVIEW_TLS, "params", None)
    _MI_REVIEW_TLS.params = dict(params)
    _jh.set_review_params(dict(params))
    try:
        yield
    finally:
        if prev is None:
            try:
                del _MI_REVIEW_TLS.params
            except AttributeError:
                pass
            _jh.set_review_params(None)
        else:
            _MI_REVIEW_TLS.params = prev
            _jh.set_review_params(prev)
