from __future__ import annotations

import re

from .common import normalize_base_path, to_int


class ServarrAdapters:
    """Adapter utilities for Servarr-family applications (Sonarr, Radarr, etc.)."""

    @staticmethod
    def _norm_profile_name(value):
        text = str(value or "").strip().lower()
        return re.sub(r"[^a-z0-9]+", "", text)

    @staticmethod
    def choose_profile(profiles, preferred_id=None, preferred_names=None):
        preferred_int = to_int(preferred_id)
        if preferred_int is not None:
            for profile in profiles:
                if to_int(profile.get("id")) == preferred_int:
                    return profile

        preferred_name_tokens = [
            ServarrAdapters._norm_profile_name(name) for name in (preferred_names or []) if ServarrAdapters._norm_profile_name(name)
        ]
        if preferred_name_tokens:
            normalized_profiles = [
                (ServarrAdapters._norm_profile_name(profile.get("name")), profile) for profile in (profiles or [])
            ]
            for token in preferred_name_tokens:
                for profile_name, profile in normalized_profiles:
                    if profile_name == token or token in profile_name:
                        return profile

        if profiles:
            return profiles[0]
        return None

    @staticmethod
    def choose_root_folder(root_folders, preferred_path):
        preferred_norm = str(preferred_path or "").rstrip("/")
        if preferred_norm:
            for folder in root_folders:
                folder_path = str(folder.get("path", "")).rstrip("/")
                if folder_path == preferred_norm:
                    return folder_path
        if root_folders:
            return str(root_folders[0].get("path", "")).rstrip("/")
        return preferred_norm

    @staticmethod
    def _normalize_mapping_path(value):
        text = str(value or "").strip()
        if not text:
            return ""
        if text != "/":
            text = text.rstrip("/")
        return text

    @staticmethod
    def normalize_remote_path_mappings(mappings):
        normalized = []
        seen = set()
        for mapping in mappings or []:
            if not isinstance(mapping, dict):
                continue
            host = str(mapping.get("host") or "").strip()
            remote_path = ServarrAdapters._normalize_mapping_path(
                mapping.get("remote_path") or mapping.get("remotePath")
            )
            local_path = ServarrAdapters._normalize_mapping_path(mapping.get("local_path") or mapping.get("localPath"))
            if not host or not remote_path or not local_path:
                continue
            key = (host.lower(), remote_path)
            if key in seen:
                continue
            seen.add(key)
            normalized.append(
                {
                    "host": host,
                    "remotePath": remote_path,
                    "localPath": local_path,
                }
            )
        return normalized

    @staticmethod
    def find_existing_servarr(existing, name, hostname, port, base_url, is4k):
        normalized_base = normalize_base_path(base_url)

        for entry in existing:
            if bool(entry.get("is4k", False)) != bool(is4k):
                continue

            entry_host = str(entry.get("hostname", "")).strip().lower()
            entry_port = to_int(entry.get("port"))
            entry_base = normalize_base_path(entry.get("baseUrl"))
            if (
                entry_host == str(hostname).strip().lower()
                and entry_port == int(port)
                and entry_base == normalized_base
            ):
                return entry

        for entry in existing:
            if bool(entry.get("is4k", False)) != bool(is4k):
                continue
            if str(entry.get("name", "")).strip().lower() == str(name).strip().lower():
                return entry

        return None


# ---------------------------------------------------------------------------
# Singleton + backward-compat module-level references
# ---------------------------------------------------------------------------

_instance = ServarrAdapters()
choose_profile = _instance.choose_profile
choose_root_folder = _instance.choose_root_folder
normalize_remote_path_mappings = _instance.normalize_remote_path_mappings
find_existing_servarr = _instance.find_existing_servarr
_norm_profile_name = _instance._norm_profile_name
_normalize_mapping_path = _instance._normalize_mapping_path
