"""SABnzbd integration service."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Callable
from urllib import parse

HttpRequestFn = Callable[..., tuple[int, Any, str]]
NormalizeUrlFn = Callable[[str], str]
NormalizeMappingPathFn = Callable[[Any], str]
ChooseCategoryFn = Callable[[dict[str, Any], dict[str, Any]], str]
CoerceListFn = Callable[[Any], list[Any]]
ResolvePathFn = Callable[[str, str], Any]
LogFn = Callable[[str], None]


@dataclass
class SabnzbdService:
    http_request: HttpRequestFn
    normalize_url: NormalizeUrlFn
    normalize_mapping_path: NormalizeMappingPathFn
    choose_category: ChooseCategoryFn
    coerce_list: CoerceListFn
    resolve_path: ResolvePathFn
    log: LogFn

    def read_api_key(self, config_root: str, sab_cfg: dict[str, Any]) -> str:
        env_name = str(sab_cfg.get("api_key_env", "SABNZBD_API_KEY")).strip() or "SABNZBD_API_KEY"
        env_value = (os.environ.get(env_name) or "").strip()
        if env_value:
            self.log(f"[OK] SABnzbd: using API key from env {env_name}")
            return env_value

        ini_rel_path = sab_cfg.get("api_key_config_path", "sabnzbd/sabnzbd.ini")
        ini_path = self.resolve_path(config_root, ini_rel_path)
        if not ini_path.exists():
            return ""

        text = ini_path.read_text(encoding="utf-8", errors="replace")
        match = re.search(r"^\s*api_key\s*=\s*(\S+)\s*$", text, flags=re.MULTILINE)
        if match:
            self.log(f"[OK] SABnzbd: discovered API key from {ini_path}")
            return match.group(1).strip()

        return ""

    def request(
        self,
        base_url: str,
        api_key: str,
        params: dict[str, Any],
        timeout: int = 20,
    ) -> tuple[int, Any, str]:
        query = dict(params or {})
        query["apikey"] = api_key
        query["output"] = "json"
        path = f"/api?{parse.urlencode(query)}"
        return self.http_request(self.normalize_url(base_url), path, timeout=timeout)

    def get_config_section(self, base_url: str, sab_api_key: str, section: str) -> Any:
        status, data, body = self.request(
            base_url,
            sab_api_key,
            {"mode": "get_config", "section": section},
        )
        if status != 200 or not isinstance(data, dict):
            raise RuntimeError(
                f"SABnzbd: failed reading config section '{section}' (HTTP {status}): {body}"
            )
        config = data.get("config", {})
        if not isinstance(config, dict):
            return None
        return config.get(section)

    def ensure_defaults(self, sab_cfg: dict[str, Any], sab_api_key: str) -> None:
        if not sab_api_key:
            return

        sab_url = self.normalize_url(sab_cfg.get("url", "http://sabnzbd:8080"))
        misc = self.get_config_section(sab_url, sab_api_key, "misc")
        if not isinstance(misc, dict):
            raise RuntimeError("SABnzbd: unexpected misc config payload from API.")

        desired_misc = {
            "download_dir": str(sab_cfg.get("incomplete_dir", "/data/usenet/incomplete")).strip(),
            "complete_dir": str(sab_cfg.get("complete_dir", "/data/usenet/completed")).strip(),
        }
        if "auto_browser" in sab_cfg:
            desired_misc["auto_browser"] = "1" if bool(sab_cfg.get("auto_browser")) else "0"

        for key, desired in desired_misc.items():
            if not desired:
                continue
            current = misc.get(key)
            if isinstance(current, bool):
                current_normalized = "1" if current else "0"
            elif current is None:
                current_normalized = ""
            else:
                current_normalized = str(current).strip()

            desired_normalized = str(desired).strip()
            if current_normalized == desired_normalized:
                self.log(f"[OK] SABnzbd: {key} already set to {desired_normalized}")
                continue

            status, data, body = self.request(
                sab_url,
                sab_api_key,
                {
                    "mode": "set_config",
                    "section": "misc",
                    "keyword": key,
                    "value": desired_normalized,
                },
            )
            if status != 200:
                raise RuntimeError(
                    f"SABnzbd: failed setting misc.{key} (HTTP {status}): {body}"
                )
            if isinstance(data, dict) and data.get("status") is False:
                raise RuntimeError(f"SABnzbd: API rejected misc.{key} update request: {body}")
            self.log(f"[OK] SABnzbd: set {key}={desired_normalized}")

    def ensure_categories(
        self,
        arr_apps: list[dict[str, Any]],
        sab_cfg: dict[str, Any],
        sab_api_key: str,
    ) -> None:
        if not sab_api_key:
            return

        sab_url = self.normalize_url(sab_cfg.get("url", "http://sabnzbd:8080"))
        categories_section = self.get_config_section(sab_url, sab_api_key, "categories")
        current_by_name: dict[str, str] = {}
        if isinstance(categories_section, list):
            for entry in categories_section:
                if not isinstance(entry, dict):
                    continue
                name = str(entry.get("name") or "").strip()
                if not name:
                    continue
                current_by_name[name.lower()] = self.normalize_mapping_path(entry.get("dir"))
        else:
            status, data, body = self.request(sab_url, sab_api_key, {"mode": "get_cats"})
            if status != 200 or not isinstance(data, dict):
                raise RuntimeError(f"SABnzbd: failed listing categories (HTTP {status}): {body}")
            for category_name in self.coerce_list(data.get("categories")):
                c = str(category_name).strip()
                if c:
                    current_by_name[c.lower()] = ""

        category_values = [self.choose_category(app, sab_cfg) for app in arr_apps]
        desired_categories: list[str] = []
        seen: set[str] = set()
        for cat in category_values:
            c = str(cat).strip()
            if not c:
                continue
            low = c.lower()
            if low in seen:
                continue
            seen.add(low)
            desired_categories.append(c)

        completed_paths = sab_cfg.get("completed_paths", {})
        complete_root = (
            self.normalize_mapping_path(sab_cfg.get("complete_dir", "/data/usenet/completed"))
            or "/data/usenet/completed"
        )
        for category in desired_categories:
            current_dir = current_by_name.get(category.lower())
            category_dir = self.normalize_mapping_path(
                completed_paths.get(category, f"{complete_root}/{category}")
            )
            if current_dir is not None and current_dir == category_dir:
                self.log(f"[OK] SABnzbd: category already set: {category} -> {category_dir}")
                continue

            status, data, body = self.request(
                sab_url,
                sab_api_key,
                {
                    "mode": "set_config",
                    "section": "categories",
                    "name": category,
                    "dir": category_dir,
                },
            )
            if status != 200:
                raise RuntimeError(
                    f"SABnzbd: failed creating category '{category}' (HTTP {status}): {body}"
                )
            if isinstance(data, dict) and data.get("status") is False:
                raise RuntimeError(
                    f"SABnzbd: API rejected category '{category}' create request: {body}"
                )
            action = "updated" if current_dir is not None else "created"
            self.log(f"[OK] SABnzbd: {action} category {category} -> {category_dir}")
