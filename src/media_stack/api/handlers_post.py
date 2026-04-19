"""POST route handlers — extracted from ControllerAPIHandler.do_POST().

Every public function receives the ControllerAPIHandler instance as its
first argument so it can call response helpers and access ``self.state``.
"""

from __future__ import annotations

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

from media_stack.api.session_singletons import (
    SESSION_COOKIE_NAME,
    session_store as _session_store,
)
from media_stack.api.tls_factory import build_default_tls_service
from media_stack.core.edge.tls_certificate_service import (
    TlsCertificateServiceError,
)

_dumps = _dumps_mod.dumps

from media_stack.core.auth.csrf import CsrfProtector
from media_stack.core.auth.rate_limiter import RateLimiter
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

if TYPE_CHECKING:
    from .server import ControllerAPIHandler

logger = logging.getLogger("controller_api")

_ERR_LEN = 99


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
    """

    def __init__(self) -> None:
        self._env = os.environ

    def login(self, handler) -> None:
        body = handler._read_json_body() or {}
        username = str(body.get("username", "")).strip()
        password = str(body.get("password", ""))
        if not username or not password:
            handler._json_response(
                HTTPStatus.BAD_REQUEST,
                {"error": "username and password required"},
            )
            return
        if not self._verify_credentials(username, password):
            handler._json_response(
                HTTPStatus.UNAUTHORIZED,
                {"error": "invalid credentials"},
            )
            return
        _sess, plaintext = _session_store.create(owner_username=username)
        self._send_cookie_response(handler, plaintext, expires=False)

    def logout(self, handler) -> None:
        cookie_raw = ""
        headers = getattr(handler, "headers", None)
        if headers is not None:
            try:
                cookie_raw = headers.get("Cookie", "") or ""
            except AttributeError:
                cookie_raw = ""
        for chunk in cookie_raw.split(";"):
            k, _, v = chunk.strip().partition("=")
            if k == SESSION_COOKIE_NAME and v:
                _session_store.revoke(v.strip())
        self._send_cookie_response(handler, "", expires=True)

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
        """Restart Envoy so the new cert is picked up. Best-effort:
        failures are returned in the payload rather than rolling back
        the cert write, since the write was already atomic and the
        worst case is the operator having to manually restart."""
        try:
            return admin_svc.restart_service("envoy")
        except Exception as exc:  # noqa: BLE001
            logger.warning("TLS install: envoy reload failed: %s", exc)
            return {"status": "error", "detail": str(exc)[:_ERR_LEN]}


_tls_handler = _TlsCertHandler()


# ---------------------------------------------------------------------------
# Known actions
# ---------------------------------------------------------------------------

# Core actions (not from contracts)
_CORE_ACTIONS = {
    "bootstrap", "post-setup", "discover-indexers", "restart-apps",
    "push-indexers", "envoy-config", "reconcile", "validate-credentials",
    "configure-media-server",
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
        return build_default_invite_service().revoke(invite_id, actor)

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
                imported.append({
                    "email": result["email"],
                    "user_id": result["id"],
                    "generated_password": result.get("generated_password", ""),
                })
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

        # POST /run -- backward-compatible alias
        if handler.path == "/run":
            handler._handle_action("bootstrap")
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

        # POST /api/guardrails
        if handler.path == "/api/guardrails":
            body = handler._read_json_body()
            if not body:
                handler._json_response(400, {"error": "JSON body required"})
                return
            handler._json_response(200, disk_svc.update_guardrails(body))
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
                        urllib.request.urlopen(urllib.request.Request(
                            f"http://{ms.host}:{ms.port}/Library/Refresh?api_key={api_key}",
                            method="POST",
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

        # POST /webhooks/test
        if handler.path == "/webhooks/test":
            handler._json_response(200, handler._test_webhook())
            return

        # POST /cancel or POST /actions/cancel -- cancel running action
        if handler.path in ("/cancel", "/actions/cancel"):
            cancelled = handler.state.cancel_action()
            handler._json_response(200, {
                "status": "cancel_requested" if cancelled else "no_action_running",
                "current_action": handler.state.current_action.to_dict() if handler.state.current_action else None,
            })
            return

        # POST /actions/{name}
        if handler.path.startswith("/actions/"):
            action_name = handler.path[len("/actions/"):]
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

        # POST /webhooks
        if handler.path == "/webhooks":
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

    def _handle_user_mgmt(self, handler: ControllerAPIHandler) -> None:
        """Dispatch user-management POST endpoints."""
        if not self._preflight(handler):
            return
        body = handler._read_json_body()
        actor = str(body.get("_actor", "") or "controller-ui")
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
            handler._json_response(
                HTTPStatus.TOO_MANY_REQUESTS,
                {"error": "rate limit exceeded; slow down"},
            )
            return False
        if handler.path in self._CSRF_EXEMPT_POST_PATHS:
            return True
        if not self._check_csrf(handler):
            handler._json_response(
                HTTPStatus.FORBIDDEN,
                {"error": "CSRF token missing or invalid"},
            )
            return False
        return True

    def _client_ip(self, handler) -> str:
        addr = getattr(handler, "client_address", None)
        if addr and len(addr) >= 1:
            return str(addr[0])
        return "-"

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
        return self._origin_matches_host(origin, referer, host)

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

    def _user_create(self, svc, body: dict, actor: str) -> dict:
        return svc.create_user(
            email=str(body.get("email", "")).strip(),
            username=str(body.get("username", "")).strip(),
            display_name=str(body.get("display_name", "")).strip(),
            role_slug=str(body.get("role_slug", "")).strip(),
            password=str(body.get("password", "") or ""),
            actor=actor,
        )

    def _user_action(self, path: str, svc, body: dict, actor: str) -> dict | None:
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
            "reset-password": lambda: svc.reset_password(
                user_id, password=str(body.get("password", "") or ""), actor=actor),
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
        """Build KNOWN_ACTIONS from core actions + contract-discovered jobs."""
        actions = set(_CORE_ACTIONS)
        try:
            from media_stack.cli.commands.job_framework import discover_jobs_from_contracts
            for job in discover_jobs_from_contracts():
                actions.add(job["name"])
        except Exception as exc:
            logging.getLogger("media_stack").debug("[DEBUG] Swallowed: %s", exc)
            pass
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
