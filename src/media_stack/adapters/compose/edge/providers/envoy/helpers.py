"""Utility helpers for Envoy dynamic config generation."""

from __future__ import annotations

import re

_HOST_RULE_RE = re.compile(r"Host\((?P<body>[^)]*)\)", flags=re.IGNORECASE)
_PATH_PREFIX_RULE_RE = re.compile(r"PathPrefix\((?P<body>[^)]*)\)", flags=re.IGNORECASE)
_BACKTICK_TOKEN_RE = re.compile(r"`([^`]+)`")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def _tokenize(value: object) -> str:
    return _NON_ALNUM_RE.sub("_", str(value or "").strip().lower()).strip("_")


def _extract_backtick_tokens(value: str) -> tuple[str, ...]:
    return tuple(
        token.strip()
        for token in _BACKTICK_TOKEN_RE.findall(str(value or ""))
        if str(token or "").strip()
    )


def _rule_hosts(rule: str) -> tuple[str, ...]:
    match = _HOST_RULE_RE.search(str(rule or ""))
    if not match:
        return ()
    return _extract_backtick_tokens(str(match.group("body") or ""))


def _rule_path_prefix(rule: str) -> str:
    match = _PATH_PREFIX_RULE_RE.search(str(rule or ""))
    if not match:
        return ""
    tokens = _extract_backtick_tokens(str(match.group("body") or ""))
    if not tokens:
        return ""
    value = str(tokens[0] or "").strip()
    if not value:
        return ""
    if not value.startswith("/"):
        value = f"/{value}"
    return value


def _strip_prefix_value(middleware_cfg: dict) -> str:
    strip_cfg = middleware_cfg.get("stripPrefix")
    if not isinstance(strip_cfg, dict):
        return ""
    prefixes = strip_cfg.get("prefixes")
    if isinstance(prefixes, list) and prefixes:
        value = str(prefixes[0] or "").strip()
    else:
        value = str(strip_cfg.get("prefix") or "").strip()
    if not value:
        return ""
    if not value.startswith("/"):
        value = f"/{value}"
    return value


def _cluster_name(service_name: str) -> str:
    token = _tokenize(service_name)
    return f"service_{token or 'app'}"


def _virtual_host_name(host: str) -> str:
    token = _tokenize(host)
    return f"vhost_{token or 'default'}"


def _path_prefix_app_slug(path_prefix: str) -> str:
    token = str(path_prefix or "").strip().rstrip("/")
    if not token:
        return ""
    slug = token.rsplit("/", 1)[-1].strip().lower()
    return _tokenize(slug)


def _session_cookie_name(path_prefix: str) -> str:
    app_slug = _path_prefix_app_slug(path_prefix)
    if not app_slug:
        return "media_stack_app"
    return f"media_stack_app_{app_slug}"


def _path_prefix_root(path_prefix: str) -> str:
    token = str(path_prefix or "").strip().rstrip("/")
    if not token:
        return "/"
    if not token.startswith("/"):
        token = f"/{token}"
    parent = token.rsplit("/", 1)[0].strip()
    if not parent:
        return "/"
    if not parent.startswith("/"):
        parent = f"/{parent}"
    return parent.rstrip("/") or "/"
