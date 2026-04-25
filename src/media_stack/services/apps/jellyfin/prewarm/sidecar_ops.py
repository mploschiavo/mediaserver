"""Shim — moved to
``media_stack.domain.jellyfin.prewarm.sidecar_ops`` in ADR-0002
Phase 16-D batch 1. Phase 16-F removes this shim.
"""

from media_stack.domain.jellyfin.prewarm.sidecar_ops import *  # noqa: F401,F403
from media_stack.domain.jellyfin.prewarm.sidecar_ops import (  # noqa: F401
    JellyfinSidecarOps,
    candidate_image_paths,
    ensure_book_sidecar_artwork,
    ensure_music_sidecar_artwork,
    extract_epub_cover_bytes,
    normalize_text_list,
    resolve_books_root_path,
    resolve_music_root_path,
)
