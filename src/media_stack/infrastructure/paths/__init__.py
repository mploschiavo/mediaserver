"""Loader for ``contracts/defaults/paths.yaml`` — the SoT for the
controller pod's view of the shared media + downloads filesystem.

Used by code paths that need a fallback default when their own config
section is empty (operator hasn't supplied an override). The shape
matches the rest of ``contracts/defaults/*.yaml``: every value is
operator-tunable through the merged config dict, this just exposes
the YAML's bundled defaults so callsites don't inline literals.

The merged runtime config (``ControllerConfigLoader._load_yaml_defaults``)
already incorporates this YAML into its top-level ``controller_paths``
key, so callers that already have a config dict in scope should read
from there. This helper exists for deep-call-chain callers (and a few
domain-layer dataclasses) that don't.

Cached at module level — the YAML is shipped in the image, immutable
across the controller's lifetime.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml


logger = logging.getLogger(__name__)


def _candidate_paths_yaml_locations() -> list[Path]:
    """Mirrors the existing per-loader candidate-path pattern (see
    ``infrastructure.promises.registry.default_registry_path`` and
    ``infrastructure.runtime_factory.config_loader._load_yaml_defaults``).

    Lives in ``contracts/catalog/`` rather than ``contracts/defaults/``
    so it isn't auto-merged into the top-level bootstrap config dict
    (the strict schema rejects unknown top-level keys, and these
    paths are read by their dedicated loader, not by every consumer
    of the merged dict).

    Order matters: dev-tree first so editable installs win over the
    image-baked copy when both exist.
    """
    here = Path(__file__).resolve()
    return [
        # Dev tree: src/media_stack/infrastructure/paths/__init__.py
        # -> repo root is parents[4]
        here.parents[4] / "contracts" / "catalog" / "paths.yaml",
        # Container: contracts is COPY'd to /opt/media-stack/contracts
        Path("/opt/media-stack/contracts/catalog/paths.yaml"),
        # Container alt: some images COPY contracts/ at /contracts
        Path("/contracts/catalog/paths.yaml"),
    ]


_cache: dict[str, Any] | None = None


def load_controller_paths() -> dict[str, Any]:
    """Return the ``controller_paths`` block from
    ``contracts/defaults/paths.yaml``.

    Returns an empty dict if the file is missing or malformed —
    callers should treat the YAML as authoritative and use the
    returned dict's ``.get(KEY, "")`` to stay resilient to a stripped-
    down image without paths.yaml.
    """
    global _cache
    if _cache is not None:
        return _cache

    for candidate in _candidate_paths_yaml_locations():
        if not candidate.is_file():
            continue
        try:
            data = yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            logger.warning("paths.yaml malformed at %s: %s", candidate, exc)
            continue
        if not isinstance(data, dict):
            logger.warning("paths.yaml at %s is not a mapping", candidate)
            continue
        block = data.get("controller_paths")
        if isinstance(block, dict):
            _cache = block
            return _cache
        logger.warning(
            "paths.yaml at %s missing top-level 'controller_paths' key",
            candidate,
        )

    logger.debug("paths.yaml not found at any candidate location")
    _cache = {}
    return _cache


def media_path(library: str) -> str:
    """Convenience: ``/srv-stack/media/<library>`` from the controller's
    view. ``library`` is one of ``tv|movies|music|books``."""
    paths = load_controller_paths().get("media") or {}
    return str(paths.get(library, "")).strip()


def media_compose_fallback_path(library: str) -> str:
    """Convenience: the compose-runtime fallback path for a media
    library (``/media/<library>``). Used by code paths that walk the
    library on the controller's filesystem when the k8s-shape primary
    doesn't exist."""
    paths = load_controller_paths().get("media_compose_fallback") or {}
    return str(paths.get(library, "")).strip()


def torrents_completed_path(category: str) -> str:
    """Convenience: ``/srv-stack/data/torrents/completed/<category>``."""
    paths = load_controller_paths().get("torrents_completed") or {}
    return str(paths.get(category, "")).strip()


def usenet_completed_path(category: str) -> str:
    """Convenience: ``/srv-stack/data/usenet/completed/<category>``."""
    paths = load_controller_paths().get("usenet_completed") or {}
    return str(paths.get(category, "")).strip()


def reset_cache_for_tests() -> None:
    """Reset the module-level cache. Tests that mutate paths.yaml or
    monkey-patch the candidate locations call this between cases."""
    global _cache
    _cache = None


__all__ = [
    "load_controller_paths",
    "media_path",
    "media_compose_fallback_path",
    "torrents_completed_path",
    "usenet_completed_path",
    "reset_cache_for_tests",
]
