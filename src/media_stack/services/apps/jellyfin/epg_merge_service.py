"""EPG merge service — combines multiple XMLTV guides into one.

Downloads all EPG XMLs for configured countries, remaps channel IDs
to match M3U tvg-ids, deduplicates channels, and outputs a single
merged XMLTV file that Jellyfin loads as one guide provider.

Performance:
- Parallel downloads (ThreadPoolExecutor, 6 workers)
- Disk cache with 6-hour TTL (avoids re-downloading on restart)
- Stream-parse XML (line-by-line regex, not full DOM load)
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable
from xml.sax.saxutils import escape as xml_escape


LogFn = Callable[[str], None]

_CACHE_TTL_SECONDS = int(os.environ.get("EPG_CACHE_TTL_SECONDS", "21600"))  # 6 hours
_DOWNLOAD_WORKERS = int(os.environ.get("EPG_DOWNLOAD_WORKERS", "6"))
_DOWNLOAD_TIMEOUT = int(os.environ.get("EPG_DOWNLOAD_TIMEOUT", "120"))


# ---------------------------------------------------------------------------
# Download + cache
# ---------------------------------------------------------------------------


class EpgMergeService:

    @staticmethod
    def _cache_dir(config_root: str) -> Path:
        p = Path(config_root) / ".controller" / "epg-cache"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @staticmethod
    def _cache_key(url: str) -> str:
        return hashlib.md5(url.encode()).hexdigest()

    @staticmethod
    def _download_xml(url: str, timeout: int = _DOWNLOAD_TIMEOUT) -> str:
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

    @staticmethod
    def _get_cached_or_download(url: str, config_root: str, log: LogFn | None = None) -> str:
        """Return cached XML if fresh, otherwise download and cache."""
        cache = _cache_dir(config_root)
        key = _cache_key(url)
        cache_file = cache / f"{key}.xml"
        meta_file = cache / f"{key}.meta"

        # Check cache freshness
        if cache_file.is_file() and meta_file.is_file():
            try:
                meta = json.loads(meta_file.read_text())
                if time.time() - meta.get("ts", 0) < _CACHE_TTL_SECONDS:
                    return cache_file.read_text(encoding="utf-8", errors="replace")
            except Exception:
                pass

        # Download
        xml_text = _download_xml(url)

        # Write to cache
        try:
            cache_file.write_text(xml_text, encoding="utf-8")
            meta_file.write_text(json.dumps({"ts": time.time(), "url": url, "size": len(xml_text)}))
        except Exception:
            pass

        return xml_text

    @staticmethod
    def _download_all_parallel(
        sources: list[dict[str, str]],
        config_root: str,
        log: LogFn | None = None,
    ) -> list[tuple[dict[str, str], str | None, str | None]]:
        """Download all EPG sources in parallel. Returns [(source, xml_text, error)]."""
        results: list[tuple[dict[str, str], str | None, str | None]] = []

        def _fetch(src: dict[str, str]) -> tuple[dict[str, str], str | None, str | None]:
            url = src.get("url", "")
            name = src.get("name", url[:40])
            if not url:
                return src, None, "no URL"
            try:
                if log:
                    log(f"[INFO] EPG merge: downloading {name}...")
                xml_text = _get_cached_or_download(url, config_root, log)
                return src, xml_text, None
            except Exception as exc:
                return src, None, str(exc)[:80]

        with ThreadPoolExecutor(max_workers=_DOWNLOAD_WORKERS) as pool:
            futures = {pool.submit(_fetch, src): src for src in sources}
            for future in as_completed(futures):
                results.append(future.result())

        return results

    @staticmethod
    def _extract_m3u_tvg_ids(m3u_text: str) -> dict[str, str]:
        """Extract tvg-id → display name mapping from M3U text."""
        ids: dict[str, str] = {}
        for match in re.finditer(r'tvg-id="([^"]*)"[^,]*,(.+)', m3u_text):
            tvg_id = match.group(1).strip()
            name = match.group(2).strip()
            if tvg_id:
                ids[tvg_id] = name
        return ids

    @staticmethod
    def _stream_extract_channels(xml_text: str) -> dict[str, list[str]]:
        """Extract channel ID → display names via regex (no DOM parse)."""
        channels: dict[str, list[str]] = {}
        for match in re.finditer(
            r'<channel\s+id="([^"]+)"[^>]*>(.*?)</channel>',
            xml_text, re.DOTALL,
        ):
            ch_id = match.group(1).strip()
            names = re.findall(r'<display-name[^>]*>([^<]+)</display-name>', match.group(2))
            channels[ch_id] = [n.strip() for n in names if n.strip()]
        return channels

    @staticmethod
    def _stream_extract_programmes_for_ids(
        xml_text: str, target_ids: set[str]
    ) -> dict[str, list[str]]:
        """Extract <programme> blocks for specific channel IDs.

        Uses SAX-style line scanning for files > 10MB, regex for smaller.
        Single pass — O(n) where n = file size.
        """
        # For small files, regex is fine
        if len(xml_text) < 10_000_000:
            progs: dict[str, list[str]] = {}
            for match in _PROG_PATTERN.finditer(xml_text):
                ch_id = match.group(1)
                if ch_id in target_ids:
                    progs.setdefault(ch_id, []).append(match.group(0))
            return progs

        # For large files (>10MB), use SAX-style line accumulation
        # This avoids holding the full regex match state in memory
        return _sax_extract_programmes(xml_text, target_ids)

    @staticmethod
    def _sax_extract_programmes(xml_text: str, target_ids: set[str]) -> dict[str, list[str]]:
        """SAX-style programme extraction for large XML files.

        Scans line-by-line, accumulates <programme>...</programme> blocks.
        Memory: O(matched programmes) instead of O(all programmes).
        """
        progs: dict[str, list[str]] = {}
        in_programme = False
        current_channel = ""
        current_lines: list[str] = []
        _ch_re = re.compile(r'channel="([^"]+)"')

        for line in xml_text.split("\n"):
            stripped = line.strip()
            if not in_programme:
                if stripped.startswith("<programme "):
                    m = _ch_re.search(stripped)
                    if m and m.group(1) in target_ids:
                        in_programme = True
                        current_channel = m.group(1)
                        current_lines = [line]
                        # Single-line programme?
                        if "</programme>" in stripped:
                            progs.setdefault(current_channel, []).append("\n".join(current_lines))
                            in_programme = False
            else:
                current_lines.append(line)
                if "</programme>" in stripped:
                    progs.setdefault(current_channel, []).append("\n".join(current_lines))
                    in_programme = False

        return progs

    @staticmethod
    def _normalize_for_match(name: str) -> str:
        s = name.lower()
        s = re.sub(r'\s*\([^)]*\)\s*', ' ', s)
        s = re.sub(r'\s*\[[^\]]*\]\s*', ' ', s)
        s = re.sub(r'\b(hd|sd|fhd|uhd|4k|720p|1080p|480p|576p|540p|2160p)\b', '', s, flags=re.IGNORECASE)
        s = re.sub(r'[^a-z0-9]+', '', s)
        return s.strip()

    @staticmethod
    def _tokenize(s: str) -> set[str]:
        return {t for t in re.split(r'[^a-z0-9]+', s.lower()) if len(t) > 1}

    @staticmethod
    def _token_similarity(a: str, b: str) -> float:
        ta, tb = _tokenize(a), _tokenize(b)
        if not ta or not tb:
            return 0.0
        return len(ta & tb) / len(ta | tb)

    @staticmethod
    def _build_id_mapping(
        m3u_ids: dict[str, str],
        epg_channels: dict[str, list[str]],
    ) -> dict[str, str]:
        """Map EPG channel IDs → M3U tvg-ids (6-level fuzzy matching)."""
        mapping: dict[str, str] = {}

        m3u_by_norm: dict[str, str] = {}
        m3u_by_id_lower: dict[str, str] = {}
        m3u_by_id_normalized: dict[str, str] = {}
        m3u_names_list: list[tuple[str, str, str]] = []
        for tvg_id, name in m3u_ids.items():
            norm = _normalize_for_match(name)
            m3u_by_norm[norm] = tvg_id
            m3u_by_id_lower[tvg_id.lower()] = tvg_id
            m3u_by_id_normalized[_normalize_for_match(tvg_id)] = tvg_id
            m3u_names_list.append((tvg_id, name, norm))

        for epg_id, names in epg_channels.items():
            # 1-4: Exact, case-insensitive, normalized, display name match
            if epg_id in m3u_ids:
                mapping[epg_id] = epg_id; continue
            matched = m3u_by_id_lower.get(epg_id.lower())
            if matched:
                mapping[epg_id] = matched; continue
            matched = m3u_by_id_normalized.get(_normalize_for_match(epg_id))
            if matched:
                mapping[epg_id] = matched; continue
            found = False
            for name in names:
                matched = m3u_by_norm.get(_normalize_for_match(name))
                if matched:
                    mapping[epg_id] = matched; found = True; break
            if found:
                continue

            # 5. Substring containment
            epg_lower = epg_id.lower().replace(".", "").replace("-", "")
            for m3u_lower, m3u_orig in m3u_by_id_lower.items():
                m3u_clean = m3u_lower.replace(".", "").replace("-", "")
                if len(epg_lower) > 3 and len(m3u_clean) > 3:
                    if epg_lower in m3u_clean or m3u_clean in epg_lower:
                        mapping[epg_id] = m3u_orig; found = True; break
            if found:
                continue

            # 6. Token similarity >= 0.6
            best_score, best_match = 0.0, ""
            for name in names:
                for m3u_tvg, m3u_name, _ in m3u_names_list:
                    score = _token_similarity(name, m3u_name)
                    if score > best_score:
                        best_score, best_match = score, m3u_tvg
            if best_score >= 0.6 and best_match:
                mapping[epg_id] = best_match

        return mapping

    def merge_epgs(self, 
        m3u_paths: list[str],
        epg_sources: list[dict[str, str]],
        output_path: str,
        config_root: str,
        log: LogFn | None = None,
    ) -> dict[str, Any]:
        """Merge multiple EPG sources into one XMLTV file.

        - Downloads in parallel (6 workers)
        - Caches to disk (6h TTL)
        - Stream-parses XML (single-pass regex, not DOM)
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

        # Step 2: Download all EPGs in parallel with caching
        downloads = _download_all_parallel(epg_sources, config_root, log)

        # Step 3: Process each downloaded EPG — stream-parse and merge
        merged_channels: dict[str, str] = {}
        merged_programmes: dict[str, list[str]] = {}
        sources_used = 0
        sources_failed = 0

        all_tvg_id_set = set(all_tvg_ids.keys())

        for src, xml_text, error in downloads:
            name = src.get("name", src.get("url", "")[:40])
            if error:
                sources_failed += 1
                _log(f"[WARN] EPG merge: failed {name}: {error}")
                continue
            if not xml_text:
                continue

            # Early termination: all M3U tvg-ids already have EPG data
            if merged_channels.keys() >= all_tvg_id_set:
                _log(f"[INFO] EPG merge: all {len(all_tvg_id_set)} tvg-ids matched, skipping {name}")
                continue

            try:
                # Stream-extract channels
                epg_channels = _stream_extract_channels(xml_text)
                id_map = _build_id_mapping(all_tvg_ids, epg_channels)
                matched = len(id_map)
                _log(f"[INFO] EPG merge: {name}: {len(epg_channels)} EPG channels, {matched} matched to M3U")

                if matched == 0:
                    continue

                # Filter out EPG IDs whose tvg_id is already merged —
                # avoids the expensive programme extraction for those channels
                new_target_ids = {
                    epg_id for epg_id, tvg_id in id_map.items()
                    if tvg_id not in merged_channels
                }

                if not new_target_ids:
                    _log(f"[INFO] EPG merge: {name}: all {matched} matched channels already merged, skipping programme extraction")
                    continue

                sources_used += 1

                # Only extract programmes for NEW matched EPG IDs (single-pass)
                all_progs = _stream_extract_programmes_for_ids(xml_text, new_target_ids)

                for epg_id, tvg_id in id_map.items():
                    if tvg_id in merged_channels:
                        continue

                    display_name = all_tvg_ids.get(tvg_id, tvg_id)
                    merged_channels[tvg_id] = (
                        f'  <channel id="{xml_escape(tvg_id)}">'
                        f'<display-name>{xml_escape(display_name)}</display-name>'
                        f'</channel>'
                    )

                    progs = all_progs.get(epg_id, [])
                    if progs:
                        remapped = [
                            p.replace(f'channel="{epg_id}"', f'channel="{xml_escape(tvg_id)}"', 1)
                            for p in progs
                        ]
                        merged_programmes.setdefault(tvg_id, []).extend(remapped)

            except Exception as exc:
                sources_failed += 1
                _log(f"[WARN] EPG merge: failed processing {name}: {exc}")

        # Step 4: Write merged XMLTV
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


_instance = EpgMergeService()
merge_epgs = _instance.merge_epgs
_extract_m3u_tvg_ids = _instance._extract_m3u_tvg_ids
_stream_extract_channels = _instance._stream_extract_channels
_stream_extract_programmes_for_ids = _instance._stream_extract_programmes_for_ids
_cache_dir = _instance._cache_dir
_cache_key = _instance._cache_key
_download_xml = _instance._download_xml
_get_cached_or_download = _instance._get_cached_or_download
_download_all_parallel = _instance._download_all_parallel
_sax_extract_programmes = _instance._sax_extract_programmes
_normalize_for_match = _instance._normalize_for_match
_tokenize = _instance._tokenize
_token_similarity = _instance._token_similarity
_build_id_mapping = _instance._build_id_mapping
