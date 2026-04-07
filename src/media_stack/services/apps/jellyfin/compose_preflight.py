"""Compose preflight hooks for Jellyfin first-run/bootstrap readiness."""

from __future__ import annotations

import json
from typing import Any, Callable
from urllib import error, request

from .cli.jellyfin_controller_api_key_service import (
    ensure_api_key,
    lookup_user_id_with_api_key,
    validate_api_key,
)
from .cli.jellyfin_controller_auth_service import JellyfinBootstrapAuthService

InfoFn = Callable[[str], None]


def _text(value: Any) -> str:
    return str(value or "").strip()


def _http_request(
    base_url: str,
    path: str,
    *,
    host_header: str,
    method: str = "GET",
    payload: Any = None,
    headers: dict[str, str] | None = None,
    timeout: int = 20,
) -> tuple[int, Any, str]:
    url = f"{base_url.rstrip('/')}{path}"
    body = None
    req_headers: dict[str, str] = {}
    if headers:
        req_headers.update(headers)
    if host_header:
        req_headers["Host"] = host_header
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        req_headers["Content-Type"] = "application/json"
    req = request.Request(url=url, data=body, method=method, headers=req_headers)
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            parsed: Any = None
            if raw:
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    parsed = raw
            return int(resp.status), parsed, raw
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        parsed = None
        if raw:
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = raw
        return int(exc.code), parsed, raw
    except error.URLError as exc:
        return 0, None, str(exc)


def _resolve_bootstrap_endpoint(compose_env: dict[str, str]) -> tuple[str, str]:
    explicit_url = _text(compose_env.get("JELLYFIN_BOOTSTRAP_URL"))
    explicit_host = _text(compose_env.get("JELLYFIN_BOOTSTRAP_HOST_HEADER"))
    if explicit_url:
        return explicit_url, explicit_host

    service_host = _text(compose_env.get("JELLYFIN_SERVICE_HOST")) or "jellyfin"
    service_port = _text(compose_env.get("JELLYFIN_SERVICE_PORT")) or "8096"
    if not service_port.isdigit():
        service_port = "8096"
    return f"http://{service_host}:{service_port}", ""


def _container_network_ipv4(container: Any) -> str:
    try:
        reload_fn = getattr(container, "reload", None)
        if callable(reload_fn):
            reload_fn()
    except Exception:
        # Best effort only; continue with last-known attrs.
        pass
    attrs = dict(getattr(container, "attrs", {}) or {})
    network_settings = attrs.get("NetworkSettings")
    if not isinstance(network_settings, dict):
        return ""
    networks = network_settings.get("Networks")
    if not isinstance(networks, dict):
        return ""
    for payload in networks.values():
        if not isinstance(payload, dict):
            continue
        ip_addr = _text(payload.get("IPAddress"))
        if ip_addr:
            return ip_addr
    return ""


def _resolve_reachable_bootstrap_endpoint(
    *,
    compose_env: dict[str, str],
    jellyfin_container: Any,
    info: InfoFn,
) -> tuple[str, str]:
    base_url, host_header = _resolve_bootstrap_endpoint(compose_env)
    status, payload, _ = _http_request(
        base_url,
        "/System/Info/Public",
        host_header=host_header,
        timeout=8,
    )
    if status == 200 and isinstance(payload, dict):
        return base_url, host_header

    ip_addr = _container_network_ipv4(jellyfin_container)
    if not ip_addr:
        return base_url, host_header

    fallback_base_url = f"http://{ip_addr}:8096"
    fallback_status, fallback_payload, _ = _http_request(
        fallback_base_url,
        "/System/Info/Public",
        host_header="",
        timeout=8,
    )
    if fallback_status == 200 and isinstance(fallback_payload, dict):
        info(
            "Compose Jellyfin preflight: fallback to container-network endpoint "
            f"{fallback_base_url} after bootstrap endpoint {base_url} was unreachable."
        )
        return fallback_base_url, ""

    return base_url, host_header


def ensure_compose_jellyfin_bootstrap_access(
    *,
    compose_env: dict[str, str],
    namespace: str,
    docker: Any,
    info: InfoFn,
    **_: object,
) -> dict[str, str]:
    jellyfin_container = docker.get_container("jellyfin")
    if jellyfin_container is None:
        info("Compose Jellyfin preflight: container 'jellyfin' not found; skipping.")
        return {}

    stack_username = _text(compose_env.get("STACK_ADMIN_USERNAME")) or "admin"
    stack_password = (
        _text(compose_env.get("STACK_ADMIN_PASSWORD")) or _text(namespace) or "media-stack"
    )
    compose_env["STACK_ADMIN_USERNAME"] = stack_username
    compose_env["STACK_ADMIN_PASSWORD"] = stack_password

    base_url, host_header = _resolve_reachable_bootstrap_endpoint(
        compose_env=compose_env,
        jellyfin_container=jellyfin_container,
        info=info,
    )

    def _request(
        base: str,
        path: str,
        *,
        method: str = "GET",
        payload: Any = None,
        headers: dict[str, str] | None = None,
        timeout: int = 20,
    ) -> tuple[int, Any, str]:
        return _http_request(
            base,
            path,
            host_header=host_header,
            method=method,
            payload=payload,
            headers=headers,
            timeout=timeout,
        )

    def _warn(message: str) -> None:
        info(f"[WARN] {message}")

    def _fail(message: str) -> None:
        raise RuntimeError(message)

    auth_service = JellyfinBootstrapAuthService(
        http_request=_request,
        info=info,
        warn=_warn,
        fail=_fail,
    )
    auth_service.startup_wizard_if_needed(base_url, stack_username, stack_password)

    existing_key = _text(compose_env.get("JELLYFIN_API_KEY"))
    if existing_key and validate_api_key(base_url, existing_key, http_request=_request):
        info("Compose Jellyfin preflight: existing JELLYFIN_API_KEY is valid.")
        user_id = _text(compose_env.get("JELLYFIN_USER_ID")) or lookup_user_id_with_api_key(
            base_url,
            existing_key,
            stack_username,
            http_request=_request,
        )
        updates: dict[str, str] = {"JELLYFIN_API_KEY": existing_key}
        if user_id:
            updates["JELLYFIN_USER_ID"] = user_id
        compose_env.update(updates)
        return updates

    auth_result = auth_service.try_authenticate_jellyfin(base_url, stack_username, stack_password)
    if auth_result is None:
        startup_auth = auth_service.try_authenticate_startup_user(base_url, stack_password)
        if startup_auth is None:
            raise RuntimeError(
                "Compose Jellyfin preflight could not authenticate with stack-admin credentials. "
                "Verify STACK_ADMIN_USERNAME/STACK_ADMIN_PASSWORD and Jellyfin startup state."
            )
        startup_user = _text(startup_auth.get("username")) or "root"
        startup_user_id = _text(startup_auth.get("user_id"))
        startup_password_used = _text(startup_auth.get("password_used"))
        session_token = _text(startup_auth.get("token"))
        if not session_token:
            raise RuntimeError("Compose Jellyfin preflight startup-user auth returned no token.")
        if startup_user_id and startup_password_used and startup_password_used != stack_password:
            try:
                auth_service.update_user_password(
                    base_url,
                    session_token,
                    startup_user_id,
                    startup_password_used,
                    stack_password,
                )
                upgraded, _, _ = auth_service.authenticate_with_credentials(
                    base_url,
                    startup_user,
                    stack_password,
                )
                if upgraded:
                    session_token = _text(upgraded.get("token")) or session_token
                    startup_user_id = _text(upgraded.get("user_id")) or startup_user_id
            except Exception as exc:
                _warn(f"Compose Jellyfin preflight could not rotate startup-user password: {exc}")
        if startup_user and startup_user.lower() != stack_username.lower():
            _warn(
                "Compose Jellyfin preflight authenticated startup user "
                f"'{startup_user}' instead of STACK_ADMIN_USERNAME '{stack_username}'."
            )
        user_id = startup_user_id
    else:
        session_token, user_id = auth_result

    api_key = ensure_api_key(
        base_url,
        session_token,
        "media-stack-controller",
        http_request=_request,
        info=info,
        warn=_warn,
        fail=_fail,
    )
    if not validate_api_key(base_url, api_key, http_request=_request):
        raise RuntimeError("Compose Jellyfin preflight generated an invalid API key.")

    if not user_id:
        user_id = lookup_user_id_with_api_key(
            base_url,
            api_key,
            stack_username,
            http_request=_request,
        )

    updates = {"JELLYFIN_API_KEY": api_key}
    if user_id:
        updates["JELLYFIN_USER_ID"] = user_id
    compose_env.update(updates)
    info(
        "Compose Jellyfin preflight: startup/auth/API key are ready "
        f"(base_url={base_url}, host_header={host_header or '<none>'})."
    )
    return updates


__all__ = ["ensure_compose_jellyfin_bootstrap_access"]
