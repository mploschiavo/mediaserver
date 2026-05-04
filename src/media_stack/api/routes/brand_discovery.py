"""Brand + discovery GET routes (ADR-0007 Phase 2).

Two routes migrated off the ``handlers_get.handle()`` elif chain:

* ``GET /api/branding`` -- white-label brand config (name, wordmark,
  homepage URL, etc.) read from ``contracts/branding.yaml``. The
  dashboard pulls this on load to render the header/favicon/splash.
* ``GET /api/discovery/popular-tv`` -- Sonarr ``CustomImport`` feed
  of popular TV scraped from TVMaze, filtered to English-language
  shows with a ``thetvdb`` external id, top ~150 by rating.
  Cached in-process for 6h.

ADR-0007 Phase 2 Phase E: bodies lifted verbatim from the legacy
``GetRequestHandler._handle_branding`` / ``_handle_popular_tv``
static methods so the legacy chain can be deleted entirely.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from http import HTTPStatus
from pathlib import Path
from typing import Any

from media_stack.api.routing import RouteModule, get
from media_stack.core.logging_utils import log_swallowed


class BrandConfigService:
    """Resolves + merges the white-label brand config.

    Reads ``contracts/branding.yaml`` (or the operator's override
    via ``BRANDING_CONFIG_FILE``) and overlays it on the in-tree
    defaults. Defaults survive when no config is present so a fresh
    install still renders cleanly.
    """

    _DEFAULTS = {
        "name": "Media Stack",
        "tagline": "Media Stack Controller",
        "vendor": "iomio",
        "homepage_url": "https://iomio.io",
        "wordmark": "/api/static/iomio-wordmark.svg",
        "icon": "/api/static/iomio-icon.svg",
        "illustration": "/api/static/iomio-orbit.svg",
    }

    def resolve(self) -> dict[str, Any]:
        """Return the merged brand-config dict."""
        candidates: list[Path] = []
        env_path = os.environ.get("BRANDING_CONFIG_FILE", "").strip()
        if env_path:
            candidates.append(Path(env_path))
        candidates.extend([
            Path("/contracts/branding.yaml"),
            Path(__file__).resolve().parents[4] / "contracts" / "branding.yaml",
            Path("contracts/branding.yaml"),
        ])
        loaded: dict[str, Any] = {}
        for cand in candidates:
            if cand and cand.is_file():
                try:
                    import yaml as _yaml
                    raw = _yaml.safe_load(cand.read_text(encoding="utf-8")) or {}
                    loaded = (
                        raw.get("brand") or {}
                    ) if isinstance(raw, dict) else {}
                    break
                except Exception as exc:  # noqa: BLE001
                    log_swallowed(exc)
        return {
            **self._DEFAULTS,
            **{k: v for k, v in loaded.items() if v is not None},
        }


class PopularTvFeedService:
    """Scores + caches the TVMaze-backed popular-TV feed Sonarr's
    ``CustomImport`` poller consumes.

    In-process cache so a Sonarr poll every few minutes doesn't
    hammer TVMaze. 6h matches Sonarr's default list-refresh
    cadence -- the popular set doesn't shift faster than that.
    """

    _CACHE_TTL_SECONDS = 6 * 3600
    _PAGE_COUNT = 4               # TVMaze paginates by 250 -> ~1000 shows.
    _MIN_RATING = 7.0
    _RESULT_CAP = 150
    _PROBE_TIMEOUT_SECONDS = 15

    def __init__(self) -> None:
        self._cache_ts: float = 0.0
        self._cache_payload: list[dict[str, Any]] = []

    def fetch(self) -> list[dict[str, Any]]:
        if (
            self._cache_payload
            and (time.time() - self._cache_ts) < self._CACHE_TTL_SECONDS
        ):
            return self._cache_payload

        # Pull pages 0-3 (~1000 shows). 4 pages gives us enough
        # breadth to filter aggressively without making the request
        # take more than a couple seconds. 404 means we've exhausted
        # the index.
        shows: list[dict[str, Any]] = []
        for page in range(self._PAGE_COUNT):
            url = f"https://api.tvmaze.com/shows?page={page}"
            try:
                req = urllib.request.Request(
                    url,
                    headers={"User-Agent": "media-stack-controller"},
                )
                with urllib.request.urlopen(
                    req, timeout=self._PROBE_TIMEOUT_SECONDS,
                ) as r:
                    chunk = json.loads(r.read())
                    if isinstance(chunk, list):
                        shows.extend(chunk)
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    break
                continue
            except Exception:  # noqa: BLE001
                continue

        scored: list[tuple[float, int, str]] = []
        for s in shows:
            if not isinstance(s, dict):
                continue
            ext = s.get("externals") or {}
            tvdb = ext.get("thetvdb")
            try:
                tvdb_id = int(tvdb) if tvdb is not None else 0
            except (TypeError, ValueError):
                tvdb_id = 0
            if tvdb_id <= 0:
                continue
            lang = str(s.get("language") or "").strip().lower()
            if lang and lang != "english":
                continue
            rating = (s.get("rating") or {}).get("average")
            try:
                score = float(rating) if rating is not None else 0.0
            except (TypeError, ValueError):
                score = 0.0
            if score < self._MIN_RATING:
                continue
            scored.append((score, tvdb_id, str(s.get("name") or "")))

        # Top by rating, dedupe TVDB ids defensively.
        scored.sort(key=lambda t: t[0], reverse=True)
        seen: set[int] = set()
        payload: list[dict[str, Any]] = []
        for _score, tvdb_id, name in scored:
            if tvdb_id in seen:
                continue
            seen.add(tvdb_id)
            payload.append({"tvdbId": tvdb_id, "title": name})
            if len(payload) >= self._RESULT_CAP:
                break

        # If TVMaze was unreachable AND we have a stale cache, serve
        # it anyway -- better than 0 entries which would tell Sonarr
        # to prune everything it auto-added.
        if not payload and self._cache_payload:
            return self._cache_payload

        self._cache_ts = time.time()
        self._cache_payload = payload
        return payload


# Module-level singleton used by both the Router-instantiated route
# module and any test/inspection that wants to manipulate the in-process
# popular-TV cache. Tests reach in via ``brand_discovery._popular_tv``.
_popular_tv = PopularTvFeedService()


class BrandDiscoveryGetRoutes(RouteModule):
    """Brand config + discovery feeds. The Router auto-discovers
    + instantiates this class + walks its tagged methods at startup."""

    def __init__(
        self,
        *,
        brand_service: BrandConfigService | None = None,
        popular_tv_service: PopularTvFeedService | None = None,
    ) -> None:
        self._brand = brand_service or BrandConfigService()
        self._popular_tv = popular_tv_service or _popular_tv

    @get("/api/branding")
    def handle_branding(self, handler: Any) -> None:
        """Return white-label brand metadata merged over the defaults."""
        handler._json_response(
            HTTPStatus.OK, {"brand": self._brand.resolve()},
        )

    @get("/api/discovery/popular-tv")
    def handle_popular_tv(self, handler: Any) -> None:
        """Return the cached TVMaze-backed popular-TV feed."""
        handler._json_response(
            HTTPStatus.OK, self._popular_tv.fetch(),
        )


__all__ = [
    "BrandDiscoveryGetRoutes",
    "BrandConfigService",
    "PopularTvFeedService",
    "_popular_tv",
]
