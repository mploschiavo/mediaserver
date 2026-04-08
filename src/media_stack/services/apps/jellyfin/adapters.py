"""Jellyfin-specific bootstrap helpers."""

from __future__ import annotations

import copy
import re
from typing import Dict, Iterable, List


def normalize_provider_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def reorder_provider_names(
    names: Iterable[str], priority_terms: Iterable[str] | None = None
) -> List[str]:
    """Reorder provider names by fuzzy priority terms while preserving unknown order."""
    deduped: List[str] = []
    seen = set()
    for raw in names or []:
        name = str(raw or "").strip()
        if not name:
            continue
        key = normalize_provider_name(name)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(name)

    priorities = [normalize_provider_name(term) for term in (priority_terms or []) if term]
    if not priorities or not deduped:
        return deduped

    chosen: List[str] = []
    used = set()
    normalized = [(name, normalize_provider_name(name)) for name in deduped]

    for term in priorities:
        if not term:
            continue
        for name, norm in normalized:
            if norm in used:
                continue
            if norm == term or term in norm or norm in term:
                chosen.append(name)
                used.add(norm)
                break

    for name, norm in normalized:
        if norm in used:
            continue
        chosen.append(name)
        used.add(norm)

    return chosen


def apply_artwork_profile(
    image_options: Iterable[dict] | None,
    supported_image_types: Iterable[str] | None,
    profile: Dict[str, Dict[str, int]] | None,
) -> List[dict]:
    """Ensure image option limits/min widths for desired image types."""
    out: List[dict] = []
    for raw in image_options or []:
        if isinstance(raw, dict):
            out.append(copy.deepcopy(raw))

    if not profile:
        return out

    supported = {str(item or "").strip().lower() for item in (supported_image_types or [])}

    for image_type, opts in profile.items():
        kind = str(image_type or "").strip()
        if not kind:
            continue
        if supported and kind.lower() not in supported:
            continue
        if not isinstance(opts, dict):
            continue

        min_limit = int(opts.get("limit", 1))
        min_width = int(opts.get("min_width", 0))

        idx = -1
        for i, existing in enumerate(out):
            if str(existing.get("Type") or "").strip().lower() == kind.lower():
                idx = i
                break

        if idx >= 0:
            current = out[idx]
            current_limit = int(current.get("Limit", 0) or 0)
            current_min_width = int(current.get("MinWidth", 0) or 0)
            if current_limit < min_limit:
                current["Limit"] = min_limit
            if current_min_width < min_width:
                current["MinWidth"] = min_width
        else:
            out.append({"Type": kind, "Limit": min_limit, "MinWidth": min_width})

    return out
