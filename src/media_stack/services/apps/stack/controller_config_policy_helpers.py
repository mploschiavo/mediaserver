"""Pure helpers for ``controller_config_policy``.

Split out of ``controller_config_policy.py`` so the core
``StackControllerConfigPolicy`` class stays focused on its
top-level apply_* methods. Every helper below was previously a
``@staticmethod`` on that class; moving them here keeps the
function-count per class sane while preserving the public
module-level names that the tests and callers already import
(``_tokenize``, ``_slugify``, ``_walk_path``, ``_set_bool_path``,
``_set_enabled`` are re-exported from the main module).

Why module-level instead of another mixin class? These are pure
utility functions — they never touch ``self`` — so a class
wrapper was cargo-culted noise. Promoting them to normal
``def`` also drops the static-method-count ratchet (each move
is one less ``@staticmethod`` decorator tracked by the test).
"""

from __future__ import annotations

import re
from typing import Any
from urllib import parse


def _tokenize(value: str) -> str:
    """Normalize a value to a lowercase alphanumeric token.

    Used everywhere selected-app membership is checked; keeping it
    pure lets it be memoized/cached freely in the future."""
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def _slugify(value: str) -> str:
    """Lowercase slug preserving hyphens — used for URL path segments."""
    return re.sub(r"[^a-z0-9\-]+", "", str(value or "").strip().lower()).strip("-")


def _set_enabled(section: dict[str, Any] | None, enabled: bool) -> None:
    """Flip the ``enabled`` flag on a section dict if present.

    Silent no-op when the section is missing or already lacks the
    flag — mirrors the original ``@staticmethod`` behaviour that
    many callers relied on."""
    if not isinstance(section, dict):
        return
    if "enabled" in section:
        section["enabled"] = bool(enabled)


def _walk_path(cfg: dict[str, object], path: str) -> dict[str, Any] | None:
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


def _set_bool_path(cfg: dict[str, object], path: str, value: bool) -> None:
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
    parent: Any = cfg if not parent_path else _walk_path(cfg, parent_path)
    if not isinstance(parent, dict):
        return
    parent[leaf_name] = bool(value)


def _normalize_prefix(value: str) -> str:
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


def _url_host(url: str) -> str:
    """Strip the scheme off a URL and return the host (+port+path)."""
    token = str(url or "").strip()
    if token.startswith("https://"):
        token = token[len("https://") :]
    elif token.startswith("http://"):
        token = token[len("http://") :]
    return token.rstrip("/")


def _normalize_port(value: object) -> str:
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


def _public_port(value: object, *, scheme: str) -> str:
    """Return the non-default port for a scheme, or empty.

    Omits port ``80`` for http and ``443`` for https — they'd be
    redundant in the rendered URL."""
    token = _normalize_port(value)
    if not token:
        return ""
    if scheme == "http" and token == "80":
        return ""
    if scheme == "https" and token == "443":
        return ""
    return token


def _host_name(value: str) -> str:
    """Parse and return the hostname in canonical lowercase."""
    text = str(value or "").strip().lower()
    if not text:
        return ""
    parsed = parse.urlparse(text if "://" in text else f"http://{text}")
    return str(parsed.hostname or "").strip().lower()


def _host_with_port(value: str, *, port: str) -> str:
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


def _homepage_host_token(value: str) -> str:
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
                return _slugify(parts[1])
            return _slugify(parts[-1])
    host = str(parsed.netloc or "").split(":", 1)[0]
    prefix = host.split(".", 1)[0]
    return _slugify(prefix)


def _homepage_direct_host(
    value: str, *, internet_exposed: bool, ingress: str, token: str,
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


def _path_prefix_url_base(token: str, prefix: str) -> str:
    """Build ``<prefix>/<token>`` (the path-prefix URL base for an app)."""
    app_token = _tokenize(token)
    if not app_token:
        return ""
    normalized_prefix = _normalize_prefix(prefix)
    return f"{normalized_prefix}/{app_token}"


_DISCOVERY_AUTO_FLAG_KEYS: tuple[str, ...] = (
    "enable_auto",
    "enable_automatic_add",
    "search_on_add",
    "should_search",
)


def _apply_discovery_auto_flags(
    arr_discovery_lists: dict[str, Any], download_enabled: bool,
) -> None:
    """Stamp ``enable_auto`` / search flags onto every discovery-list entry.

    The inner key-stamp loop is delegated to
    ``_stamp_discovery_auto_flags_on_entry`` so each function stays
    shallower than the 4-level nesting threshold."""
    for value in arr_discovery_lists.values():
        if not isinstance(value, list):
            continue
        for item in value:
            _stamp_discovery_auto_flags_on_entry(item, download_enabled)


def _stamp_discovery_auto_flags_on_entry(
    item: Any, download_enabled: bool,
) -> None:
    """Set each canonical ``enable_auto`` key on one discovery-list entry.

    Kept separate from ``_apply_discovery_auto_flags`` so the outer
    iterator doesn't bury a 3-deep for/if/for under two more levels."""
    if not isinstance(item, dict):
        return
    for key in _DISCOVERY_AUTO_FLAG_KEYS:
        if key in item:
            item[key] = download_enabled


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
