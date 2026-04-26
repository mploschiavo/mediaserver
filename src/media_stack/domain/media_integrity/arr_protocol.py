"""Media-type-neutral protocol every *arr adapter satisfies.

The reconciler and enforcer (landing in turn 2) never reach into a
specific *arr's API shape — they call through this protocol. Every
per-app quirk (Radarr's ``autoUnmonitorPreviouslyDownloadedMovies``
vs. Sonarr's ``...Episodes``; ``/api/v3/moviefile`` vs.
``/api/v3/episodefile?seriesId=X``) is absorbed by the adapter.

Design invariants
-----------------

- **Every identifier is a string.** *arr APIs return ints; we
  stringify at the adapter boundary so the rest of the stack never
  has to care which *arr lent us the id.
- **Every dataclass is frozen.** Reconciler passes these across
  threads without locks.
- **Values never carry secrets.** API keys + bearer tokens are
  adapter-private. The controller's other services never see them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# Domain objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QualityProfile:
    """A profile that ranks releases for a given media slot.

    ``cutoff_id`` is the quality-id at which upgrades stop. Every
    *arr uses the same conceptual shape; adapters flatten the
    per-app nesting into these fields.
    """

    id: int
    name: str
    cutoff_id: int
    # Original items list, opaque. Reconciler doesn't introspect —
    # it calls ``ArrApp.quality_score(file)`` for decisions.
    items: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class MediaRelease:
    """A discrete library slot.

    - Radarr: a movie (one release).
    - Sonarr: an episode (one release per episode — the adapter
      walks series→season→episode so the reconciler sees one row
      per (series, season, episode)).
    - Lidarr: an album (one release).
    - Readarr: a book (one release).

    The reconciler's "≤ 1 file per release" invariant tests against
    ``id``. Duplicate files = multiple ``MediaFile`` rows with the
    same ``release_id``.
    """

    id: str
    title: str
    path: str
    year: int | None = None
    quality_profile_id: int | None = None
    monitored: bool = True


@dataclass(frozen=True)
class MediaFile:
    """A file on disk tracked by the *arr.

    ``source_torrent_hash`` is best-effort; the *arr only records
    the download client id on recently-imported files and loses it
    after the history window rolls over. Empty means "we don't know;
    reconciler can't pause an orphan torrent for this file."
    """

    id: str
    release_id: str
    relative_path: str
    absolute_path: str
    size: int
    quality_name: str
    quality_score: int
    added_at: str
    source_torrent_hash: str = ""


@dataclass(frozen=True)
class AdapterCapabilities:
    """What shape the adapter expects.

    Used by the enforcer to decide which fields to apply and how.
    For example, Radarr 5.x exposes ``autoUnmonitorDeletedMovies``
    but Radarr 4.x doesn't — the adapter probes its API surface at
    construction and reports via this struct so the enforcer skips
    unsupported fields cleanly.
    """

    supports_auto_unmonitor_deleted: bool = True
    supports_rename: bool = True
    supports_hardlinks: bool = True
    supports_quality_profile_cutoff: bool = True
    supports_file_delete: bool = True
    supports_release_listing: bool = True
    probed_field_names: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class ArrApp(Protocol):
    """Every Servarr-family adapter satisfies this shape.

    Bazarr (subtitles) is NOT a Servarr; it satisfies a sibling
    protocol. See ``bazarr_adapter.py`` (turn 3).
    """

    name: str               # "radarr" | "sonarr" | "lidarr" | "readarr"
    api_version: str        # "v3" | "v1"
    media_root: str         # "/media/movies", "/media/tv", ...
    capabilities: AdapterCapabilities

    # --- config surface (Enforcer) -------------------------------------

    def get_media_management(self) -> dict[str, Any]:
        """GET /api/v{ver}/config/mediamanagement. Raw response."""
        ...

    def put_media_management(self, cfg: dict[str, Any]) -> None:
        """PUT /api/v{ver}/config/mediamanagement. Adapter supplies
        the ``id`` field if the app needs it."""
        ...

    def get_naming(self) -> dict[str, Any]:
        """GET /api/v{ver}/config/naming. Raw response."""
        ...

    def put_naming(self, cfg: dict[str, Any]) -> None:
        """PUT /api/v{ver}/config/naming."""
        ...

    def media_management_field_map(self) -> dict[str, str]:
        """Canonical-key → app-API-field translation for /mediamanagement.

        Radarr's ``auto_unmonitor_previously_downloaded`` →
        ``autoUnmonitorPreviouslyDownloadedMovies``; Sonarr's same
        key → ``...Episodes``; etc. Missing keys mean "this adapter
        doesn't support this canonical field" and the enforcer skips."""
        ...

    def naming_field_map(self) -> dict[str, str]:
        """Canonical-key → app-API-field translation for /naming.

        Only ``rename_files`` is canonical today (Radarr:
        ``renameMovies``, Sonarr: ``renameEpisodes``, etc.)."""
        ...

    # --- inventory surface (Reconciler) --------------------------------

    def list_releases(self) -> list[MediaRelease]:
        """Every release the *arr tracks. Adapter flattens nested
        shapes (e.g., Sonarr series→episode) so the reconciler sees
        a flat list."""
        ...

    def list_files_for(self, release_id: str) -> list[MediaFile]:
        """Files the *arr has imported for this release. ≥ 2 rows
        here means the reconciler has a duplicate to resolve."""
        ...

    def delete_file(self, file_id: str) -> None:
        """Atomically delete file record + disk file.

        *arr's DELETE /api/v{ver}/{media}file/{id} does both in the
        same transaction — the adapter surfaces failure rather than
        swallowing, so the reconciler can report it as a
        ``failures`` entry.
        """
        ...

    def quality_profiles(self) -> list[QualityProfile]:
        """All quality profiles the *arr knows about."""
        ...

    def quality_score(self, file: MediaFile) -> int:
        """Resolve the *arr's own quality score for a file. Used by
        the reconciler to pick a winner deterministically."""
        ...

    def list_releases_for_file(self, file_id: str) -> list[str]:
        """Returns every release id this file backs.

        For Sonarr an episodefile can span multiple episodes (a
        double-episode file backing both ep 1 and ep 2). The
        reconciler must never delete a file that backs a release
        OTHER than the one currently being reconciled. For Radarr/
        Lidarr/Readarr this is 1:1 — one row backs exactly one
        release. Implementations should return ``[]`` when the
        linkage cannot be confirmed; the reconciler treats empty as
        "skip delete, surface for review"."""
        ...


__all__ = [
    "AdapterCapabilities",
    "ArrApp",
    "MediaFile",
    "MediaRelease",
    "QualityProfile",
]
