"""Typed value-object for one media type the stack manages.

A ``MediaType`` is the 1:N relationship between a media category
(``tv`` / ``movies`` / ``music`` / ``books``), the *arr that manages
it, and the filesystem paths that category occupies in each
container's view (the *arr/Jellyfin view, the download client view,
the controller pod's k8s view, the controller pod's compose view).

The catalog itself lives in ``contracts/defaults/media_types.yaml``;
the loader (``infrastructure.media.catalog``) deserializes one
``MediaType`` per entry. This dataclass is in the domain layer
because it's a pure value object — no I/O, no platform awareness.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class MediaType:
    """One media type the stack manages end-to-end."""

    name: str
    """Stable lowercase identifier — ``tv`` | ``movies`` | ``music`` | ``books``."""

    arr_name: str
    """The *arr's display name as registered in Jellyseerr / arr.yaml —
    ``Sonarr`` | ``Radarr`` | ``Lidarr`` | ``Readarr``."""

    arr_lower: str
    """Lowercased *arr name — used as the contract-services file id and
    the dispatch key in factories."""

    arr_api_version: str
    """REST API version path segment — ``v3`` for sonarr/radarr,
    ``v1`` for lidarr/readarr."""

    arr_scan_command: str
    """Webhook command name the *arr emits when a download completes —
    ``DownloadedEpisodesScan`` etc."""

    qbit_category: str
    """qBittorrent category label for this type. Today identical to
    ``name`` (``tv`` / ``movies`` / ``music`` / ``books``); kept as a
    distinct field so a future qBit category rename doesn't have to
    fan out to every consumer."""

    sab_category: str
    """SABnzbd category label. Same shape as ``qbit_category``."""

    # --- filesystem views --------------------------------------------
    # Same physical files; different mount paths per container.

    library_path: str
    """The *arr / Jellyfin / Plex container's view of the library
    (e.g. ``/media/tv``). This is what *arr's ``root_folder`` config
    points at, what Jellyseerr advertises to its frontend, and what
    the media-integrity adapters scan."""

    controller_library_path: str
    """The controller pod's view of the library (k8s convention,
    e.g. ``/srv-stack/media/tv``). Used by controller-side code that
    walks the library — sidecar prewarm, hygiene cleanup, drift
    detection."""

    controller_library_path_compose: str
    """The controller pod's view of the library on compose (e.g.
    ``/media/tv``). Compose currently mounts the controller's media
    volume at ``/media/...`` rather than ``/srv-stack/media/...``;
    cross-platform code searches both paths until the substrates align."""

    torrents_completed_path: str
    """The qBittorrent container's view of where this type's torrents
    finish (e.g. ``/data/torrents/completed/tv``). Configured into
    qbit's category save_path; *arr reads it to find imports."""

    usenet_completed_path: str
    """The SABnzbd container's view of where this type's usenet
    downloads finish (e.g. ``/data/usenet/completed/tv``)."""

    controller_torrents_completed: str
    """The controller pod's view of where torrents complete
    (e.g. ``/srv-stack/data/torrents/completed/tv``). Same physical
    dir as ``torrents_completed_path``, different mount."""

    controller_usenet_completed: str
    """The controller pod's view of where usenet downloads complete
    (e.g. ``/srv-stack/data/usenet/completed/tv``)."""

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MediaType":
        """Build a ``MediaType`` from one entry in
        ``contracts/defaults/media_types.yaml``. Raises ``ValueError``
        when a required field is missing — the catalog must be complete
        for every type the rest of the stack depends on."""
        src = dict(data or {})

        def _required(key: str) -> str:
            value = src.get(key)
            if value is None or str(value).strip() == "":
                raise ValueError(
                    f"media_types entry missing required field {key!r} "
                    f"(got: {data!r})",
                )
            return str(value).strip()

        return cls(
            name=_required("name"),
            arr_name=_required("arr_name"),
            arr_lower=_required("arr_lower"),
            arr_api_version=_required("arr_api_version"),
            arr_scan_command=_required("arr_scan_command"),
            qbit_category=_required("qbit_category"),
            sab_category=_required("sab_category"),
            library_path=_required("library_path"),
            controller_library_path=_required("controller_library_path"),
            controller_library_path_compose=_required("controller_library_path_compose"),
            torrents_completed_path=_required("torrents_completed_path"),
            usenet_completed_path=_required("usenet_completed_path"),
            controller_torrents_completed=_required("controller_torrents_completed"),
            controller_usenet_completed=_required("controller_usenet_completed"),
        )


__all__ = ["MediaType"]
