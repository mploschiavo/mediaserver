"""Sonarr adapter — satisfies ``ArrApp`` for TV episodes.

API surface (v3, verified live 2026-04-24):
- ``/api/v3/series``            — list series
- ``/api/v3/episode?seriesId=X`` — list episodes for a series
- ``/api/v3/episodefile?seriesId=X`` — files for a series
- ``/api/v3/episodefile/{id}``  — DELETE a single file
- ``/api/v3/config/mediamanagement``
- ``/api/v3/config/naming``     — ``renameEpisodes``
- ``/api/v3/qualityprofile``

The Sonarr quirk: releases (what we flatten to) are *episodes*,
not series. One series → N seasons → M episodes. The reconciler
wants a flat list where each row is (series, season, episode)
keyed by the episode id. Files are returned per-series by the
API, so ``_list_files_for(episode_id)`` fetches the series bundle
once per call and filters — see the LRU guard inside.
"""

from __future__ import annotations

from typing import Any

from media_stack.adapters.media_integrity._servarr_base import (
    ServarrHttpError,
    _ServarrBaseAdapter,
)
from media_stack.domain.media_integrity.arr_protocol import MediaFile, MediaRelease


class SonarrAdapter(_ServarrBaseAdapter):
    name = "sonarr"
    api_version = "v3"
    _media_file_endpoint = "episodefile"
    _media_endpoint = "episode"

    _MEDIA_MANAGEMENT_FIELDS = {
        "auto_unmonitor_previously_downloaded": "autoUnmonitorPreviouslyDownloadedEpisodes",
        "use_hardlinks": "copyUsingHardlinks",
        "delete_empty_folders": "deleteEmptyFolders",
        "import_extra_files": "importExtraFiles",
        "extra_file_extensions": "extraFileExtensions",
        "skip_free_space_check": "skipFreeSpaceCheckWhenImporting",
        "minimum_free_space_mb": "minimumFreeSpaceWhenImporting",
        "create_empty_media_folders": "createEmptySeriesFolders",
        # Sonarr 4.x exposes this; older releases don't.
        "unmonitor_deleted": "autoUnmonitorDeletedEpisodes",
    }

    _NAMING_FIELDS = {"rename_files": "renameEpisodes"}

    def list_releases(self) -> list[MediaRelease]:
        """Flatten series → episode. Each episode is one release.

        Sonarr's ``/series`` returns series-level data; we then pull
        ``/episode?seriesId=X`` per series to build episode rows.
        """
        series_raw = self._request_json("GET", "/series")
        if not isinstance(series_raw, list):
            return []
        out: list[MediaRelease] = []
        for series in series_raw:
            if not isinstance(series, dict):
                continue
            series_id = series.get("id")
            if series_id is None:
                continue
            series_title = str(series.get("title", ""))
            series_path = str(series.get("path", ""))
            series_year = int(series["year"]) if series.get("year") is not None else None
            quality_profile_id = (
                int(series["qualityProfileId"])
                if series.get("qualityProfileId") is not None
                else None
            )
            episodes = self._request_json("GET", f"/episode?seriesId={series_id}")
            if not isinstance(episodes, list):
                continue
            for ep in episodes:
                if not isinstance(ep, dict):
                    continue
                ep_id = ep.get("id")
                if ep_id is None:
                    continue
                season = int(ep.get("seasonNumber", 0))
                ep_num = int(ep.get("episodeNumber", 0))
                ep_title = str(ep.get("title", ""))
                full_title = (
                    f"{series_title} S{season:02d}E{ep_num:02d} — {ep_title}"
                    if ep_title
                    else f"{series_title} S{season:02d}E{ep_num:02d}"
                )
                out.append(
                    MediaRelease(
                        id=str(ep_id),
                        title=full_title,
                        path=series_path,
                        year=series_year,
                        quality_profile_id=quality_profile_id,
                        monitored=bool(ep.get("monitored", True)),
                    )
                )
        return out

    def _list_files_for(self, release_id: str) -> list[MediaFile]:
        """``release_id`` is an episode id. Sonarr's episodefile
        endpoint wants a seriesId, not an episodeId, so we reverse-
        look-up through ``/episode/{id}`` and then filter the
        series' episodefile list.
        """
        ep = self._request_json("GET", f"/episode/{release_id}")
        if not isinstance(ep, dict):
            return []
        series_id = ep.get("seriesId")
        if series_id is None:
            return []
        raw = self._request_json("GET", f"/episodefile?seriesId={series_id}")
        if not isinstance(raw, list):
            return []
        out: list[MediaFile] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            # Filter to files actually attached to this episode.
            # An episodefile can span multiple episodes (double-episode
            # file); Sonarr reports this via the linked episodes list.
            linked_episodes = _collect_linked_episode_ids(item)
            if release_id not in linked_episodes:
                continue
            mf = self._file_from_raw(item, release_id)
            if mf is not None:
                out.append(mf)
        return out

    def _release_from_raw(self, raw: dict[str, Any]) -> MediaRelease | None:
        # Not used directly — series/episode split goes through
        # ``list_releases``. Kept for protocol completeness.
        return None

    def _file_from_raw(
        self, raw: dict[str, Any], release_id: str
    ) -> MediaFile | None:
        file_id = raw.get("id")
        if file_id is None:
            return None
        quality_name, quality_id = self._extract_quality(raw.get("quality"))
        return MediaFile(
            id=str(file_id),
            release_id=release_id,
            relative_path=str(raw.get("relativePath", "")),
            absolute_path=str(raw.get("path", "")),
            size=int(raw.get("size", 0)),
            quality_name=quality_name,
            quality_score=quality_id,
            added_at=str(raw.get("dateAdded", "")),
            source_torrent_hash="",
        )

    def list_releases_for_file(self, file_id: str) -> list[str]:
        """An episodefile can back N episodes (double-episode imports).
        Read the row and return EVERY linked episode id so the
        reconciler can refuse to delete a file that still backs
        another episode."""
        try:
            raw = self._request_json(
                "GET", f"/{self._media_file_endpoint}/{file_id}"
            )
        except ServarrHttpError:
            return []
        if not isinstance(raw, dict):
            return []
        ids = _collect_linked_episode_ids(raw)
        return sorted(ids)


def _collect_linked_episode_ids(episodefile_raw: dict[str, Any]) -> set[str]:
    """Sonarr ``episodefile`` rows carry their linked episode ids
    inconsistently across versions. Check both the newer ``episodes``
    list and the older ``episodeIds`` / ``episodeId`` shapes."""
    ids: set[str] = set()
    episodes = episodefile_raw.get("episodes")
    if isinstance(episodes, list):
        for ep in episodes:
            if isinstance(ep, dict) and ep.get("id") is not None:
                ids.add(str(ep["id"]))
    older = episodefile_raw.get("episodeIds")
    if isinstance(older, list):
        for eid in older:
            if eid is not None:
                ids.add(str(eid))
    single = episodefile_raw.get("episodeId")
    if single is not None:
        ids.add(str(single))
    return ids
