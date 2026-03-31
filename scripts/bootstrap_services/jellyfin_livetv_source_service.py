"""Jellyfin Live TV source preprocessing helpers."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib import request

CoerceListFn = Callable[[Any], list[Any]]
CandidateRootsFn = Callable[[str], list[Path]]
ResolvePathFn = Callable[[Path | str, str], Path]
LogFn = Callable[[str], None]


@dataclass
class JellyfinLiveTvSourceService:
    coerce_list: CoerceListFn
    candidate_config_roots: CandidateRootsFn
    resolve_path: ResolvePathFn
    log: LogFn

    def _read_text_from_source(
        self, source: str, config_root: str, timeout_seconds: int = 60
    ) -> str:
        src = str(source or "").strip()
        if not src:
            return ""

        if src.lower().startswith("http://") or src.lower().startswith("https://"):
            with request.urlopen(src, timeout=timeout_seconds) as resp:
                payload = resp.read()
            return payload.decode("utf-8", errors="replace")

        candidate_paths: list[Path] = []
        src_path = Path(src)
        if src_path.is_absolute():
            candidate_paths.append(src_path)
            if src.startswith("/config/"):
                config_relative = src[len("/config/") :].lstrip("/")
                for root in self.candidate_config_roots(config_root):
                    candidate_paths.append(root / "jellyfin" / config_relative)
        else:
            for root in self.candidate_config_roots(config_root):
                candidate_paths.append(self.resolve_path(root, src))

        seen: set[str] = set()
        for path in candidate_paths:
            path_key = str(path)
            if path_key in seen:
                continue
            seen.add(path_key)
            if path.exists():
                return path.read_text(encoding="utf-8", errors="replace")

        raise RuntimeError(f"Unable to read source data from {src}")

    @staticmethod
    def _extract_xmltv_channel_ids(xml_text: str) -> set[str]:
        if not xml_text:
            return set()
        return {match for match in re.findall(r"<channel id=\"([^\"]+)\"", xml_text) if match}

    @staticmethod
    def _rewrite_extinf_tvg_id(extinf_line: str, new_id: str) -> str:
        pattern = r"tvg-id=\"[^\"]*\""
        replacement = f'tvg-id="{new_id}"'
        if re.search(pattern, extinf_line):
            return re.sub(pattern, replacement, extinf_line, count=1)
        return extinf_line

    def _transform_m3u_for_guide(
        self,
        m3u_text: str,
        normalize_tvg_id_suffix: bool = False,
        guide_channel_ids: set[str] | None = None,
    ) -> tuple[str, dict[str, int]]:
        lines = m3u_text.splitlines()
        output: list[str] = []
        pending_extinf: str | None = None
        pending_meta: list[str] = []
        total_entries = 0
        kept_entries = 0
        dropped_entries = 0
        normalized_ids = 0

        for line in lines:
            raw = str(line).rstrip("\r\n")
            stripped = raw.strip()
            if not stripped:
                continue

            if pending_extinf is None and stripped.startswith("#EXTM3U"):
                if not output:
                    output.append(stripped)
                continue

            if stripped.startswith("#EXTINF"):
                pending_extinf = stripped
                pending_meta = []
                continue

            if pending_extinf is not None and stripped.startswith("#"):
                pending_meta.append(stripped)
                continue

            if pending_extinf is None:
                if stripped.startswith("#") and output:
                    output.append(stripped)
                continue

            total_entries += 1
            extinf = pending_extinf
            pending_extinf = None
            tvg_id_match = re.search(r"tvg-id=\"([^\"]*)\"", extinf)
            tvg_id = str((tvg_id_match.group(1) if tvg_id_match else "") or "").strip()
            effective_tvg_id = tvg_id

            if normalize_tvg_id_suffix and tvg_id:
                stripped_id = tvg_id.split("@", 1)[0].strip()
                if stripped_id and stripped_id != tvg_id:
                    effective_tvg_id = stripped_id
                    normalized_ids += 1

            if guide_channel_ids is not None:
                if not effective_tvg_id or effective_tvg_id not in guide_channel_ids:
                    dropped_entries += 1
                    pending_meta = []
                    continue

            if effective_tvg_id and effective_tvg_id != tvg_id:
                extinf = self._rewrite_extinf_tvg_id(extinf, effective_tvg_id)

            if not output:
                output.append("#EXTM3U")
            output.append(extinf)
            output.extend(pending_meta)
            output.append(stripped)
            pending_meta = []
            kept_entries += 1

        if not output:
            output = ["#EXTM3U"]

        rendered = "\n".join(output) + "\n"
        summary = {
            "total_entries": total_entries,
            "kept_entries": kept_entries,
            "dropped_entries": dropped_entries,
            "normalized_ids": normalized_ids,
        }
        return rendered, summary

    @staticmethod
    def _container_path_for_materialized_playlist(output_rel_path: str) -> str:
        rel = str(output_rel_path or "").strip().lstrip("/")
        if not rel:
            return ""
        if rel.startswith("jellyfin/"):
            return "/config/" + rel[len("jellyfin/") :]
        return "/" + rel

    def prepare_m3u_tuner_url(
        self,
        tuner: dict[str, Any] | Any,
        guides: list[dict[str, Any]] | Any,
        config_root: str,
        guide_channel_ids_cache: dict[str, set[str]] | None = None,
    ) -> str:
        if not isinstance(tuner, dict):
            return str(tuner or "").strip()

        tuner_type = str(tuner.get("type", "m3u")).strip().lower()
        source_url = str(tuner.get("url") or "").strip()
        if tuner_type != "m3u" or not source_url:
            return source_url

        normalize_tvg_id_suffix = bool(tuner.get("normalize_tvg_id_suffix", False))
        filter_to_guide_channels = bool(tuner.get("filter_to_guide_channels", False))
        if not normalize_tvg_id_suffix and not filter_to_guide_channels:
            return source_url

        source_hash = hashlib.sha1(source_url.encode("utf-8")).hexdigest()[:12]
        output_rel_path = str(
            tuner.get("materialized_output_path") or f"jellyfin/livetv-tuners/{source_hash}.m3u"
        ).strip()
        if not output_rel_path:
            output_rel_path = f"jellyfin/livetv-tuners/{source_hash}.m3u"

        try:
            m3u_text = self._read_text_from_source(source_url, config_root, timeout_seconds=90)

            guide_channel_ids = None
            selected_guide_path = ""
            if filter_to_guide_channels:
                selected_guide_path = str(tuner.get("filter_guide_path") or "").strip()
                if not selected_guide_path:
                    for guide in self.coerce_list(guides):
                        if not isinstance(guide, dict):
                            continue
                        candidate = str(guide.get("path") or "").strip()
                        if candidate:
                            selected_guide_path = candidate
                            break

                if selected_guide_path:
                    cache = (
                        guide_channel_ids_cache if isinstance(guide_channel_ids_cache, dict) else {}
                    )
                    if selected_guide_path in cache:
                        guide_channel_ids = cache[selected_guide_path]
                    else:
                        xml_text = self._read_text_from_source(
                            selected_guide_path, config_root, timeout_seconds=150
                        )
                        guide_channel_ids = self._extract_xmltv_channel_ids(xml_text)
                        cache[selected_guide_path] = guide_channel_ids
                    if not guide_channel_ids:
                        self.log(
                            "[WARN] Jellyfin Live TV: guide channel list is empty; "
                            f"disabling channel filter for tuner={source_url}"
                        )
                        guide_channel_ids = None
                else:
                    self.log(
                        "[WARN] Jellyfin Live TV: filter_to_guide_channels is enabled but no guide path "
                        f"was resolved for tuner={source_url}; continuing without guide filtering."
                    )

            rendered, summary = self._transform_m3u_for_guide(
                m3u_text,
                normalize_tvg_id_suffix=normalize_tvg_id_suffix,
                guide_channel_ids=guide_channel_ids,
            )
            if filter_to_guide_channels and summary.get("kept_entries", 0) == 0:
                rendered, summary = self._transform_m3u_for_guide(
                    m3u_text,
                    normalize_tvg_id_suffix=normalize_tvg_id_suffix,
                    guide_channel_ids=None,
                )
                self.log(
                    "[WARN] Jellyfin Live TV: guide-filtered playlist was empty; "
                    f"falling back to unfiltered normalized playlist for tuner={source_url}"
                )

            target_paths: list[str] = []
            for root in self.candidate_config_roots(config_root):
                path = self.resolve_path(root, output_rel_path)
                key = str(path)
                if key not in target_paths:
                    target_paths.append(key)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(rendered, encoding="utf-8")

            container_path = self._container_path_for_materialized_playlist(output_rel_path)
            self.log(
                "[INFO] Jellyfin Live TV: prepared tuner playlist "
                f"({source_url} -> {container_path}, total={summary.get('total_entries', 0)}, "
                f"kept={summary.get('kept_entries', 0)}, dropped={summary.get('dropped_entries', 0)}, "
                f"normalized_ids={summary.get('normalized_ids', 0)}, "
                f"guide_filter={'on' if guide_channel_ids is not None else 'off'})"
            )
            return container_path or source_url
        except Exception as exc:
            self.log(
                "[WARN] Jellyfin Live TV: playlist preprocessing failed "
                f"for tuner={source_url} ({exc}); continuing with source URL."
            )
            return source_url
