"""Bazarr adapter — satisfies ``BazarrApp`` for subtitles.

API surface (verified live on the k8s cluster, 2026-04-24):

- ``GET /api/system/settings`` — full settings blob (one flat
  object with nested sections)
- ``POST /api/system/settings`` — replace the blob (merge is the
  enforcer's job)
- ``GET /api/movies`` — radarr-backed movie index
- ``GET /api/episodes?seriesid=X`` — sonarr-backed episodes per series
- ``GET /api/series`` — sonarr-backed series
- ``GET /api/movies/subtitles?radarrid=X`` — subtitles for a movie
- ``GET /api/episodes/subtitles?episodeid=X`` — subtitles for an
  episode (some versions expose ``sonarr_episode_id``)
- ``DELETE /api/subtitles`` — body = ``{"type", "id",
  "subtitles_path", "language"}``

Bazarr uses ``X-API-KEY`` as the auth header (not ``X-Api-Key`` —
yes, the case matters for some proxies).

The adapter purposefully does NOT fetch every episode on start —
``list_subtitle_releases`` walks series→episodes, which can be
slow on big libraries. The reconciler calls this once per pass.
"""

from __future__ import annotations

import json
from typing import Any

from media_stack.adapters.media_integrity._servarr_base import (
    DEFAULT_TIMEOUT_SEC,
    HttpClient,
    HttpResponse,
    ServarrHttpError,
    UrllibHttpClient,
)
from media_stack.domain.media_integrity.bazarr_protocol import (
    BazarrCapabilities,
    SubtitleFile,
    SubtitleRelease,
)


# Canonical → Bazarr settings key map. Values are
# ``section.field`` paths into the settings blob; the base
# ``_apply_settings_patch`` walks them.
_BAZARR_SETTINGS_FIELDS: dict[str, str] = {
    # "general.upgrade_subs": corresponds to "Upgrade Previously Downloaded Subs"
    "upgrade_allowed": "general.upgrade_subs",
    # Don't monitor subs for files the user deleted from disk.
    "ignore_deleted": "general.ignore_deleted_episodes",
    # Rename subtitle files to match the canonical video filename.
    "rename_files": "general.subfolder_custom",
    # Auto-sync subtitles on import so we don't fire a second
    # download for the same episode+language+flag combo.
    "auto_sync": "general.auto_sync_subs",
}


class _BazarrPayloadHelpers:
    """Parsing helpers for Bazarr's loose JSON shapes.

    Carved out of ``BazarrAdapter`` to keep its method count under the
    god-class ratchet — the three helpers are pure-data transforms
    (no I/O, no state) so they're naturally a sub-helper rather than
    methods on the HTTP-touching adapter.
    """

    def unwrap_items(self, raw: Any) -> list[Any]:
        """Bazarr's list endpoints either return a top-level list or
        wrap in ``{"data": [...]}`` depending on version. Accept both."""
        if isinstance(raw, list):
            return raw
        if isinstance(raw, dict):
            data = raw.get("data")
            if isinstance(data, list):
                return data
        return []

    def subtitle_from_raw(
        self, raw: dict[str, Any], release_id: str, release_kind: str
    ) -> SubtitleFile | None:
        path = raw.get("path") or raw.get("subtitles_path")
        if not path or not isinstance(path, str):
            return None
        language = str(raw.get("language") or raw.get("code2") or "")
        forced = bool(raw.get("forced", False))
        hi = bool(raw.get("hi", False))
        provider = str(raw.get("provider") or raw.get("providerName") or "")
        score_raw = raw.get("score")
        try:
            score = int(score_raw) if score_raw is not None else 0
        except (TypeError, ValueError):
            score = 0
        added_at = str(raw.get("timestamp") or raw.get("date") or "")
        size_raw = raw.get("file_size")
        try:
            size = int(size_raw) if size_raw is not None else 0
        except (TypeError, ValueError):
            size = 0
        return SubtitleFile(
            release_id=release_id,
            release_kind=release_kind,
            path=path,
            language=language,
            forced=forced,
            hi=hi,
            provider=provider,
            score=score,
            added_at=added_at,
            size=size,
        )

    def flatten_keys(self, obj: Any, prefix: str = "") -> list[str]:
        """Return dotted key paths into a nested dict — used by the
        capability probe so the settings patch knows which keys to write."""
        out: list[str] = []
        if isinstance(obj, dict):
            for k, v in obj.items():
                path = f"{prefix}.{k}" if prefix else str(k)
                if isinstance(v, dict):
                    out.extend(self.flatten_keys(v, path))
                else:
                    out.append(path)
        return out


class BazarrAdapter:
    """Bazarr HTTP adapter — standalone from the Servarr base
    because its API shape is sufficiently different that the
    shared plumbing would accumulate too many ``if`` branches."""

    name = "bazarr"
    api_version = "v1"  # placeholder — Bazarr's API is unversioned

    _SETTINGS_FIELDS: dict[str, str] = _BAZARR_SETTINGS_FIELDS
    _PAYLOAD_HELPERS = _BazarrPayloadHelpers()

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        media_root: str = "",
        http_client: HttpClient | None = None,
        timeout_sec: float = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self.media_root = media_root
        self._http = http_client or UrllibHttpClient()
        self._timeout = timeout_sec
        self.capabilities = self._probe_capabilities()

    # -- BazarrApp protocol surface -------------------------------------

    def settings_field_map(self) -> dict[str, str]:
        return dict(self._SETTINGS_FIELDS)

    def get_settings(self) -> dict[str, Any]:
        cfg = self._request_json("GET", "/api/system/settings")
        if not isinstance(cfg, dict):
            raise ServarrHttpError(
                200,
                self._url("/api/system/settings"),
                b"expected object, got " + json.dumps(cfg).encode(),
            )
        return cfg

    def put_settings(self, cfg: dict[str, Any]) -> None:
        self._request_json("POST", "/api/system/settings", body=cfg)

    def list_subtitle_releases(self) -> list[SubtitleRelease]:
        out: list[SubtitleRelease] = []
        out.extend(self._list_movie_releases())
        out.extend(self._list_episode_releases())
        return out

    def _list_movie_releases(self) -> list[SubtitleRelease]:
        raw = self._request_json("GET", "/api/movies")
        items = self._PAYLOAD_HELPERS.unwrap_items(raw)
        out: list[SubtitleRelease] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            movie_id = item.get("radarrId") or item.get("radarr_id") or item.get("id")
            if movie_id is None:
                continue
            out.append(
                SubtitleRelease(
                    id=str(movie_id),
                    kind="movie",
                    title=str(item.get("title", "")),
                    path=str(item.get("path", "")),
                )
            )
        return out

    def _list_episode_releases(self) -> list[SubtitleRelease]:
        series_raw = self._request_json("GET", "/api/series")
        series_items = self._PAYLOAD_HELPERS.unwrap_items(series_raw)
        out: list[SubtitleRelease] = []
        for series in series_items:
            if not isinstance(series, dict):
                continue
            series_id = (
                series.get("sonarrSeriesId")
                or series.get("sonarr_series_id")
                or series.get("id")
            )
            if series_id is None:
                continue
            series_title = str(series.get("title", ""))
            episodes = self._request_json(
                "GET", f"/api/episodes?seriesid={series_id}"
            )
            ep_items = self._PAYLOAD_HELPERS.unwrap_items(episodes)
            for ep in ep_items:
                if not isinstance(ep, dict):
                    continue
                ep_id = (
                    ep.get("sonarrEpisodeId")
                    or ep.get("sonarr_episode_id")
                    or ep.get("id")
                )
                if ep_id is None:
                    continue
                season = int(ep.get("season", 0))
                ep_num = int(ep.get("episode", 0))
                ep_title = str(ep.get("title", ""))
                full_title = (
                    f"{series_title} S{season:02d}E{ep_num:02d} — {ep_title}"
                    if ep_title
                    else f"{series_title} S{season:02d}E{ep_num:02d}"
                )
                out.append(
                    SubtitleRelease(
                        id=str(ep_id),
                        kind="episode",
                        title=full_title,
                        path=str(ep.get("path", "")),
                    )
                )
        return out

    def list_subtitles_for(
        self, release_id: str, release_kind: str
    ) -> list[SubtitleFile]:
        if release_kind == "movie":
            path = f"/api/movies/subtitles?radarrid={release_id}"
        elif release_kind == "episode":
            path = f"/api/episodes/subtitles?episodeid={release_id}"
        else:
            return []
        raw = self._request_json("GET", path)
        items = self._PAYLOAD_HELPERS.unwrap_items(raw)
        out: list[SubtitleFile] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            sub = self._PAYLOAD_HELPERS.subtitle_from_raw(item, release_id, release_kind)
            if sub is not None:
                out.append(sub)
        return out

    def delete_subtitle(self, subtitle: SubtitleFile) -> None:
        body = {
            "type": subtitle.release_kind,
            "id": int(subtitle.release_id) if subtitle.release_id.isdigit() else subtitle.release_id,
            "subtitles_path": subtitle.path,
            "language": subtitle.language,
            "forced": subtitle.forced,
            "hi": subtitle.hi,
        }
        self._request_raw("DELETE", "/api/subtitles", body=body)

    def subtitle_score(self, subtitle: SubtitleFile) -> int:
        return subtitle.score

    # -- HTTP plumbing (mirrors _ServarrBaseAdapter, but with
    # Bazarr's header casing and un-prefixed paths) ---------------------

    def _url(self, path: str) -> str:
        return f"{self._base_url}{path}"

    def _headers(self, *, json_body: bool) -> dict[str, str]:
        headers = {"X-API-KEY": self._api_key, "Accept": "application/json"}
        if json_body:
            headers["Content-Type"] = "application/json"
        return headers

    def _request_raw(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | list[Any] | None = None,
    ) -> HttpResponse:
        url = self._url(path)
        raw_body: bytes | None = None
        has_body = body is not None
        if has_body:
            raw_body = json.dumps(body).encode("utf-8")
        resp = self._http.request(
            method,
            url,
            headers=self._headers(json_body=has_body),
            body=raw_body,
            timeout=self._timeout,
        )
        if resp.status < 200 or resp.status >= 300:
            raise ServarrHttpError(resp.status, url, resp.body)
        return resp

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | list[Any] | None = None,
    ) -> Any:
        resp = self._request_raw(method, path, body=body)
        if not resp.body:
            return {}
        return json.loads(resp.body.decode("utf-8"))

    def _probe_capabilities(self) -> BazarrCapabilities:
        try:
            cfg = self.get_settings()
        except ServarrHttpError:
            return BazarrCapabilities()
        keys = tuple(sorted(self._PAYLOAD_HELPERS.flatten_keys(cfg)))
        return BazarrCapabilities(probed_setting_keys=keys)

