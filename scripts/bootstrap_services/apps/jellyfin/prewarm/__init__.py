"""Jellyfin prewarm operation helpers."""

from .metadata_ops import item_has_artwork, item_has_overview, run_artwork_health_check, run_metadata_backfill
from .sidecar_ops import (
    candidate_image_paths,
    ensure_book_sidecar_artwork,
    ensure_music_sidecar_artwork,
    extract_epub_cover_bytes,
    normalize_text_list,
    resolve_books_root_path,
    resolve_music_root_path,
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

