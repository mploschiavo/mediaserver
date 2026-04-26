"""Per-technology behavioral traits for Servarr apps.

Platform code uses these lookups instead of checking implementation names
directly, keeping service-specific knowledge in the app layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class DiscoveryKickoffTraits:
    """Traits that govern discovery kickoff behavior for a technology."""

    #: Commands to run during initial sync kickoff (empty = skip kickoff).
    kickoff_commands: list[str] = field(default_factory=list)
    #: API endpoint to check for existing library items (e.g. "/artist").
    seed_check_endpoint: str = ""


@dataclass(frozen=True)
class ImportListTraits:
    """Traits that govern import-list behavior for a technology."""

    #: Whether this technology requires a metadata profile ID on import lists.
    requires_metadata_profile: bool = False


# ── Technology trait registries ──────────────────────────────────────

_DISCOVERY_KICKOFF_TRAITS: dict[str, DiscoveryKickoffTraits] = {
    "lidarr": DiscoveryKickoffTraits(
        kickoff_commands=["MissingAlbumSearch", "RssSync"],
        seed_check_endpoint="/artist",
    ),
    "readarr": DiscoveryKickoffTraits(
        kickoff_commands=["MissingBookSearch", "RssSync"],
        seed_check_endpoint="/author",
    ),
}

_IMPORT_LIST_TRAITS: dict[str, ImportListTraits] = {
    "lidarr": ImportListTraits(requires_metadata_profile=True),
    "readarr": ImportListTraits(requires_metadata_profile=True),
}


def get_discovery_kickoff_traits(implementation: str) -> DiscoveryKickoffTraits | None:
    """Look up discovery kickoff traits for a technology, or None if not applicable."""
    return _DISCOVERY_KICKOFF_TRAITS.get(implementation.strip().lower())


def get_import_list_traits(implementation: str) -> ImportListTraits:
    """Look up import-list traits for a technology."""
    return _IMPORT_LIST_TRAITS.get(
        implementation.strip().lower(),
        ImportListTraits(),
    )
