"""Pure helpers for ``controller_config_policy``.

Split out of ``controller_config_policy.py`` so the core
``StackControllerConfigPolicy`` class stays focused on its
top-level apply_* methods.

Per ADR-0012 (OO + class-structure discipline) the 16 helpers
are organized into two collaborator classes:

* ``_StringNormalizer`` — pure-string transforms (tokenize,
  slugify, host/port parsing, etc.). No state; reused by the
  config-mutator class via constructor injection.
* ``ControllerConfigPolicyHelpers`` — config-mutating ops that
  walk/edit a ``cfg`` dict (set_enabled, walk_path,
  set_bool_path, path_prefix_url_base, discovery-auto-flag
  stamping). Constructor-injects a ``_StringNormalizer``.

Module-level singletons + 16 underscore aliases preserve the
public API the main module already imports
(``_tokenize``, ``_slugify``, ``_walk_path``, ``_set_bool_path``,
``_set_enabled`` & friends).
"""

from __future__ import annotations

import re
from typing import Any
from urllib import parse


_DISCOVERY_AUTO_FLAG_KEYS: tuple[str, ...] = (
    "enable_auto",
    "enable_automatic_add",
    "search_on_add",
    "should_search",
)


class _StringNormalizer:
    """Pure-string transforms shared across policy mutators.

    None of these methods touch ``self`` for state — the class
    exists so callers can be constructor-injected with a single
    collaborator instead of importing 10 loose functions. Plain
    instance methods (no ``@staticmethod``) per ADR-0012.
    """

    def tokenize(self, value: str) -> str:
        """Normalize a value to a lowercase alphanumeric token.

        Used everywhere selected-app membership is checked; keeping it
        pure lets it be memoized/cached freely in the future."""
        return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())

    def slugify(self, value: str) -> str:
        """Lowercase slug preserving hyphens — used for URL path segments."""
        return re.sub(r"[^a-z0-9\-]+", "", str(value or "").strip().lower()).strip("-")

    def normalize_prefix(self, value: str) -> str:
        """Canonicalize an app-path prefix (default ``/app``).

        Ensures a leading slash, strips trailing slashes, and falls
        back to ``/app`` for empty input."""
        token = str(value or "").strip()
        if not token:
            return "/app"
        if not token.startswith("/"):
            token = f"/{token}"
        token = token.rstrip("/")
        return token or "/app"

    def url_host(self, url: str) -> str:
        """Strip the scheme off a URL and return the host (+port+path)."""
        token = str(url or "").strip()
        if token.startswith("https://"):
            token = token[len("https://") :]
        elif token.startswith("http://"):
            token = token[len("http://") :]
        return token.rstrip("/")

    def normalize_port(self, value: object) -> str:
        """Return a valid ``1..65535`` port as a string, or ``""``."""
        token = str(value or "").strip()
        if token.startswith(":"):
            token = token[1:]
        if not token or not token.isdigit():
            return ""
        port = int(token)
        if port < 1 or port > 65535:
            return ""
        return str(port)

    def public_port(self, value: object, *, scheme: str) -> str:
        """Return the non-default port for a scheme, or empty.

        Omits port ``80`` for http and ``443`` for https — they'd be
        redundant in the rendered URL."""
        token = self.normalize_port(value)
        if not token:
            return ""
        if scheme == "http" and token == "80":
            return ""
        if scheme == "https" and token == "443":
            return ""
        return token

    def host_name(self, value: str) -> str:
        """Parse and return the hostname in canonical lowercase."""
        text = str(value or "").strip().lower()
        if not text:
            return ""
        parsed = parse.urlparse(text if "://" in text else f"http://{text}")
        return str(parsed.hostname or "").strip().lower()

    def host_with_port(self, value: str, *, port: str) -> str:
        """Combine a host and a port string, preserving URL components.

        If ``value`` already contains a port, that wins over ``port``.
        Path, query and fragment are passed through verbatim."""
        text = str(value or "").strip().lower()
        if not text:
            return ""
        if not port:
            return text
        parsed = parse.urlparse(text if "://" in text else f"http://{text}")
        host = str(parsed.hostname or "").strip().lower()
        if not host:
            return text
        selected_port = str(parsed.port) if parsed.port else port
        path = str(parsed.path or "")
        query = str(parsed.query or "")
        fragment = str(parsed.fragment or "")
        out = f"{host}:{selected_port}{path}"
        if query:
            out = f"{out}?{query}"
        if fragment:
            out = f"{out}#{fragment}"
        return out

    def homepage_host_token(self, value: str) -> str:
        """Extract a URL-safe slug from a homepage host entry.

        Preserves hyphens so the token can be used directly as a URL path
        segment that matches Envoy/K8s service names (e.g. media-stack-controller).
        """
        text = str(value or "").strip().lower()
        if not text:
            return ""
        parsed = parse.urlparse(text if "://" in text else f"http://{text}")
        path = str(parsed.path or "").strip("/")
        if path:
            parts = [part for part in path.split("/") if part]
            if parts:
                if len(parts) >= 2 and parts[0] == "app":
                    return self.slugify(parts[1])
                return self.slugify(parts[-1])
        host = str(parsed.netloc or "").split(":", 1)[0]
        prefix = host.split(".", 1)[0]
        return self.slugify(prefix)

    def homepage_direct_host(
        self,
        value: str,
        *,
        internet_exposed: bool,
        ingress: str,
        token: str,
    ) -> str:
        """Render a tile's direct-host URL for the homepage."""
        text = str(value or "").strip().lower()
        parsed = parse.urlparse(text if "://" in text else f"http://{text}")
        host = str(parsed.netloc or "").split(":", 1)[0].strip().lower()
        if not host:
            host = str(parsed.path or "").strip().split("/", 1)[0].strip().lower()
        if not host:
            if internet_exposed and ingress:
                return f"{token}.{ingress}"
            return f"{token}.local"
        if internet_exposed and ingress and host.endswith(".local"):
            return f"{host[:-6]}.{ingress}"
        return host


class ControllerConfigPolicyHelpers:
    """Config-mutating helpers used by ``StackControllerConfigPolicy``.

    Constructor-injects a ``_StringNormalizer`` so the URL/path
    helpers (``path_prefix_url_base``) can delegate token+prefix
    canonicalization without importing module-level functions.
    """

    def __init__(self, normalizer: _StringNormalizer) -> None:
        self._normalizer = normalizer

    def set_enabled(self, section: dict[str, Any] | None, enabled: bool) -> None:
        """Flip the ``enabled`` flag on a section dict if present.

        Silent no-op when the section is missing or already lacks the
        flag — mirrors the original ``@staticmethod`` behaviour that
        many callers relied on."""
        if not isinstance(section, dict):
            return
        if "enabled" in section:
            section["enabled"] = bool(enabled)

    def walk_path(self, cfg: dict[str, object], path: str) -> dict[str, Any] | None:
        """Walk a dotted path through a config dict.

        Returns the nested dict at ``path`` or ``None`` if any segment
        is missing / non-dict. Caller must treat ``None`` as 'absent'."""
        token = str(path or "").strip()
        if not token:
            return None
        current: Any = cfg
        for segment in token.split("."):
            if not isinstance(current, dict):
                return None
            current = current.get(segment)
        if isinstance(current, dict):
            return current
        return None

    def set_bool_path(self, cfg: dict[str, object], path: str, value: bool) -> None:
        """Set a boolean leaf at a dotted path under ``cfg``.

        Creates nothing — bails if the parent is absent or not a dict.
        Used for toggling feature flags without clobbering siblings."""
        token = str(path or "").strip()
        if not token:
            return
        parent_path, _, leaf = token.rpartition(".")
        leaf_name = str(leaf or "").strip()
        if not leaf_name:
            return
        parent: Any = cfg if not parent_path else self.walk_path(cfg, parent_path)
        if not isinstance(parent, dict):
            return
        parent[leaf_name] = bool(value)

    def path_prefix_url_base(self, token: str, prefix: str) -> str:
        """Build ``<prefix>/<token>`` (the path-prefix URL base for an app)."""
        app_token = self._normalizer.tokenize(token)
        if not app_token:
            return ""
        normalized_prefix = self._normalizer.normalize_prefix(prefix)
        return f"{normalized_prefix}/{app_token}"

    def apply_discovery_auto_flags(
        self,
        arr_discovery_lists: dict[str, Any],
        download_enabled: bool,
    ) -> None:
        """Stamp ``enable_auto`` / search flags onto every discovery-list entry.

        The inner key-stamp loop is delegated to
        ``stamp_discovery_auto_flags_on_entry`` so each method stays
        shallower than the 4-level nesting threshold."""
        for value in arr_discovery_lists.values():
            if not isinstance(value, list):
                continue
            for item in value:
                self.stamp_discovery_auto_flags_on_entry(item, download_enabled)

    def stamp_discovery_auto_flags_on_entry(
        self,
        item: Any,
        download_enabled: bool,
    ) -> None:
        """Set each canonical ``enable_auto`` key on one discovery-list entry.

        Kept separate from ``apply_discovery_auto_flags`` so the outer
        iterator doesn't bury a 3-deep for/if/for under two more levels."""
        if not isinstance(item, dict):
            return
        for key in _DISCOVERY_AUTO_FLAG_KEYS:
            if key in item:
                item[key] = download_enabled


# Module-level singletons — collaborators wired once at import time.
_STRING_NORMALIZER = _StringNormalizer()
_CONFIG_HELPERS = ControllerConfigPolicyHelpers(_STRING_NORMALIZER)


# Backwards-compatible underscore aliases — preserve the public
# import surface ``controller_config_policy.py`` already uses.
_tokenize = _STRING_NORMALIZER.tokenize
_slugify = _STRING_NORMALIZER.slugify
_normalize_prefix = _STRING_NORMALIZER.normalize_prefix
_url_host = _STRING_NORMALIZER.url_host
_normalize_port = _STRING_NORMALIZER.normalize_port
_public_port = _STRING_NORMALIZER.public_port
_host_name = _STRING_NORMALIZER.host_name
_host_with_port = _STRING_NORMALIZER.host_with_port
_homepage_host_token = _STRING_NORMALIZER.homepage_host_token
_homepage_direct_host = _STRING_NORMALIZER.homepage_direct_host

_set_enabled = _CONFIG_HELPERS.set_enabled
_walk_path = _CONFIG_HELPERS.walk_path
_set_bool_path = _CONFIG_HELPERS.set_bool_path
_path_prefix_url_base = _CONFIG_HELPERS.path_prefix_url_base
_apply_discovery_auto_flags = _CONFIG_HELPERS.apply_discovery_auto_flags
_stamp_discovery_auto_flags_on_entry = _CONFIG_HELPERS.stamp_discovery_auto_flags_on_entry


__all__ = [
    "_tokenize",
    "_slugify",
    "_set_enabled",
    "_walk_path",
    "_set_bool_path",
    "_normalize_prefix",
    "_url_host",
    "_normalize_port",
    "_public_port",
    "_host_name",
    "_host_with_port",
    "_homepage_host_token",
    "_homepage_direct_host",
    "_path_prefix_url_base",
    "_apply_discovery_auto_flags",
]
