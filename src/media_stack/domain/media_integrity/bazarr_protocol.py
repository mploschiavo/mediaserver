"""Bazarr adapter protocol — subtitle-management sibling to ``ArrApp``.

Bazarr is NOT a Servarr. It uses a different API shape:

- A single ``/api/system/settings`` blob (no section split).
- Subtitle resources keyed by ``(release, language, forced, hi)``
  rather than a single file id.
- Rename/ignore-deleted knobs have different names than Radarr's
  equivalents.

Dupe semantics are different too: the same video can legitimately
have multiple subtitle files if they differ in (language, forced,
hi). What is NOT legitimate is two ``.en.srt`` files from two
different providers — that's the dupe case the reconciler heals.

Design invariants mirror ``arr_protocol``:
- Every identifier is a string.
- Every dataclass is frozen.
- Values never carry secrets.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# Domain objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SubtitleRelease:
    """A video file that may have subtitles attached.

    ``kind`` is ``"movie"`` or ``"episode"`` — the reconciler uses
    it to key duplicate detection and to hit the right Bazarr
    endpoint on delete.
    """

    id: str
    kind: str  # "movie" | "episode"
    title: str
    path: str


@dataclass(frozen=True)
class SubtitleFile:
    """A subtitle attached to a ``SubtitleRelease``.

    Duplicate detection groups by ``(release_id, release_kind,
    language, forced, hi)``. Two files in the same group means the
    reconciler needs to pick one to keep.

    ``score`` is Bazarr's own 0-100 quality rating for the
    subtitle. Higher is better. ``provider`` is the source slug
    ("opensubtitles.com", "subscene", etc.) for observability only.
    """

    release_id: str
    release_kind: str  # "movie" | "episode"
    path: str  # absolute path on disk; primary identifier
    language: str  # BCP-47 short code: "en", "es-MX", ...
    forced: bool = False
    hi: bool = False  # hearing-impaired
    provider: str = ""
    score: int = 0
    added_at: str = ""
    size: int = 0


@dataclass(frozen=True)
class BazarrCapabilities:
    """What shape the Bazarr install exposes.

    Bazarr's settings blob evolves; the adapter probes what fields
    exist so the enforcer never PUTs a key the Bazarr instance
    will reject."""

    supports_rename: bool = True
    supports_auto_sync: bool = True
    supports_upgrade: bool = True
    supports_ignore_deleted: bool = True
    supports_subtitle_delete: bool = True
    probed_setting_keys: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class BazarrApp(Protocol):
    """Every Bazarr adapter satisfies this shape.

    Separate from ``ArrApp`` because Bazarr's API shape diverges
    enough that a single base protocol would accumulate too many
    ``if self.name == "bazarr"`` branches to stay honest.
    """

    name: str  # "bazarr"
    api_version: str  # typically unversioned — this is left for future use
    media_root: str  # the library root Bazarr watches (unused today)
    capabilities: BazarrCapabilities

    # --- settings surface (Enforcer) -----------------------------------

    def get_settings(self) -> dict[str, Any]:
        """``GET /api/system/settings``. Returns the raw blob.

        Bazarr folds every knob into one big object — adapter
        returns it unmodified; the enforcer merges its patch and
        PUTs the whole thing back."""
        ...

    def put_settings(self, cfg: dict[str, Any]) -> None:
        """``POST /api/system/settings``. Full-document replace."""
        ...

    def settings_field_map(self) -> dict[str, str]:
        """Canonical-key → Bazarr-settings-field translation.

        Canonical keys (same spirit as the Servarr policy):
        - ``rename_files``    → the ``subfolder`` + naming knob
        - ``auto_sync``       → auto-sync on import
        - ``upgrade_allowed`` → "Upgrade Previously Downloaded Subs"
        - ``ignore_deleted``  → "Don't Monitor Deleted Episodes/Movies"
        """
        ...

    # --- inventory surface (Reconciler) --------------------------------

    def list_subtitle_releases(self) -> list[SubtitleRelease]:
        """Every movie + episode Bazarr knows about, flattened to
        a single list of ``SubtitleRelease``."""
        ...

    def list_subtitles_for(
        self, release_id: str, release_kind: str
    ) -> list[SubtitleFile]:
        """Subtitles attached to ``(release_id, release_kind)``.

        ≥ 2 files in the same (language, forced, hi) group means
        the reconciler has a duplicate to resolve."""
        ...

    def delete_subtitle(self, subtitle: SubtitleFile) -> None:
        """Delete a subtitle (file on disk + Bazarr's record).

        Bazarr's DELETE takes the release id + path in the body;
        the adapter unpacks the ``SubtitleFile`` to construct the
        correct call."""
        ...

    def subtitle_score(self, subtitle: SubtitleFile) -> int:
        """Resolve Bazarr's own score for a subtitle. Used by the
        reconciler as the primary winner-picking rule."""
        ...


__all__ = [
    "BazarrApp",
    "BazarrCapabilities",
    "SubtitleFile",
    "SubtitleRelease",
]
