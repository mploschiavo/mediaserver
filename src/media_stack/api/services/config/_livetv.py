"""IPTV / Live TV source configuration."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from ._profile import ProfileService


class LiveTvConfigService:
    """Manages IPTV tuner and guide source configuration."""

    # Sections migrated to per-app config — stripped from profile API response.
    APP_CONFIG_SECTIONS: frozenset[str] = frozenset({"live_tv_defaults", "download_categories"})
    DEDICATED_ENDPOINT_SECTIONS: frozenset[str] = frozenset({"routing", "auth", "bootstrap", "chaos", "app_auth"})
    STRIPPED_FROM_PROFILE: frozenset[str] = APP_CONFIG_SECTIONS | DEDICATED_ENDPOINT_SECTIONS

    _DEFAULTS_DIR: Path = Path(__file__).resolve().parents[5] / "contracts" / "ui_defaults"

    def __init__(self, profile: ProfileService):
        self._profile = profile

    @classmethod
    def _load_default_countries(cls) -> list[dict[str, str]]:
        """Load IPTV countries from contracts/defaults/iptv_countries.yaml."""
        path = cls._DEFAULTS_DIR / "iptv_countries.yaml"
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            countries = data.get("countries", [])
            return countries if isinstance(countries, list) else []
        except Exception:
            return [{"code": "us", "name": "United States"}]

    def get_livetv_sources(self) -> dict[str, Any]:
        from media_stack.services.app_config_service import load_app_config
        ms_id = self._profile.media_server_id()
        app_cfg = load_app_config(ms_id) if ms_id else {}
        from_app_config = "livetv" in app_cfg
        ltv = app_cfg.get("livetv", {})
        if not from_app_config:
            data, _ = self._profile.load()
            ltv = data.get("live_tv_defaults", {})
        tuners = ltv.get("tuners", [])
        guides = ltv.get("guides", [])
        if not from_app_config:
            if not tuners and ltv.get("tuner_url"):
                tuners = [{"url": ltv["tuner_url"], "name": "Default"}]
            if not guides and ltv.get("guide_url"):
                guides = [{"url": ltv["guide_url"], "name": "Default"}]
        return {
            "tuners": tuners, "guides": guides,
            "tuner_url": tuners[0]["url"] if tuners else "",
            "guide_url": guides[0]["url"] if guides else "",
            "load_all_tuners": bool(ltv.get("load_all_tuners", False)),
            "source": "app_config" if app_cfg.get("livetv") else ("profile" if tuners else "not_configured"),
        }

    def update_livetv_sources(
        self,
        tuners: list[dict[str, str]] | None = None,
        guides: list[dict[str, str]] | None = None,
        tuner_url: str = "", guide_url: str = "",
        load_all_tuners: bool | None = None,
    ) -> dict[str, Any]:
        from media_stack.services.app_config_service import load_app_config, save_app_config
        ms_id = self._profile.media_server_id()
        if not ms_id:
            return {"error": "No media server configured"}
        app_cfg = load_app_config(ms_id)
        ltv = app_cfg.get("livetv", {})
        if load_all_tuners is not None:
            ltv["load_all_tuners"] = bool(load_all_tuners)
        if tuners is not None:
            ltv["tuners"] = tuners
        elif tuner_url:
            ltv["tuners"] = [{"url": tuner_url, "name": "Default"}]
        if guides is not None:
            ltv["guides"] = guides
        elif guide_url:
            ltv["guides"] = [{"url": guide_url, "name": "Default"}]
        if ltv.get("tuners"):
            ltv["tuner_url"] = ltv["tuners"][0].get("url", "")
        if ltv.get("guides"):
            ltv["guide_url"] = ltv["guides"][0].get("url", "")
        app_cfg["livetv"] = ltv
        result = save_app_config(ms_id, app_cfg)
        if "error" not in result:
            result["tuners"] = ltv.get("tuners", [])
            result["guides"] = ltv.get("guides", [])
            result["note"] = "Run configure-livetv to apply Live TV changes"
        return result

    def get_discovery_lists(self) -> dict[str, Any]:
        data, _ = self._profile.load()
        lists = data.get("discovery_lists", [])
        if not isinstance(lists, list):
            lists = []
        return {"lists": lists, "count": len(lists)}

    def update_discovery_lists(self, lists: list[dict[str, Any]]) -> dict[str, Any]:
        result = self._profile.update_section("discovery_lists", lists)
        if "error" not in result:
            result["lists"] = lists
            result["note"] = "Run bootstrap to apply discovery list changes"
        return result

    def get_iptv_countries(self) -> dict[str, Any]:
        data, _ = self._profile.load()
        custom = data.get("iptv_countries")
        if isinstance(custom, list) and custom:
            return {"countries": custom, "source": "profile"}
        ltv_defaults = data.get("live_tv_defaults", {})
        tuner_tpl = ltv_defaults.get("tuner_url_template", "")
        guide_tpl = ltv_defaults.get("guide_url_template", "")
        from media_stack.services.epg_provider_service import get_guide_providers, _expand_url
        guide_providers = get_guide_providers()
        countries = []
        for entry in self._load_default_countries():
            c = entry.get("code", "")
            n = entry.get("name", "")
            g_url = ""
            for p in guide_providers:
                url = _expand_url(p, c)
                if url:
                    g_url = url
                    break
            countries.append({
                "code": c, "name": n,
                "tuner_url": tuner_tpl.replace("{code}", c),
                "guide_url": g_url or guide_tpl.replace("{code}", c),
            })
        return {"countries": countries, "source": "defaults"}


# Package-level constants re-exported by __init__.py
APP_CONFIG_SECTIONS = LiveTvConfigService.APP_CONFIG_SECTIONS
DEDICATED_ENDPOINT_SECTIONS = LiveTvConfigService.DEDICATED_ENDPOINT_SECTIONS
STRIPPED_FROM_PROFILE = LiveTvConfigService.STRIPPED_FROM_PROFILE
