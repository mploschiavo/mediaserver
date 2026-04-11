"""EPG merge service — combines multiple XMLTV guides into one.

Downloads all EPG XMLs for configured countries, remaps channel IDs
to match M3U tvg-ids, deduplicates channels, and outputs a single
merged XMLTV file that Jellyfin loads as one guide provider.

This gives near-100% guide coverage because:
1. Channel IDs are rewritten to match M3U tvg-ids exactly
2. Multiple EPG sources per country are tried (provider fallback)
3. Duplicate channels across countries are merged (not duplicated)
4. One guide file = one refresh = fast and reliable
"""

from __future__ import annotations

import gzip
import hashlib
import re
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable
from xml.sax.saxutils import escape as xml_escape


LogFn = Callable[[str], None]


def _download_xml(url: str, timeout: int = 120) -> str:
    """Download XMLTV, auto-decompress gzip."""
    req = urllib.request.Request(url, headers={
        "User-Agent": "media-stack-controller/1.0",
        "Accept-Encoding": "gzip, identity",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = resp.read()
    enc = (resp.headers.get("Content-Encoding") or "").lower()
    if url.lower().endswith(".gz") or enc == "gzip":
        try:
            payload = gzip.decompress(payload)
        except Exception:
            pass
    return payload.decode("utf-8", errors="replace")


def _extract_m3u_tvg_ids(m3u_text: str) -> dict[str, str]:
    """Extract tvg-id → display name mapping from M3U text."""
    ids: dict[str, str] = {}
    for match in re.finditer(
        r'tvg-id="([^"]*)"[^,]*,(.+)', m3u_text
    ):
        tvg_id = match.group(1).strip()
        name = match.group(2).strip()
        if tvg_id:
            ids[tvg_id] = name
    return ids


def _extract_xmltv_channels(xml_text: str) -> dict[str, list[str]]:
    """Extract channel ID → list of display names from XMLTV."""
    channels: dict[str, list[str]] = {}
    for match in re.finditer(
        r'<channel\s+id="([^"]+)"[^>]*>(.*?)</channel>',
        xml_text, re.DOTALL,
    ):
        ch_id = match.group(1).strip()
        names = re.findall(r'<display-name[^>]*>([^<]+)</display-name>', match.group(2))
        channels[ch_id] = [n.strip() for n in names if n.strip()]
    return channels


def _normalize_for_match(name: str) -> str:
    """Normalize a channel name for fuzzy matching."""
    s = name.lower()
    # Remove resolution markers, country codes, common suffixes
    s = re.sub(r'\s*\([^)]*\)\s*', ' ', s)
    s = re.sub(r'\s*\[[^\]]*\]\s*', ' ', s)
    s = re.sub(r'\b(hd|sd|fhd|uhd|4k|720p|1080p|480p|576p|540p|2160p)\b', '', s, flags=re.IGNORECASE)
    s = re.sub(r'[^a-z0-9]+', '', s)
    return s.strip()


def _tokenize(s: str) -> set[str]:
    """Split into lowercase alpha-numeric tokens for fuzzy matching."""
    return {t for t in re.split(r'[^a-z0-9]+', s.lower()) if len(t) > 1}


def _token_similarity(a: str, b: str) -> float:
    """Jaccard similarity between token sets. 0.0 = no overlap, 1.0 = identical."""
    ta, tb = _tokenize(a), _tokenize(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _build_id_mapping(
    m3u_ids: dict[str, str],
    epg_channels: dict[str, list[str]],
) -> dict[str, str]:
    """Map EPG channel IDs → M3U tvg-ids.

    Strategy (in priority order):
    1. Exact ID match
    2. Case-insensitive ID match
    3. Normalized ID match (strip .us, .de, resolution markers)
    4. Exact normalized display name match
    5. Substring containment (EPG ID in M3U ID or vice versa)
    6. Token similarity on display names (Jaccard >= 0.6)
    """
    mapping: dict[str, str] = {}

    # Build indices
    m3u_by_norm: dict[str, str] = {}
    m3u_by_id_lower: dict[str, str] = {}
    m3u_by_id_normalized: dict[str, str] = {}
    m3u_names_list: list[tuple[str, str, str]] = []  # (tvg_id, name, norm_name)
    for tvg_id, name in m3u_ids.items():
        norm = _normalize_for_match(name)
        m3u_by_norm[norm] = tvg_id
        m3u_by_id_lower[tvg_id.lower()] = tvg_id
        m3u_by_id_normalized[_normalize_for_match(tvg_id)] = tvg_id
        m3u_names_list.append((tvg_id, name, norm))

    for epg_id, names in epg_channels.items():
        # 1. Exact ID match
        if epg_id in m3u_ids:
            mapping[epg_id] = epg_id
            continue

        # 2. Case-insensitive ID match
        matched = m3u_by_id_lower.get(epg_id.lower())
        if matched:
            mapping[epg_id] = matched
            continue

        # 3. Normalized ID match (strip .us, .de suffixes)
        epg_norm_id = _normalize_for_match(epg_id)
        matched = m3u_by_id_normalized.get(epg_norm_id)
        if matched:
            mapping[epg_id] = matched
            continue

        # 4. Exact normalized display name match
        found = False
        for name in names:
            norm = _normalize_for_match(name)
            matched = m3u_by_norm.get(norm)
            if matched:
                mapping[epg_id] = matched
                found = True
                break
        if found:
            continue

        # 5. Substring containment on normalized IDs
        epg_lower = epg_id.lower().replace(".", "").replace("-", "")
        for m3u_lower, m3u_orig in m3u_by_id_lower.items():
            m3u_clean = m3u_lower.replace(".", "").replace("-", "")
            if len(epg_lower) > 3 and len(m3u_clean) > 3:
                if epg_lower in m3u_clean or m3u_clean in epg_lower:
                    mapping[epg_id] = m3u_orig
                    found = True
                    break
        if found:
            continue

        # 6. Token similarity on display names (fuzzy)
        best_score = 0.0
        best_match = ""
        for name in names:
            for m3u_tvg, m3u_name, _ in m3u_names_list:
                score = _token_similarity(name, m3u_name)
                if score > best_score:
                    best_score = score
                    best_match = m3u_tvg
        if best_score >= 0.6 and best_match:
            mapping[epg_id] = best_match

    return mapping


def _extract_programmes(xml_text: str, channel_id: str) -> list[str]:
    """Extract raw <programme> XML blocks for a channel ID."""
    pattern = re.compile(
        r'(<programme\s[^>]*channel="' + re.escape(channel_id) + r'"[^>]*>.*?</programme>)',
        re.DOTALL,
    )
    return pattern.findall(xml_text)


def merge_epgs(
    m3u_paths: list[str],
    epg_sources: list[dict[str, str]],
    output_path: str,
    config_root: str,
    log: LogFn | None = None,
) -> dict[str, Any]:
    """Merge multiple EPG sources into one XMLTV file.

    Args:
        m3u_paths: Paths to M3U files (local filesystem)
        epg_sources: List of {url, name, country_code} dicts
        output_path: Where to write the merged XMLTV
        config_root: Controller config root
        log: Optional log function

    Returns:
        Summary dict with counts
    """
    def _log(msg: str) -> None:
        if log:
            log(msg)

    t0 = time.time()

    # Step 1: Collect all tvg-ids from M3U files
    all_tvg_ids: dict[str, str] = {}
    for m3u_path in m3u_paths:
        try:
            p = Path(m3u_path)
            if not p.is_file():
                # Try under config_root
                p = Path(config_root) / m3u_path.lstrip("/")
            if not p.is_file():
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
            ids = _extract_m3u_tvg_ids(text)
            all_tvg_ids.update(ids)
        except Exception as exc:
            _log(f"[WARN] EPG merge: failed reading M3U {m3u_path}: {exc}")

    _log(f"[INFO] EPG merge: {len(all_tvg_ids)} unique tvg-ids from {len(m3u_paths)} M3U files")

    if not all_tvg_ids:
        return {"error": "No tvg-ids found in M3U files", "channels": 0, "programmes": 0}

    # Step 2: Download EPG XMLs and build merged output
    merged_channels: dict[str, str] = {}  # tvg-id → <channel> XML block
    merged_programmes: dict[str, list[str]] = {}  # tvg-id → [<programme> blocks]
    sources_used = 0
    sources_failed = 0

    for src in epg_sources:
        url = src.get("url", "")
        name = src.get("name", url[:40])
        if not url:
            continue

        try:
            _log(f"[INFO] EPG merge: downloading {name}...")
            xml_text = _download_xml(url)
            epg_channels = _extract_xmltv_channels(xml_text)
            id_map = _build_id_mapping(all_tvg_ids, epg_channels)
            matched = len(id_map)
            _log(f"[INFO] EPG merge: {name}: {len(epg_channels)} EPG channels, {matched} matched to M3U")

            if matched == 0:
                continue

            sources_used += 1

            # Extract programmes for matched channels, remap IDs
            for epg_id, tvg_id in id_map.items():
                if tvg_id in merged_channels:
                    continue  # Already have this channel from a higher-priority source

                # Build <channel> block with M3U tvg-id
                display_name = all_tvg_ids.get(tvg_id, tvg_id)
                merged_channels[tvg_id] = (
                    f'  <channel id="{xml_escape(tvg_id)}">'
                    f'<display-name>{xml_escape(display_name)}</display-name>'
                    f'</channel>'
                )

                # Extract and remap programmes
                progs = _extract_programmes(xml_text, epg_id)
                if progs:
                    # Rewrite channel= attribute to use tvg-id
                    remapped = []
                    for prog in progs:
                        remapped.append(
                            prog.replace(f'channel="{epg_id}"', f'channel="{xml_escape(tvg_id)}"', 1)
                        )
                    merged_programmes.setdefault(tvg_id, []).extend(remapped)

        except Exception as exc:
            sources_failed += 1
            _log(f"[WARN] EPG merge: failed downloading {name}: {exc}")

    # Step 3: Write merged XMLTV
    total_progs = sum(len(p) for p in merged_programmes.values())
    channels_with_progs = sum(1 for tvg_id in merged_channels if tvg_id in merged_programmes)

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    with open(out, "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write('<tv generator-info-name="media-stack-epg-merge">\n')
        for ch_xml in merged_channels.values():
            f.write(ch_xml + "\n")
        for progs in merged_programmes.values():
            for prog in progs:
                f.write("  " + prog + "\n")
        f.write("</tv>\n")

    elapsed = round(time.time() - t0, 1)
    _log(
        f"[OK] EPG merge: wrote {out.name} — "
        f"{len(merged_channels)} channels, {channels_with_progs} with programmes, "
        f"{total_progs} programmes from {sources_used} sources ({elapsed}s)"
    )

    return {
        "status": "ok",
        "output": str(out),
        "channels": len(merged_channels),
        "channels_with_programmes": channels_with_progs,
        "programmes": total_progs,
        "sources_used": sources_used,
        "sources_failed": sources_failed,
        "tvg_ids_total": len(all_tvg_ids),
        "elapsed": elapsed,
    }
