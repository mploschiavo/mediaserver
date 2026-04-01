"""Jellyfin Live TV source preprocessing helpers."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib import request

from bootstrap_services.apps.jellyfin.livetv_source_ops import (
    collect_tuner_channel_metadata,
    enrich_xmltv_programmes,
    transform_m3u_for_guide,
)

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
        return transform_m3u_for_guide(
            m3u_text,
            normalize_tvg_id_suffix=normalize_tvg_id_suffix,
            guide_channel_ids=guide_channel_ids,
            rewrite_extinf_tvg_id=self._rewrite_extinf_tvg_id,
        )

    @staticmethod
    def _container_path_for_materialized_playlist(output_rel_path: str) -> str:
        rel = str(output_rel_path or "").strip().lstrip("/")
        if not rel:
            return ""
        if rel.startswith("jellyfin/"):
            return "/config/" + rel[len("jellyfin/") :]
        return "/" + rel

    @staticmethod
    def _extract_extinf_attr(extinf_line: str, name: str) -> str:
        match = re.search(rf'{re.escape(name)}="([^"]*)"', extinf_line)
        return str((match.group(1) if match else "") or "").strip()

    @staticmethod
    def _normalize_tvg_id(value: str) -> str:
        raw = str(value or "").strip()
        if not raw:
            return ""
        if "@" in raw:
            base = raw.split("@", 1)[0].strip()
            if base:
                return base
        return raw

    @staticmethod
    def _category_from_group_title(group_title: str) -> str:
        text = str(group_title or "").strip().lower()
        if not text:
            return ""
        sports_tokens = (
            "sport",
            "nfl",
            "nba",
            "mlb",
            "nhl",
            "ufc",
            "mma",
            "wwe",
            "soccer",
            "football",
            "basketball",
            "baseball",
            "hockey",
            "tennis",
            "golf",
            "racing",
            "motorsport",
            "boxing",
            "fight",
        )
        kids_tokens = ("kids", "kid", "children", "child", "cartoon", "animation", "family")
        news_tokens = ("news", "weather", "politics")
        movie_tokens = ("movie", "cinema", "film")
        if any(token in text for token in sports_tokens):
            return "Sports"
        if any(token in text for token in kids_tokens):
            return "Kids"
        if any(token in text for token in news_tokens):
            return "News"
        if any(token in text for token in movie_tokens):
            return "Movie"
        return ""

    @staticmethod
    def _normalize_name(value: str) -> str:
        text = str(value or "").strip().lower()
        if not text:
            return ""
        return re.sub(r"[^a-z0-9]+", "", text)

    @staticmethod
    def _extract_channel_name_from_extinf(extinf_line: str) -> str:
        match = re.search(r"#EXTINF[^,]*,(.*)$", str(extinf_line or ""))
        return str((match.group(1) if match else "") or "").strip()

    @staticmethod
    def _extract_xmltv_channel_display_names(xml_text: str) -> dict[str, list[str]]:
        mapping: dict[str, list[str]] = {}
        if not xml_text:
            return mapping
        for match in re.finditer(r'<channel\s+id="([^"]+)">(.*?)</channel>', xml_text, re.DOTALL):
            channel_id = str((match.group(1) if match else "") or "").strip()
            body = str((match.group(2) if match else "") or "")
            if not channel_id:
                continue
            names: list[str] = []
            for raw_name in re.findall(r"<display-name[^>]*>(.*?)</display-name>", body, re.DOTALL):
                cleaned = re.sub(r"<[^>]+>", "", str(raw_name or "")).strip()
                if cleaned and cleaned not in names:
                    names.append(cleaned)
            if names:
                mapping[channel_id] = names
        return mapping

    def _collect_tuner_channel_metadata(
        self, tuners: list[dict[str, Any]] | Any, config_root: str
    ) -> tuple[dict[str, str], dict[str, set[str]], dict[str, str]]:
        return collect_tuner_channel_metadata(
            tuners,
            config_root=config_root,
            coerce_list=self.coerce_list,
            read_text_from_source=self._read_text_from_source,
            extract_extinf_attr=self._extract_extinf_attr,
            normalize_tvg_id=self._normalize_tvg_id,
            extract_channel_name_from_extinf=self._extract_channel_name_from_extinf,
            normalize_name=self._normalize_name,
        )

    def _enrich_xmltv_programmes(
        self,
        xml_text: str,
        logo_by_channel: dict[str, str],
        groups_by_channel: dict[str, set[str]],
        channel_display_names: dict[str, list[str]],
        logo_by_name: dict[str, str],
        add_icons: bool,
        replace_existing_icons: bool,
        add_categories: bool,
        default_category: str,
        default_icon_url: str,
    ) -> tuple[str, dict[str, int]]:
        return enrich_xmltv_programmes(
            xml_text,
            logo_by_channel=logo_by_channel,
            groups_by_channel=groups_by_channel,
            channel_display_names=channel_display_names,
            logo_by_name=logo_by_name,
            add_icons=add_icons,
            replace_existing_icons=replace_existing_icons,
            add_categories=add_categories,
            default_category=default_category,
            default_icon_url=default_icon_url,
            normalize_tvg_id=self._normalize_tvg_id,
            category_from_group_title=self._category_from_group_title,
            normalize_name=self._normalize_name,
        )

    def prepare_xmltv_guide_path(
        self,
        guide: dict[str, Any] | Any,
        tuners: list[dict[str, Any]] | Any,
        config_root: str,
    ) -> str:
        if not isinstance(guide, dict):
            return str(guide or "").strip()

        guide_type = str(guide.get("type", "xmltv")).strip().lower()
        source_path = str(guide.get("path") or "").strip()
        if guide_type != "xmltv" or not source_path:
            return source_path

        enrich_icons = bool(guide.get("enrich_program_icons_from_tuner_logo", True))
        replace_existing_icons = bool(
            guide.get("replace_existing_program_icons_with_tuner_logo", False)
        )
        enrich_categories = bool(guide.get("enrich_program_categories_from_tuner_groups", True))
        if not enrich_icons and not enrich_categories:
            return source_path
        default_category = str(guide.get("default_program_category", "Shows") or "").strip()
        default_icon_url = str(guide.get("default_program_icon_url") or "").strip()

        source_hash = hashlib.sha1(source_path.encode("utf-8")).hexdigest()[:12]
        output_rel_path = str(
            guide.get("materialized_output_path")
            or f"jellyfin/livetv-guides/{source_hash}.xml"
        ).strip()
        if not output_rel_path:
            output_rel_path = f"jellyfin/livetv-guides/{source_hash}.xml"

        try:
            xml_text = self._read_text_from_source(source_path, config_root, timeout_seconds=180)
            logo_by_channel, groups_by_channel, logo_by_name = self._collect_tuner_channel_metadata(
                tuners=tuners,
                config_root=config_root,
            )
            channel_display_names = self._extract_xmltv_channel_display_names(xml_text)
            rendered, summary = self._enrich_xmltv_programmes(
                xml_text=xml_text,
                logo_by_channel=logo_by_channel,
                groups_by_channel=groups_by_channel,
                channel_display_names=channel_display_names,
                logo_by_name=logo_by_name,
                add_icons=enrich_icons,
                replace_existing_icons=replace_existing_icons,
                add_categories=enrich_categories,
                default_category=default_category,
                default_icon_url=default_icon_url,
            )

            for root in self.candidate_config_roots(config_root):
                path = self.resolve_path(root, output_rel_path)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(rendered, encoding="utf-8")

            container_path = self._container_path_for_materialized_playlist(output_rel_path)
            self.log(
                "[INFO] Jellyfin Live TV: prepared XMLTV guide "
                f"({source_path} -> {container_path}, programmes={summary.get('programmes', 0)}, "
                f"icons_added={summary.get('icons_added', 0)}, "
                f"categories_added={summary.get('categories_added', 0)})"
            )
            return container_path or source_path
        except Exception as exc:
            self.log(
                "[WARN] Jellyfin Live TV: guide preprocessing failed "
                f"for guide={source_path} ({exc}); continuing with source path."
            )
            return source_path

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
