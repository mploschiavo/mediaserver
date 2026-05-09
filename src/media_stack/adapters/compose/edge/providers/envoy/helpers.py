"""Utility helpers for Envoy dynamic config generation."""

from __future__ import annotations

import re


class EnvoyTraefikHelpers:
    """String/dict utilities for parsing Traefik labels and naming Envoy
    primitives (clusters, virtual hosts, session cookies, path prefixes).

    All public methods are plain instance methods; callers should reuse
    the module-level singleton ``_HELPERS`` (or the function-name
    aliases re-exported below) rather than instantiating this class.
    """

    _HOST_RULE_RE = re.compile(r"Host\((?P<body>[^)]*)\)", flags=re.IGNORECASE)
    _PATH_PREFIX_RULE_RE = re.compile(
        r"PathPrefix\((?P<body>[^)]*)\)", flags=re.IGNORECASE
    )
    _BACKTICK_TOKEN_RE = re.compile(r"`([^`]+)`")
    _NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")

    def tokenize(self, value: object) -> str:
        return self._NON_ALNUM_RE.sub("_", str(value or "").strip().lower()).strip("_")

    def extract_backtick_tokens(self, value: str) -> tuple[str, ...]:
        return tuple(
            token.strip()
            for token in self._BACKTICK_TOKEN_RE.findall(str(value or ""))
            if str(token or "").strip()
        )

    def rule_hosts(self, rule: str) -> tuple[str, ...]:
        match = self._HOST_RULE_RE.search(str(rule or ""))
        if not match:
            return ()
        return self.extract_backtick_tokens(str(match.group("body") or ""))

    def rule_path_prefix(self, rule: str) -> str:
        match = self._PATH_PREFIX_RULE_RE.search(str(rule or ""))
        if not match:
            return ""
        tokens = self.extract_backtick_tokens(str(match.group("body") or ""))
        if not tokens:
            return ""
        value = str(tokens[0] or "").strip()
        if not value:
            return ""
        if not value.startswith("/"):
            value = f"/{value}"
        return value

    def strip_prefix_value(self, middleware_cfg: dict) -> str:
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

    def cluster_name(self, service_name: str) -> str:
        token = self.tokenize(service_name)
        return f"service_{token or 'app'}"

    def virtual_host_name(self, host: str) -> str:
        token = self.tokenize(host)
        return f"vhost_{token or 'default'}"

    def path_prefix_app_slug(self, path_prefix: str) -> str:
        token = str(path_prefix or "").strip().rstrip("/")
        if not token:
            return ""
        slug = token.rsplit("/", 1)[-1].strip().lower()
        return self.tokenize(slug)

    def session_cookie_name(self, path_prefix: str) -> str:
        app_slug = self.path_prefix_app_slug(path_prefix)
        if not app_slug:
            return "media_stack_app"
        return f"media_stack_app_{app_slug}"

    def path_prefix_root(self, path_prefix: str) -> str:
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


_HELPERS = EnvoyTraefikHelpers()

# Module-level aliases preserving the legacy underscore-prefixed import
# API consumed by routes.py / dynamic_config.py / clusters.py /
# virtual_hosts.py and the two unit-test modules.
_tokenize = _HELPERS.tokenize
_extract_backtick_tokens = _HELPERS.extract_backtick_tokens
_rule_hosts = _HELPERS.rule_hosts
_rule_path_prefix = _HELPERS.rule_path_prefix
_strip_prefix_value = _HELPERS.strip_prefix_value
_cluster_name = _HELPERS.cluster_name
_virtual_host_name = _HELPERS.virtual_host_name
_path_prefix_app_slug = _HELPERS.path_prefix_app_slug
_session_cookie_name = _HELPERS.session_cookie_name
_path_prefix_root = _HELPERS.path_prefix_root
