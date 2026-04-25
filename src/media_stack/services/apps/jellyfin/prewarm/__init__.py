"""Shim — Jellyfin prewarm helpers moved to
``media_stack.domain.jellyfin.prewarm`` in ADR-0002 Phase 16-D batch 1.
Phase 16-F removes this shim.
"""

from media_stack.domain.jellyfin.prewarm import *  # noqa: F401,F403
from media_stack.domain.jellyfin.prewarm import (  # noqa: F401
    candidate_image_paths,
    ensure_book_sidecar_artwork,
    ensure_music_sidecar_artwork,
    extract_epub_cover_bytes,
    item_has_artwork,
    item_has_overview,
    normalize_text_list,
    resolve_books_root_path,
    resolve_music_root_path,
    run_artwork_health_check,
    run_metadata_backfill,
)

__all__ = [
    "normalize_text_list",
    "candidate_image_paths",
    "extract_epub_cover_bytes",
    "resolve_books_root_path",
    "resolve_music_root_path",
    "ensure_book_sidecar_artwork",
    "ensure_music_sidecar_artwork",
    "item_has_artwork",
    "item_has_overview",
    "run_metadata_backfill",
    "run_artwork_health_check",
]
