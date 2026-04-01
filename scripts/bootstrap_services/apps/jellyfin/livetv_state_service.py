"""Jellyfin Live TV state parsing and reconcile helper operations."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib import parse

CoerceListFn = Callable[[Any], list[Any]]
ResolvePathFn = Callable[[str, Any], Path]
CandidateRootsFn = Callable[[str], list[Path]]
JellyfinRequestFn = Callable[..., tuple[int, Any, str]]
LogFn = Callable[[str], None]


@dataclass
class JellyfinLiveTvStateService:
    coerce_list: CoerceListFn
    resolve_path: ResolvePathFn
    candidate_config_roots: CandidateRootsFn
    jellyfin_request: JellyfinRequestFn
    log: LogFn

    def load_state(self, config_root: str, live_cfg: dict[str, Any]) -> dict[str, Any]:
        xml_rel_path = live_cfg.get("livetv_xml_path", "jellyfin/config/livetv.xml")
        candidate_paths = [
            self.resolve_path(str(root), xml_rel_path)
            for root in self.candidate_config_roots(config_root)
        ]
        xml_path = None
        for candidate in candidate_paths:
            if candidate.exists():
                xml_path = candidate
                break
        if xml_path is None:
            xml_path = candidate_paths[0]
        if not xml_path.exists():
            return {
                "tuner_keys": set(),
                "guide_keys": set(),
                "tuner_ids_by_key": {},
                "tuners_by_key": {},
                "guides_by_key": {},
                "source_path": str(xml_path),
            }

        try:
            root = ET.fromstring(xml_path.read_text(encoding="utf-8", errors="replace"))
        except ET.ParseError as exc:
            raise RuntimeError(f"Failed parsing Jellyfin Live TV config {xml_path}: {exc}") from exc

        tuner_keys = set()
        guide_keys = set()
        tuner_ids_by_key = {}
        tuners_by_key = defaultdict(list)
        guides_by_key = defaultdict(list)

        for node in root.findall("./TunerHosts/TunerHostInfo"):
            tuner_type = str((node.findtext("Type") or "")).strip().lower()
            tuner_url = str((node.findtext("Url") or "")).strip()
            tuner_id = str((node.findtext("Id") or "")).strip()
            if tuner_type and tuner_url:
                key = (tuner_type, tuner_url)
                tuner_keys.add(key)
                if tuner_id:
                    tuner_ids_by_key[key] = tuner_id
                tuners_by_key[key].append(
                    {
                        "id": tuner_id,
                        "type": tuner_type,
                        "url": tuner_url,
                    }
                )

        for node in root.findall("./ListingProviders/ListingsProviderInfo"):
            guide_type = str((node.findtext("Type") or "")).strip().lower()
            guide_path = str((node.findtext("Path") or "")).strip()
            if guide_type and guide_path:
                key = (guide_type, guide_path)
                guide_keys.add(key)
                enabled_tuners = []
                for tuner_node in node.findall("./EnabledTuners/string"):
                    value = str((tuner_node.text or "")).strip()
                    if value:
                        enabled_tuners.append(value)
                enable_all_tuners_raw = (
                    str((node.findtext("EnableAllTuners") or "")).strip().lower()
                )
                enable_all_tuners = enable_all_tuners_raw in ("1", "true", "yes", "on")
                guides_by_key[key].append(
                    {
                        "id": str((node.findtext("Id") or "")).strip(),
                        "type": guide_type,
                        "path": guide_path,
                        "enabled_tuners": enabled_tuners,
                        "enable_all_tuners": enable_all_tuners,
                    }
                )

        return {
            "tuner_keys": tuner_keys,
            "guide_keys": guide_keys,
            "tuner_ids_by_key": tuner_ids_by_key,
            "tuners_by_key": dict(tuners_by_key),
            "guides_by_key": dict(guides_by_key),
            "source_path": str(xml_path),
        }

    def resolve_tuner_type_id(
        self,
        jellyfin_url: str,
        jellyfin_api_key: str,
        requested_type: str,
    ) -> str:
        status, data, body = self.jellyfin_request(
            jellyfin_url, "/LiveTv/TunerHosts/Types", jellyfin_api_key
        )
        if status != 200 or not isinstance(data, list):
            raise RuntimeError(
                f"Jellyfin Live TV: failed to list tuner host types (HTTP {status}): {body}"
            )

        requested_norm = str(requested_type or "m3u").strip().lower()
        id_map = {}
        name_map = {}
        for item in data:
            type_id = str(item.get("Id") or "").strip()
            type_name = str(item.get("Name") or "").strip()
            if not type_id:
                continue
            id_map[type_id.lower()] = type_id
            if type_name:
                name_map[type_name.lower()] = type_id

        if requested_norm in id_map:
            return id_map[requested_norm]
        if requested_norm in name_map:
            return name_map[requested_norm]

        for type_name, type_id in name_map.items():
            if requested_norm in type_name:
                return type_id

        available = sorted(set(list(id_map.values()) + list(name_map.keys())))
        raise RuntimeError(
            "Jellyfin Live TV: requested tuner type "
            f"'{requested_type}' not available. Available: {available}"
        )

    def normalize_enabled_tuner_ids(
        self,
        enabled_tuners: Any,
        state: dict[str, Any],
    ) -> list[str]:
        out = []
        for item in self.coerce_list(enabled_tuners):
            value = str(item or "").strip()
            if not value:
                continue
            if value.startswith("tuner-url:"):
                raw_url = value.split(":", 1)[1].strip()
                for key, tuner_id in state["tuner_ids_by_key"].items():
                    _, tuner_url = key
                    if tuner_url == raw_url and tuner_id:
                        out.append(tuner_id)
            elif value.startswith("tuner-type-url:"):
                raw = value.split(":", 1)[1].strip()
                if "|" in raw:
                    raw_type, raw_url = raw.split("|", 1)
                    lookup = (raw_type.strip().lower(), raw_url.strip())
                    tuner_id = state["tuner_ids_by_key"].get(lookup)
                    if tuner_id:
                        out.append(tuner_id)
            else:
                out.append(value)

        deduped = []
        seen = set()
        for item in out:
            if item in seen:
                continue
            seen.add(item)
            deduped.append(item)
        return deduped

    def delete_entity(
        self,
        jellyfin_url: str,
        jellyfin_api_key: str,
        entity: str,
        entity_id: str,
    ) -> None:
        normalized_id = str(entity_id or "").strip()
        if not normalized_id:
            return

        if entity == "tuner":
            endpoint = "/LiveTv/TunerHosts"
        elif entity == "guide":
            endpoint = "/LiveTv/ListingProviders"
        else:
            raise RuntimeError(f"Unsupported Jellyfin Live TV entity type: {entity}")

        encoded_id = parse.quote(normalized_id, safe="")
        status, _, body = self.jellyfin_request(
            jellyfin_url,
            f"{endpoint}?id={encoded_id}",
            jellyfin_api_key,
            method="DELETE",
        )
        if status not in (200, 204):
            raise RuntimeError(
                f"Jellyfin Live TV: failed deleting {entity} id={normalized_id} "
                f"(HTTP {status}): {body}"
            )

    def trigger_scheduled_task(
        self,
        jellyfin_url: str,
        jellyfin_api_key: str,
        preferred_names: list[str],
    ) -> tuple[bool, str]:
        status, tasks, body = self.jellyfin_request(
            jellyfin_url,
            "/ScheduledTasks",
            jellyfin_api_key,
        )
        if status != 200 or not isinstance(tasks, list):
            return False, f"failed to list tasks (HTTP {status}): {body}"

        lowered = [name.lower() for name in preferred_names if str(name).strip()]
        task_id = ""
        task_name = ""
        for task in tasks:
            name = str((task or {}).get("Name") or "").strip()
            if not name:
                continue
            normalized_name = name.lower()
            if any(target in normalized_name for target in lowered):
                task_id = str((task or {}).get("Id") or "").strip()
                task_name = name
                break
        if not task_id:
            return False, f"task not found (wanted one of: {preferred_names})"

        encoded_id = parse.quote(task_id, safe="")
        run_status, _, run_body = self.jellyfin_request(
            jellyfin_url,
            f"/ScheduledTasks/Running/{encoded_id}",
            jellyfin_api_key,
            method="POST",
        )
        if run_status not in (200, 204):
            return False, f"failed to trigger task '{task_name}' (HTTP {run_status}): {run_body}"
        return True, task_name

    def trigger_refresh(
        self,
        jellyfin_url: str,
        jellyfin_api_key: str,
        endpoint_path: str,
        label: str,
    ) -> tuple[bool, str]:
        status, _, body = self.jellyfin_request(
            jellyfin_url,
            endpoint_path,
            jellyfin_api_key,
            method="POST",
        )
        if status in (200, 201, 202, 204):
            return True, f"requested {label}"

        # Jellyfin 10.11+ may expose refresh via scheduled tasks instead of
        # legacy /LiveTv/Refresh* endpoints.
        if status == 404:
            fallback_names: list[str] = []
            if "channel" in label:
                fallback_names = ["TasksRefreshChannels", "Refresh Channels"]
            elif "guide" in label:
                fallback_names = ["Refresh Guide"]
            if fallback_names:
                ok, detail = self.trigger_scheduled_task(
                    jellyfin_url,
                    jellyfin_api_key,
                    fallback_names,
                )
                if ok:
                    return True, f"requested {label} via scheduled task '{detail}'"
                return False, (
                    f"could not request {label} via endpoint (HTTP {status}); "
                    f"fallback failed: {detail}"
                )

        return False, f"could not request {label} (HTTP {status}): {body}"
