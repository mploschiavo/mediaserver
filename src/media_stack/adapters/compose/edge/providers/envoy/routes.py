"""Route generation for Envoy dynamic config."""

from __future__ import annotations

import re
from typing import Any

from media_stack.adapters.compose.edge.providers.envoy.helpers import (
    _path_prefix_app_slug,
    _path_prefix_root,
    _session_cookie_name,
)



class EnvoyRouteService:
    def route_headers(self, 
        path_prefix: str,
        host: str,
        *,
        include_session_cookie: bool = False,
        prefer_uncompressed_upstream: bool = False,
    ) -> dict[str, Any]:
        """Build request/response header manipulation for a route."""
        app_slug = _path_prefix_app_slug(path_prefix)
        request_headers_to_add: list[dict[str, Any]] = [
            {
                "header": {
                    "key": "x-forwarded-prefix",
                    "value": path_prefix,
                },
                "append_action": "OVERWRITE_IF_EXISTS_OR_ADD",
            }
        ]
        if prefer_uncompressed_upstream:
            request_headers_to_add.append(
                {
                    "header": {
                        "key": "accept-encoding",
                        "value": "identity",
                    },
                    "append_action": "OVERWRITE_IF_EXISTS_OR_ADD",
                }
            )
        response_headers_to_add: list[dict[str, Any]] = [
            {
                "header": {
                    "key": "x-media-stack-prefix",
                    "value": path_prefix,
                },
                "append_action": "OVERWRITE_IF_EXISTS_OR_ADD",
            },
            {
                "header": {
                    "key": "x-media-stack-host",
                    "value": str(host or "").strip().lower(),
                },
                "append_action": "OVERWRITE_IF_EXISTS_OR_ADD",
            },
        ]
        if include_session_cookie and app_slug:
            response_headers_to_add.append(
                {
                    "header": {
                        "key": "set-cookie",
                        "value": f"{_session_cookie_name(path_prefix)}=1; Path=/; SameSite=Lax",
                    },
                    "append_action": "APPEND_IF_EXISTS_OR_ADD",
                }
            )
        return {
            "request_headers_to_add": request_headers_to_add,
            "response_headers_to_add": response_headers_to_add,
        }
    
    
    def primary_route_cfg(self, 
        *,
        host: str,
        path_prefix: str,
        cluster_name: str,
        include_session_cookie: bool = False,
        prefer_uncompressed_upstream: bool = False,
    ) -> dict[str, Any]:
        """Build primary prefix-match route configuration."""
        route_cfg: dict[str, Any] = {
            "match": {"prefix": path_prefix},
            "route": {
                "cluster": cluster_name,
                "timeout": "0s",
            },
        }
        route_cfg.update(
            route_headers(
                path_prefix,
                host,
                include_session_cookie=include_session_cookie,
                prefer_uncompressed_upstream=prefer_uncompressed_upstream,
            )
        )
        return route_cfg
    
    
    def fallback_regex_rewrite(self, 
        *,
        path_prefix: str,
        strip_prefix: str,
    ) -> dict[str, Any] | None:
        """Build regex rewrite for fallback routes."""
        normalized_prefix = str(path_prefix or "").strip()
        if not normalized_prefix or normalized_prefix == "/":
            return None
    
        normalized_strip = str(strip_prefix or "").strip()
        if normalized_strip and normalized_strip == normalized_prefix:
            # Strip-prefix mode: route fallback requests under the shared app root
            # (for example /app/<service>) back to upstream root paths.
            fallback_prefix = _path_prefix_root(normalized_prefix)
            if not fallback_prefix or fallback_prefix == "/":
                return None
            return {
                "pattern": {
                    "google_re2": {},
                    "regex": f"^{re.escape(fallback_prefix)}/?(.*)$",
                },
                "substitution": r"/\1",
            }
    
        if not normalized_strip:
            # Preserve-prefix mode: when browsers emit root-relative navigations
            # (for example /login), re-prefix to the app path prefix so the
            # upstream still receives /app/<service>/... routes.
            return {
                "pattern": {
                    "google_re2": {},
                    "regex": r"^/(.*)$",
                },
                "substitution": f"{normalized_prefix}/\\1",
            }
    
        return None
    
    
    def html_fallback_redirect_rewrite(self, 
        *,
        path_prefix: str,
        strip_prefix: str,
    ) -> dict[str, Any] | None:
        """Build HTML fallback redirect rewrite."""
        normalized_prefix = str(path_prefix or "").strip()
        if not normalized_prefix or normalized_prefix == "/":
            return None
    
        normalized_strip = str(strip_prefix or "").strip()
        if normalized_strip and normalized_strip == normalized_prefix:
            return {
                "pattern": {
                    "google_re2": {},
                    "regex": r"^/(.*)$",
                },
                "substitution": f"{normalized_prefix}/\\1",
            }
        return None
    
    
    def html_accept_header_match(self) -> dict[str, Any]:
        """Build header matcher for HTML Accept header."""
        return {
            "name": "accept",
            "safe_regex_match": {
                "google_re2": {},
                "regex": r"(?i).*text/html.*",
            },
        }
    
    
    def referer_fallback_route_cfg(self, 
        *,
        host: str,
        path_prefix: str,
        cluster_name: str,
        regex_rewrite: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build referer-based fallback route configuration."""
        route_cfg: dict[str, Any] = {
            "match": {
                "prefix": "/",
                "headers": [
                    {
                        "name": "referer",
                        "safe_regex_match": {
                            "google_re2": {},
                            "regex": (
                                f"^https?://{re.escape(str(host or '').strip())}"
                                rf"(?:\:[0-9]+)?{re.escape(path_prefix)}(?:/.*)?$"
                            ),
                        },
                    }
                ],
            },
            "route": {
                "cluster": cluster_name,
                "timeout": "0s",
            },
        }
        if regex_rewrite is not None:
            route_cfg["route"]["regex_rewrite"] = dict(regex_rewrite)
        route_cfg.update(route_headers(path_prefix, host))
        return route_cfg
    
    
    def asset_referer_fallback_route_cfg(self, 
        *,
        host: str,
        path_prefix: str,
        cluster_name: str,
        regex_rewrite: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Fallback for ES module dynamic import() -- Referer is the parent JS URL.
    
        When <script type="module" crossorigin> dynamically imports a chunk,
        the browser sets Referer to the importing JS file URL (e.g. /assets/main.js),
        not the page URL. Cookies aren't sent in crossorigin anonymous mode.
        This route catches those requests by matching any same-host Referer.
        """
        route_cfg: dict[str, Any] = {
            "match": {
                "prefix": "/",
                "headers": [
                    {
                        "name": "referer",
                        "safe_regex_match": {
                            "google_re2": {},
                            "regex": (
                                f"^https?://{re.escape(str(host or '').strip())}"
                                r"(?:\:[0-9]+)?/.*$"
                            ),
                        },
                    },
                    {
                        "name": "cookie",
                        "safe_regex_match": {
                            "google_re2": {},
                            "regex": (
                                rf".*(?:^|;\s*)"
                                rf"{re.escape(_session_cookie_name(path_prefix))}=1"
                                rf"(?:;|$).*"
                            ),
                        },
                    },
                ],
            },
            "route": {
                "cluster": cluster_name,
                "timeout": "0s",
            },
        }
        if regex_rewrite is not None:
            route_cfg["route"]["regex_rewrite"] = dict(regex_rewrite)
        route_cfg.update(route_headers(path_prefix, host, include_session_cookie=False))
        return route_cfg
    
    
    def cookie_fallback_route_cfg(self, 
        *,
        host: str,
        path_prefix: str,
        cluster_name: str,
        regex_rewrite: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build cookie-based fallback route configuration."""
        app_slug = _path_prefix_app_slug(path_prefix)
        if not app_slug:
            return {}
        cookie_name = _session_cookie_name(path_prefix)
        route_cfg: dict[str, Any] = {
            "match": {
                "prefix": "/",
                "headers": [
                    {
                        "name": "cookie",
                        "safe_regex_match": {
                            "google_re2": {},
                            # Envoy header regex uses full-string matching semantics.
                            # Include prefix/suffix wildcards so multi-cookie headers still match.
                            "regex": (rf".*(?:^|;\s*){re.escape(cookie_name)}=1(?:;|$).*"),
                        },
                    }
                ],
            },
            "route": {
                "cluster": cluster_name,
                "timeout": "0s",
            },
            # CORS headers so ES module dynamic import() includes credentials.
            # Without this, <script type="module" crossorigin> scripts can't
            # send cookies on import(), causing asset 404s for SPAs like Bazarr.
            "response_headers_to_add": [
                {"header": {"key": "access-control-allow-origin", "value": f"http://{host}"},
                 "append_action": "OVERWRITE_IF_EXISTS_OR_ADD"},
                {"header": {"key": "access-control-allow-credentials", "value": "true"},
                 "append_action": "OVERWRITE_IF_EXISTS_OR_ADD"},
            ],
        }
        if regex_rewrite is not None:
            route_cfg["route"]["regex_rewrite"] = dict(regex_rewrite)
        extras = route_headers(path_prefix, host, include_session_cookie=False)
        if "response_headers_to_add" in extras:
            route_cfg["response_headers_to_add"].extend(extras.pop("response_headers_to_add"))
        route_cfg.update(extras)
        return route_cfg
    
    
    def referer_html_redirect_fallback_route_cfg(self, 
        *,
        host: str,
        path_prefix: str,
        regex_rewrite: dict[str, Any],
    ) -> dict[str, Any]:
        """Build referer+HTML redirect fallback route."""
        return {
            "match": {
                "prefix": "/",
                "headers": [
                    {
                        "name": "referer",
                        "safe_regex_match": {
                            "google_re2": {},
                            "regex": (
                                f"^https?://{re.escape(str(host or '').strip())}"
                                rf"(?:\:[0-9]+)?{re.escape(path_prefix)}(?:/.*)?$"
                            ),
                        },
                    },
                    html_accept_header_match(),
                ],
            },
            "redirect": {
                "regex_rewrite": dict(regex_rewrite),
            },
        }
    
    
    def cookie_html_redirect_fallback_route_cfg(self, 
        *,
        path_prefix: str,
        regex_rewrite: dict[str, Any],
    ) -> dict[str, Any]:
        """Build cookie+HTML redirect fallback route."""
        app_slug = _path_prefix_app_slug(path_prefix)
        if not app_slug:
            return {}
        cookie_name = _session_cookie_name(path_prefix)
        return {
            "match": {
                "prefix": "/",
                "headers": [
                    {
                        "name": "cookie",
                        "safe_regex_match": {
                            "google_re2": {},
                            "regex": (rf".*(?:^|;\s*){re.escape(cookie_name)}=1(?:;|$).*"),
                        },
                    },
                    html_accept_header_match(),
                ],
            },
            "redirect": {
                "regex_rewrite": dict(regex_rewrite),
            },
        }


_instance = EnvoyRouteService()
route_headers = _instance.route_headers
primary_route_cfg = _instance.primary_route_cfg
fallback_regex_rewrite = _instance.fallback_regex_rewrite
html_fallback_redirect_rewrite = _instance.html_fallback_redirect_rewrite
html_accept_header_match = _instance.html_accept_header_match
referer_fallback_route_cfg = _instance.referer_fallback_route_cfg
asset_referer_fallback_route_cfg = _instance.asset_referer_fallback_route_cfg
cookie_fallback_route_cfg = _instance.cookie_fallback_route_cfg
referer_html_redirect_fallback_route_cfg = _instance.referer_html_redirect_fallback_route_cfg
cookie_html_redirect_fallback_route_cfg = _instance.cookie_html_redirect_fallback_route_cfg
