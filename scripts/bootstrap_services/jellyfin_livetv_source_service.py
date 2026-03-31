"""Jellyfin Live TV source preprocessing helpers."""

from __future__ import annotations

import hashlib
import io
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib import request
from xml.sax.saxutils import escape

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
        logo_by_channel: dict[str, str] = {}
        groups_by_channel: dict[str, set[str]] = {}
        logo_by_name: dict[str, str] = {}

        for tuner in self.coerce_list(tuners):
            if not isinstance(tuner, dict):
                continue
            tuner_type = str(tuner.get("type", "m3u")).strip().lower()
            if tuner_type != "m3u":
                continue
            source_url = str(tuner.get("_effective_url") or tuner.get("url") or "").strip()
            if not source_url:
                continue
            try:
                m3u_text = self._read_text_from_source(source_url, config_root, timeout_seconds=90)
            except Exception:
                continue

            for line in m3u_text.splitlines():
                extinf = str(line or "").strip()
                if not extinf.startswith("#EXTINF"):
                    continue
                tvg_id = self._extract_extinf_attr(extinf, "tvg-id")
                if not tvg_id:
                    continue
                normalized_id = self._normalize_tvg_id(tvg_id)
                tvg_logo = self._extract_extinf_attr(extinf, "tvg-logo")
                group_title = self._extract_extinf_attr(extinf, "group-title")
                tvg_name = self._extract_extinf_attr(extinf, "tvg-name")
                channel_name = self._extract_channel_name_from_extinf(extinf)

                for channel_id in (tvg_id, normalized_id):
                    cid = str(channel_id or "").strip()
                    if not cid:
                        continue
                    if tvg_logo and cid not in logo_by_channel:
                        logo_by_channel[cid] = tvg_logo
                    if group_title:
                        groups_by_channel.setdefault(cid, set()).add(group_title)

                if tvg_logo:
                    for raw_name in (tvg_name, channel_name):
                        norm_name = self._normalize_name(raw_name)
                        if norm_name and norm_name not in logo_by_name:
                            logo_by_name[norm_name] = tvg_logo

        return logo_by_channel, groups_by_channel, logo_by_name

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
    ) -> tuple[str, dict[str, int]]:
        if not xml_text:
            return xml_text, {
                "programmes": 0,
                "icons_added": 0,
                "categories_added": 0,
            }

        out = io.StringIO()
        block_lines: list[str] = []
        in_programme = False
        programmes = 0
        icons_added = 0
        categories_added = 0

        def flush_programme(lines: list[str]) -> None:
            nonlocal programmes, icons_added, categories_added
            block = "".join(lines)
            programmes += 1

            has_icon = bool(re.search(r"<icon\b", block))
            channel_match = re.search(r'channel="([^"]+)"', block)
            channel_id = str((channel_match.group(1) if channel_match else "") or "").strip()
            channel_id_norm = self._normalize_tvg_id(channel_id)

            logo_url = (
                logo_by_channel.get(channel_id)
                or logo_by_channel.get(channel_id_norm)
                or ""
            )
            if not logo_url:
                display_names = (
                    channel_display_names.get(channel_id)
                    or channel_display_names.get(channel_id_norm)
                    or []
                )
                for display_name in display_names:
                    candidate = logo_by_name.get(self._normalize_name(display_name)) or ""
                    if candidate:
                        logo_url = candidate
                        break
            raw_groups = groups_by_channel.get(channel_id) or groups_by_channel.get(channel_id_norm) or set()
            mapped_categories: list[str] = []
            for group in sorted(raw_groups):
                mapped = self._category_from_group_title(group)
                if mapped and mapped not in mapped_categories:
                    mapped_categories.append(mapped)
            if not mapped_categories and default_category:
                mapped_categories.append(default_category)

            insert_fragments: list[str] = []
            indent_match = re.search(r"\n(\s*)</programme>", block)
            indent = (indent_match.group(1) if indent_match else "  ") + "  "
            if add_icons and replace_existing_icons and logo_url and has_icon:
                block = re.sub(r"<icon\b[^>]*\/>\s*", "", block)
                has_icon = bool(re.search(r"<icon\b", block))
            if add_icons and not has_icon and logo_url:
                insert_fragments.append(f'{indent}<icon src="{escape(logo_url)}" />\n')
                icons_added += 1
            if add_categories and mapped_categories:
                existing_category_norm = {
                    str(match.group(1) or "").strip().lower()
                    for match in re.finditer(
                        r"<category\b[^>]*>(.*?)</category>",
                        block,
                        re.DOTALL,
                    )
                    if str(match.group(1) or "").strip()
                }
                for category in mapped_categories:
                    if str(category).strip().lower() in existing_category_norm:
                        continue
                    insert_fragments.append(
                        f"{indent}<category lang=\"en\">{escape(category)}</category>\n"
                    )
                    categories_added += 1

            if insert_fragments:
                block = block.replace("</programme>", "".join(insert_fragments) + "</programme>", 1)

            out.write(block)

        for raw_line in io.StringIO(xml_text):
            line = str(raw_line or "")
            if not in_programme:
                if "<programme" in line:
                    in_programme = True
                    block_lines = [line]
                    if "</programme>" in line:
                        flush_programme(block_lines)
                        block_lines = []
                        in_programme = False
                else:
                    out.write(line)
                continue

            block_lines.append(line)
            if "</programme>" in line:
                flush_programme(block_lines)
                block_lines = []
                in_programme = False

        if block_lines:
            out.write("".join(block_lines))

        return out.getvalue(), {
            "programmes": programmes,
            "icons_added": icons_added,
            "categories_added": categories_added,
        }

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
