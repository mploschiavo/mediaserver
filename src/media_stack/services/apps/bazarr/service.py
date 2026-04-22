"""Bazarr bootstrap orchestration service."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable
from media_stack.api.services.registry import service_internal_url

LogFn = Callable[[str], None]
BoolCfgFn = Callable[[dict[str, Any], str, bool], bool]
NormalizeUrlFn = Callable[[str], str]
WaitForServiceFn = Callable[[str, str, str, int], None]
GetArrAppFn = Callable[[list[dict[str, Any]], str], dict[str, Any] | None]
ParseServiceUrlFn = Callable[[str, int], dict[str, Any]]
CoerceListFn = Callable[[Any], list[Any]]
ResolvePathFn = Callable[[str, str], Any]
BazarrApplyScalarUpdatesFn = Callable[[str, dict[str, Any]], tuple[str, bool]]


@dataclass
class BazarrService:
    log: LogFn
    bool_cfg: BoolCfgFn
    normalize_url: NormalizeUrlFn
    wait_for_service: WaitForServiceFn
    get_arr_app: GetArrAppFn
    parse_service_url: ParseServiceUrlFn
    coerce_list: CoerceListFn
    resolve_path: ResolvePathFn
    apply_scalar_updates: BazarrApplyScalarUpdatesFn

    def ensure_arr_integration(
        self,
        cfg: dict[str, Any],
        config_root: str,
        arr_apps: list[dict[str, Any]],
        app_keys: dict[str, str],
        wait_timeout: int,
    ) -> bool:
        bazarr_cfg = cfg.get("bazarr") or {}
        if not self.bool_cfg(bazarr_cfg, "enabled", False):
            return False

        bazarr_url = self.normalize_url(bazarr_cfg.get("url", service_internal_url("bazarr")))
        self.wait_for_service("Bazarr", bazarr_url, "/", wait_timeout)

        sonarr_cfg = self.get_arr_app(arr_apps, "Sonarr")
        radarr_cfg = self.get_arr_app(arr_apps, "Radarr")
        sonarr_key = (app_keys.get("Sonarr") or "").strip()
        radarr_key = (app_keys.get("Radarr") or "").strip()

        if not sonarr_cfg and not radarr_cfg:
            self.log(
                "[WARN] Bazarr: no Sonarr/Radarr app config found; skipping integration wiring."
            )
            return False

        config_rel_path = str(
            bazarr_cfg.get("config_relative_path") or "bazarr/config/config.yaml"
        ).strip()
        config_path = self.resolve_path(config_root, config_rel_path)
        if not config_path.exists():
            raise RuntimeError(
                f"Bazarr: config file not found at {config_path}. "
                "Ensure Bazarr has started at least once."
            )

        updates: dict[str, Any] = {"general": {}}
        bazarr_base_url = str(bazarr_cfg.get("base_url", "")).strip()
        if bazarr_base_url:
            updates["general"]["base_url"] = bazarr_base_url

        if sonarr_cfg and sonarr_key:
            parsed = self.parse_service_url(sonarr_cfg["url"], 8989)
            updates["general"]["use_sonarr"] = True
            updates["sonarr"] = {
                "apikey": sonarr_key,
                "ip": parsed["hostname"],
                "port": parsed["port"],
                "base_url": parsed["base_url"] or "/",
                "ssl": parsed["use_ssl"],
            }
        elif sonarr_cfg and not sonarr_key:
            self.log(
                "[WARN] Bazarr: Sonarr config exists but Sonarr API key is missing; skipping Sonarr link."
            )
            updates["general"]["use_sonarr"] = False

        if radarr_cfg and radarr_key:
            parsed = self.parse_service_url(radarr_cfg["url"], 7878)
            updates["general"]["use_radarr"] = True
            updates["radarr"] = {
                "apikey": radarr_key,
                "ip": parsed["hostname"],
                "port": parsed["port"],
                "base_url": parsed["base_url"] or "/",
                "ssl": parsed["use_ssl"],
            }
        elif radarr_cfg and not radarr_key:
            self.log(
                "[WARN] Bazarr: Radarr config exists but Radarr API key is missing; skipping Radarr link."
            )
            updates["general"]["use_radarr"] = False

        subtitle_defaults = bazarr_cfg.get("subtitle_defaults")
        if isinstance(subtitle_defaults, dict) and self.bool_cfg(
            subtitle_defaults, "enabled", True
        ):
            general_defaults = subtitle_defaults.get("general")
            if isinstance(general_defaults, dict):
                updates.setdefault("general", {}).update(general_defaults)

            providers = [
                str(x).strip()
                for x in self.coerce_list(subtitle_defaults.get("providers"))
                if str(x).strip()
            ]
            if providers:
                updates.setdefault("general", {})["enabled_providers"] = providers

            section_defaults = subtitle_defaults.get("sections")
            if isinstance(section_defaults, dict):
                for section_name, section_values in section_defaults.items():
                    if not isinstance(section_values, dict):
                        continue
                    normalized_section = str(section_name or "").strip()
                    if not normalized_section:
                        continue
                    updates.setdefault(normalized_section, {}).update(section_values)

        current = config_path.read_text(encoding="utf-8", errors="replace")
        rendered, changed = self.apply_scalar_updates(current, updates)
        if not changed:
            self.log(
                "[OK] Bazarr: Sonarr/Radarr + subtitle automation config already matches desired state"
            )
            return False

        config_path.write_text(rendered, encoding="utf-8")
        self.log(f"[OK] Bazarr: wrote integration config {config_path}")
        self.log("[INFO] Bazarr: restart required to apply updated integration/subtitle settings.")
        return True
