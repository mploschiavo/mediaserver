"""Shared operations for Jellyfin Live TV source preprocessing."""

from __future__ import annotations

import io
import re
from typing import Any, Callable
from xml.sax.saxutils import escape


class JellyfinLiveTvSourceOps:

    def transform_m3u_for_guide(self, 
        m3u_text: str,
        *,
        normalize_tvg_id_suffix: bool,
        guide_channel_ids: set[str] | None,
        rewrite_extinf_tvg_id: Callable[[str, str], str],
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
            tvg_id_match = re.search(r'tvg-id="([^"]*)"', extinf)
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
                extinf = rewrite_extinf_tvg_id(extinf, effective_tvg_id)

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

    def collect_tuner_channel_metadata(self, 
        tuners: list[dict[str, Any]] | Any,
        *,
        config_root: str,
        coerce_list: Callable[[Any], list[Any]],
        read_text_from_source: Callable[[str, str, int], str],
        extract_extinf_attr: Callable[[str, str], str],
        normalize_tvg_id: Callable[[str], str],
        extract_channel_name_from_extinf: Callable[[str], str],
        normalize_name: Callable[[str], str],
    ) -> tuple[dict[str, str], dict[str, set[str]], dict[str, str]]:
        logo_by_channel: dict[str, str] = {}
        groups_by_channel: dict[str, set[str]] = {}
        logo_by_name: dict[str, str] = {}

        for tuner in coerce_list(tuners):
            if not isinstance(tuner, dict):
                continue
            tuner_type = str(tuner.get("type", "m3u")).strip().lower()
            if tuner_type != "m3u":
                continue
            source_url = str(tuner.get("_effective_url") or tuner.get("url") or "").strip()
            if not source_url:
                continue
            try:
                m3u_text = read_text_from_source(source_url, config_root, 90)
            except Exception as exc:
                import logging; logging.getLogger("media_stack").debug("[DEBUG] Swallowed: %s", exc)
                continue

            for line in m3u_text.splitlines():
                extinf = str(line or "").strip()
                if not extinf.startswith("#EXTINF"):
                    continue
                tvg_id = extract_extinf_attr(extinf, "tvg-id")
                if not tvg_id:
                    continue
                normalized_id = normalize_tvg_id(tvg_id)
                tvg_logo = extract_extinf_attr(extinf, "tvg-logo")
                group_title = extract_extinf_attr(extinf, "group-title")
                tvg_name = extract_extinf_attr(extinf, "tvg-name")
                channel_name = extract_channel_name_from_extinf(extinf)

                for channel_id in (tvg_id, normalized_id):
                    cid = str(channel_id or "").strip()
                    if not cid:
                        continue
                    if tvg_logo and cid not in logo_by_channel:
                        logo_by_channel[cid] = tvg_logo
                    if group_title:
                        groups_by_channel.setdefault(cid, set()).add(group_title)

                if tvg_logo:
                    for raw_name in (tvg_name, channel_name, tvg_id, normalized_id):
                        norm_name = normalize_name(raw_name)
                        if norm_name and norm_name not in logo_by_name:
                            logo_by_name[norm_name] = tvg_logo

        return logo_by_channel, groups_by_channel, logo_by_name

    def enrich_xmltv_programmes(self, 
        xml_text: str,
        *,
        logo_by_channel: dict[str, str],
        groups_by_channel: dict[str, set[str]],
        channel_display_names: dict[str, list[str]],
        logo_by_name: dict[str, str],
        add_icons: bool,
        replace_existing_icons: bool,
        add_categories: bool,
        default_category: str,
        default_icon_url: str,
        normalize_tvg_id: Callable[[str], str],
        category_from_group_title: Callable[[str], str],
        normalize_name: Callable[[str], str],
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
            channel_id_norm = normalize_tvg_id(channel_id)

            logo_url = logo_by_channel.get(channel_id) or logo_by_channel.get(channel_id_norm) or ""
            if not logo_url:
                display_names = (
                    channel_display_names.get(channel_id)
                    or channel_display_names.get(channel_id_norm)
                    or []
                )
                for display_name in display_names:
                    candidate = logo_by_name.get(normalize_name(display_name)) or ""
                    if candidate:
                        logo_url = candidate
                        break
            if not logo_url:
                logo_url = (
                    logo_by_name.get(normalize_name(channel_id))
                    or logo_by_name.get(normalize_name(channel_id_norm))
                    or ""
                )
            if not logo_url and default_icon_url:
                logo_url = default_icon_url

            raw_groups = (
                groups_by_channel.get(channel_id) or groups_by_channel.get(channel_id_norm) or set()
            )
            mapped_categories: list[str] = []
            for group in sorted(raw_groups):
                mapped = category_from_group_title(group)
                if mapped and mapped not in mapped_categories:
                    mapped_categories.append(mapped)
            if not mapped_categories and default_category:
                mapped_categories.append(default_category)

            insert_fragments: list[str] = []
            indent_match = re.search(r"\n(\s*)</programme>", block)
            indent = (indent_match.group(1) if indent_match else "  ") + "  "
            if add_icons and replace_existing_icons and logo_url and has_icon:
                block = re.sub(r"<icon\b[^>]*/>\s*", "", block)
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
                        f'{indent}<category lang="en">{escape(category)}</category>\n'
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


_instance = JellyfinLiveTvSourceOps()
transform_m3u_for_guide = _instance.transform_m3u_for_guide
collect_tuner_channel_metadata = _instance.collect_tuner_channel_metadata
enrich_xmltv_programmes = _instance.enrich_xmltv_programmes
