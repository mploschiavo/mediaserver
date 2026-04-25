"""Radarr adapter — satisfies ``ArrApp`` for movies.

API surface (v3, verified live 2026-04-24):
- ``/api/v3/movie``           — list movies (each = MediaRelease)
- ``/api/v3/moviefile?movieId=X`` — files for a movie
- ``/api/v3/moviefile/{id}``  — DELETE single file
- ``/api/v3/config/mediamanagement`` — the dupe-prevention knobs
- ``/api/v3/config/naming``   — ``renameMovies``
- ``/api/v3/qualityprofile``  — all profiles

Radarr is the simplest Servarr: one movie = one release = at most
one file. The reconciler's ``≥ 2 files`` signal therefore means
literal duplicate imports.
"""

from __future__ import annotations

from typing import Any

from media_stack.services.media_integrity.adapters._servarr_base import (
    _ServarrBaseAdapter,
)
from media_stack.services.media_integrity.arr_protocol import MediaFile, MediaRelease


class RadarrAdapter(_ServarrBaseAdapter):
    name = "radarr"
    api_version = "v3"
    _media_file_endpoint = "moviefile"
    _media_endpoint = "movie"
    _parent_file_field = "movieId"

    _MEDIA_MANAGEMENT_FIELDS = {
        "auto_unmonitor_previously_downloaded": "autoUnmonitorPreviouslyDownloadedMovies",
        "use_hardlinks": "copyUsingHardlinks",
        "delete_empty_folders": "deleteEmptyFolders",
        "import_extra_files": "importExtraFiles",
        "extra_file_extensions": "extraFileExtensions",
        "skip_free_space_check": "skipFreeSpaceCheckWhenImporting",
        "minimum_free_space_mb": "minimumFreeSpaceWhenImporting",
        "create_empty_media_folders": "createEmptyMovieFolders",
        # Radarr 5.x exposes this; 4.x doesn't — probe decides.
        "unmonitor_deleted": "autoUnmonitorDeletedMovies",
    }

    _NAMING_FIELDS = {"rename_files": "renameMovies"}

    def list_releases(self) -> list[MediaRelease]:
        raw = self._request_json("GET", "/movie")
        if not isinstance(raw, list):
            return []
        out: list[MediaRelease] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            release = self._release_from_raw(item)
            if release is not None:
                out.append(release)
        return out

    def _release_from_raw(self, raw: dict[str, Any]) -> MediaRelease | None:
        release_id = raw.get("id")
        if release_id is None:
            return None
        return MediaRelease(
            id=str(release_id),
            title=str(raw.get("title", "")),
            path=str(raw.get("path", "")),
            year=int(raw["year"]) if raw.get("year") is not None else None,
            quality_profile_id=(
                int(raw["qualityProfileId"])
                if raw.get("qualityProfileId") is not None
                else None
            ),
            monitored=bool(raw.get("monitored", True)),
        )

    def _list_files_for(self, release_id: str) -> list[MediaFile]:
        raw = self._request_json("GET", f"/moviefile?movieId={release_id}")
        if not isinstance(raw, list):
            return []
        out: list[MediaFile] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            mf = self._file_from_raw(item, release_id)
            if mf is not None:
                out.append(mf)
        return out

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
