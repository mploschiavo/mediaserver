"""Loader for ``contracts/defaults/media_types.yaml`` — the per-type
catalog of media the stack manages.

Caches the deserialized ``dict[str, MediaType]`` at module level —
the YAML is shipped in the image and immutable across the controller's
lifetime.

Mirrors the path-resolution shape used by other infrastructure
loaders (``infrastructure.paths``, ``infrastructure.promises.registry``):
dev-tree first, then ``/opt/media-stack/contracts/...``, then
``/contracts/...`` for stripped-down test images.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

import yaml

from media_stack.domain.media.catalog import MediaType


logger = logging.getLogger(__name__)


class MediaCatalog:
    """Loader/cache for the media-types catalog.

    Module-level singleton ``_INSTANCE`` exposes every method as a
    module alias so callers (and tests that ``mock.patch`` the
    module-level names) keep working unchanged. Per ADR-0012 design
    principle 3, intra-class calls dispatch through
    ``sys.modules[__name__]`` so test patches intercept correctly.
    """

    def __init__(self) -> None:
        self._cache: dict[str, MediaType] | None = None

    def _candidate_yaml_locations(self) -> list[Path]:
        here = Path(__file__).resolve()
        return [
            # Dev tree: src/media_stack/infrastructure/media/catalog.py
            # → repo root is parents[4]
            here.parents[4] / "contracts" / "catalog" / "media_types.yaml",
            Path("/opt/media-stack/contracts/catalog/media_types.yaml"),
            Path("/contracts/catalog/media_types.yaml"),
        ]

    def _parse_media_types_yaml(self, path: Path) -> dict[str, MediaType] | None:
        """Parse one candidate file. Returns ``None`` when the file is
        unreadable / malformed / missing the expected top-level key —
        caller falls through to the next candidate. Returns the deserialized
        catalog dict (possibly empty) on a parse-clean file."""
        try:
            data: Any = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            logger.warning("media_types.yaml malformed at %s: %s", path, exc)
            return None
        if not isinstance(data, dict):
            logger.warning("media_types.yaml at %s is not a mapping", path)
            return None
        block = data.get("media_types")
        if not isinstance(block, dict):
            logger.warning(
                "media_types.yaml at %s missing top-level 'media_types' key", path,
            )
            return None
        # Dispatch through module alias so tests can patch
        # ``_deserialize_catalog_block`` at module scope.
        return sys.modules[__name__]._deserialize_catalog_block(block)

    def _deserialize_catalog_block(self, block: dict[str, Any]) -> dict[str, MediaType]:
        """Turn the YAML's ``media_types:`` mapping into ``{name: MediaType}``.
        Skip individual malformed entries with a WARN; the catalog returned
        contains every well-formed type."""
        catalog: dict[str, MediaType] = {}
        for name, entry in block.items():
            if not isinstance(entry, dict):
                logger.warning(
                    "media_types.yaml entry %r is not a mapping; skipping", name,
                )
                continue
            try:
                catalog[str(name)] = MediaType.from_dict(entry)
            except ValueError as exc:
                logger.warning("media_types.yaml entry %r invalid: %s", name, exc)
        return catalog

    def load_media_types(self) -> dict[str, MediaType]:
        """Return ``{name: MediaType}`` loaded from media_types.yaml.

        Cached. Returns an empty dict (and logs at WARN) when the file
        isn't found — callers should treat the catalog as authoritative
        and an empty result as "running in a stripped image without the
        contracts copy", and fail fast loudly if their feature requires
        media-type data.
        """
        if self._cache is not None:
            return self._cache
        mod = sys.modules[__name__]
        for candidate in mod._candidate_yaml_locations():
            if not candidate.is_file():
                continue
            catalog = mod._parse_media_types_yaml(candidate)
            if catalog is not None:
                self._cache = catalog
                return self._cache
        logger.warning("media_types.yaml not found at any candidate location")
        self._cache = {}
        return self._cache

    def media_type(self, name: str) -> MediaType | None:
        """Return the catalog entry for ``name`` (e.g. ``"tv"``) or ``None``
        if the catalog doesn't have that entry. Convenience for one-shot
        lookups; iterators should call ``load_media_types().values()``."""
        return sys.modules[__name__].load_media_types().get(name)

    def reset_cache_for_tests(self) -> None:
        """Clear the module-level cache. Tests that mutate
        ``media_types.yaml`` or the candidate locations call this between
        cases."""
        self._cache = None


_INSTANCE = MediaCatalog()

# Module-level aliases — preserve the existing public + underscore
# import API. Tests that ``mock.patch`` any of these names continue to
# work because in-class callers dispatch through ``sys.modules[__name__]``.
_candidate_yaml_locations = _INSTANCE._candidate_yaml_locations
_parse_media_types_yaml = _INSTANCE._parse_media_types_yaml
_deserialize_catalog_block = _INSTANCE._deserialize_catalog_block
load_media_types = _INSTANCE.load_media_types
media_type = _INSTANCE.media_type
reset_cache_for_tests = _INSTANCE.reset_cache_for_tests


__all__ = ["load_media_types", "media_type", "reset_cache_for_tests"]
