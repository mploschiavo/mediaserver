"""Readarr adapter — satisfies ``ArrApp`` for books.

API surface (v1, verified live 2026-04-24):
- ``/api/v1/book``              — list books (one release = one book)
- ``/api/v1/bookfile?bookId=X`` — files for a book
- ``/api/v1/bookfile/{id}``     — DELETE single file
- ``/api/v1/config/mediamanagement``
- ``/api/v1/config/naming``     — ``renameBooks``
- ``/api/v1/qualityprofile``

Readarr is ``v1`` and, like Lidarr, has a parent/child shape
(author → book). The reconciler sees one release per book.
"""

from __future__ import annotations

from typing import Any

from media_stack.adapters.media_integrity._servarr_base import (
    _ServarrBaseAdapter,
)
from media_stack.domain.media_integrity.arr_protocol import MediaFile, MediaRelease


class ReadarrAdapter(_ServarrBaseAdapter):
    name = "readarr"
    api_version = "v1"
    _media_file_endpoint = "bookfile"
    _media_endpoint = "book"
    _parent_file_field = "bookId"

    _MEDIA_MANAGEMENT_FIELDS = {
        "auto_unmonitor_previously_downloaded": "autoUnmonitorPreviouslyDownloadedBooks",
        "use_hardlinks": "copyUsingHardlinks",
        "delete_empty_folders": "deleteEmptyFolders",
        "import_extra_files": "importExtraFiles",
        "extra_file_extensions": "extraFileExtensions",
        "skip_free_space_check": "skipFreeSpaceCheckWhenImporting",
        "minimum_free_space_mb": "minimumFreeSpaceWhenImporting",
        "create_empty_media_folders": "createEmptyAuthorFolders",
        "unmonitor_deleted": "autoUnmonitorDeletedBooks",
    }

    _NAMING_FIELDS = {"rename_files": "renameBooks"}

    # list_releases inherited from _ServarrBaseAdapter (default
    # GET /{_media_endpoint} → list of _release_from_raw rows).

    def _release_from_raw(self, raw: dict[str, Any]) -> MediaRelease | None:
        book_id = raw.get("id")
        if book_id is None:
            return None
        author = raw.get("author") or {}
        author_name = ""
        author_path = ""
        quality_profile_id: int | None = None
        if isinstance(author, dict):
            author_name = str(author.get("authorName", ""))
            author_path = str(author.get("path", ""))
            if author.get("qualityProfileId") is not None:
                quality_profile_id = int(author["qualityProfileId"])
        book_title = str(raw.get("title", ""))
        full_title = f"{author_name} — {book_title}" if author_name else book_title
        release_date = raw.get("releaseDate")
        year: int | None = None
        if isinstance(release_date, str) and len(release_date) >= 4:
            prefix = release_date[:4]
            if prefix.isdigit():
                year = int(prefix)
        return MediaRelease(
            id=str(book_id),
            title=full_title,
            path=author_path,
            year=year,
            quality_profile_id=quality_profile_id,
            monitored=bool(raw.get("monitored", True)),
        )

    # _list_files_for inherited from _ServarrBaseAdapter (default
    # GET /{_media_file_endpoint}?{_parent_file_field}=<id>).

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
