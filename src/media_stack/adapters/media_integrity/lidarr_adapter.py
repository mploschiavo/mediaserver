"""Lidarr adapter — satisfies ``ArrApp`` for music albums.

API surface (v1, verified live 2026-04-24):
- ``/api/v1/album``              — list albums (one release = one album)
- ``/api/v1/trackfile?albumId=X`` — files for an album
- ``/api/v1/trackfile/{id}``     — DELETE single file
- ``/api/v1/config/mediamanagement``
- ``/api/v1/config/naming``      — ``renameTracks``
- ``/api/v1/qualityprofile``

Lidarr's api_version is ``v1`` (not ``v3`` like Radarr/Sonarr).
An album can legitimately have multiple trackfiles (one per
track); the reconciler's ``≥ 2 files per release`` test is
expressed in *distinct tracks* — the adapter returns one
MediaFile per trackfile, and the reconciler groups by
(album_id, track_number).
"""

from __future__ import annotations

from typing import Any

from media_stack.adapters.media_integrity._servarr_base import (
    _ServarrBaseAdapter,
)
from media_stack.domain.media_integrity.arr_protocol import MediaFile, MediaRelease


class LidarrAdapter(_ServarrBaseAdapter):
    name = "lidarr"
    api_version = "v1"
    _media_file_endpoint = "trackfile"
    _media_endpoint = "album"
    _parent_file_field = "albumId"

    _MEDIA_MANAGEMENT_FIELDS = {
        "auto_unmonitor_previously_downloaded": "autoUnmonitorPreviouslyDownloadedTracks",
        "use_hardlinks": "copyUsingHardlinks",
        "delete_empty_folders": "deleteEmptyFolders",
        "import_extra_files": "importExtraFiles",
        "extra_file_extensions": "extraFileExtensions",
        "skip_free_space_check": "skipFreeSpaceCheckWhenImporting",
        "minimum_free_space_mb": "minimumFreeSpaceWhenImporting",
        "create_empty_media_folders": "createEmptyArtistFolders",
        "unmonitor_deleted": "autoUnmonitorDeletedTracks",
    }

    _NAMING_FIELDS = {"rename_files": "renameTracks"}

    def list_releases(self) -> list[MediaRelease]:
        raw = self._request_json("GET", "/album")
        if not isinstance(raw, list):
            return []
        out: list[MediaRelease] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            rel = self._release_from_raw(item)
            if rel is not None:
                out.append(rel)
        return out

    def _release_from_raw(self, raw: dict[str, Any]) -> MediaRelease | None:
        album_id = raw.get("id")
        if album_id is None:
            return None
        artist = raw.get("artist") or {}
        artist_name = ""
        if isinstance(artist, dict):
            artist_name = str(artist.get("artistName", ""))
        album_title = str(raw.get("title", ""))
        full_title = f"{artist_name} — {album_title}" if artist_name else album_title
        return MediaRelease(
            id=str(album_id),
            title=full_title,
            path=str(
                (artist.get("path") if isinstance(artist, dict) else "") or ""
            ),
            year=(
                int(raw["releaseDate"][:4])
                if isinstance(raw.get("releaseDate"), str) and len(raw["releaseDate"]) >= 4
                and raw["releaseDate"][:4].isdigit()
                else None
            ),
            quality_profile_id=(
                int(artist["qualityProfileId"])
                if isinstance(artist, dict) and artist.get("qualityProfileId") is not None
                else None
            ),
            monitored=bool(raw.get("monitored", True)),
        )

    def _list_files_for(self, release_id: str) -> list[MediaFile]:
        raw = self._request_json("GET", f"/trackfile?albumId={release_id}")
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
