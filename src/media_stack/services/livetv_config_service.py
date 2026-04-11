"""Live TV configuration enrichment service.

Enriches tuner/guide entries from the profile with required handler
fields (type, materialized_output_path, etc.), resolves EPG provider
URLs, and applies the guide-first tuner filtering strategy.

This logic was extracted from job_framework.py to keep the job
framework thin and service-specific logic in service modules.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

import media_stack.services.runtime_platform as runtime_platform


def _url_looks_valid(url: str) -> bool:
    return url.startswith("https://") or url.startswith("http://")


_NAME_TO_CODE = {
    "united states": "us", "united kingdom": "gb", "canada": "ca",
    "australia": "au", "germany": "de", "france": "fr", "spain": "es",
    "italy": "it", "brazil": "br", "mexico": "mx", "japan": "jp",
    "south korea": "kr", "india": "in", "china": "cn", "taiwan": "tw",
    "hong kong": "hk", "philippines": "ph", "thailand": "th",
    "indonesia": "id", "netherlands": "nl", "sweden": "se",
    "norway": "no", "denmark": "dk", "finland": "fi", "poland": "pl",
    "portugal": "pt", "russia": "ru", "turkey": "tr", "israel": "il",
    "uae": "ae", "chile": "cl", "south africa": "za",
    "argentina": "ar", "colombia": "co",
}


def extract_country_code(name: str, url: str) -> str:
    """Try to extract a 2-letter country code from a guide name or URL."""
    m = re.search(r'[/_-]([a-zA-Z]{2})[\d]*\.(xml|m3u)', url)
    if m:
        return m.group(1).lower()
    name_lower = name.lower().replace(" epg", "").replace(" iptv", "").strip()
    return _NAME_TO_CODE.get(name_lower, "")


def enrich_livetv_entries(cfg: dict[str, Any], profile: dict[str, Any]) -> None:
    """Build tuner+guide lists, guides-first.

    Strategy: resolve guides first via the EPG provider fallback chain.
    Only include tuners whose guide is confirmed working. This ensures
    every channel has programme data (no blank guide rows).

    Profile flag ``load_all_tuners`` (default False) overrides this and
    loads every tuner regardless of guide availability.
    """
    ms_id = cfg.get("technology_bindings", {}).get("media_server", "")
    livetv_key = f"{ms_id}_livetv" if ms_id else ""
    livetv = cfg.get(livetv_key) if livetv_key else None
    if not isinstance(livetv, dict):
        return

    ltv_defaults = profile.get("live_tv_defaults", {})
    from media_stack.services.epg_provider_service import get_tuner_providers, get_guide_providers
    _tp = get_tuner_providers()
    _gp = get_guide_providers()
    tuner_tpl = ltv_defaults.get("tuner_url_template", _tp[0].get("url_template", "") if _tp else "")
    guide_tpl = ltv_defaults.get("guide_url_template", _gp[0].get("url_template", "") if _gp else "")
    load_all = ltv_defaults.get("load_all_tuners", False)

    raw_tuners = livetv.get("tuners", [])
    raw_guides = livetv.get("guides", [])

    # Step 1: Resolve and validate guides first
    resolved_guides: list[dict[str, Any]] = []
    guide_country_codes: set[str] = set()

    for guide in raw_guides:
        if not isinstance(guide, dict):
            continue
        guide = dict(guide)
        url = guide.pop("url", None)
        if url and "path" not in guide:
            if url.startswith("/"):
                from urllib.parse import urlparse
                parsed = urlparse(guide_tpl)
                url = f"{parsed.scheme}://{parsed.netloc}{url}"
            guide["path"] = url

        path = guide.get("path", "")
        guide_name = guide.get("name", "")
        code = extract_country_code(guide_name, path)

        if code:
            try:
                from media_stack.services.epg_provider_service import resolve_guide_url
                resolved = resolve_guide_url(code)
                if resolved:
                    guide["path"] = resolved
            except Exception:
                pass

        path = guide.get("path", "")
        if not _url_looks_valid(path):
            continue

        guide.setdefault("type", "xmltv")
        guide.setdefault("enrich_program_icons_from_tuner_logo", True)
        guide.setdefault("enrich_program_categories_from_tuner_groups", True)
        guide.setdefault("enable_all_tuners", False)
        if "materialized_output_path" not in guide:
            slug = hashlib.md5(path.encode()).hexdigest()[:8]
            name_slug = (guide_name or "unknown").lower().replace(" ", "-").replace("/", "-")[:20]
            guide["materialized_output_path"] = f"{ms_id}/livetv-guides/{name_slug}-{slug}.xml"

        resolved_guides.append(guide)
        if code:
            guide_country_codes.add(code)

    # Step 2: Build tuner list — only include tuners with a matching guide
    resolved_tuners: list[dict[str, Any]] = []
    for tuner in raw_tuners:
        if not isinstance(tuner, dict):
            continue
        tuner = dict(tuner)
        url = tuner.get("url", "")
        if url.startswith("/"):
            from urllib.parse import urlparse
            parsed = urlparse(tuner_tpl)
            tuner["url"] = f"{parsed.scheme}://{parsed.netloc}{url}"
            url = tuner["url"]

        tuner_name = tuner.get("name", "")
        code = extract_country_code(tuner_name, url)

        if not load_all and code and code not in guide_country_codes:
            runtime_platform.log(
                f"[INFO] Live TV: skipping tuner '{tuner_name}' ({code}) — no working guide. "
                "Set load_all_tuners=true in profile to override."
            )
            continue

        tuner.setdefault("type", "m3u")
        tuner.setdefault("normalize_tvg_id_suffix", True)
        tuner.setdefault("filter_to_guide_channels", True)
        tuner.setdefault("allow_hw_transcoding", True)
        if "materialized_output_path" not in tuner:
            slug = hashlib.md5(url.encode()).hexdigest()[:8]
            name_slug = (tuner_name or "unknown").lower().replace(" ", "-").replace("/", "-")[:20]
            tuner["materialized_output_path"] = f"{ms_id}/livetv-tuners/{name_slug}-{slug}.m3u"

        resolved_tuners.append(tuner)

    livetv["guides"] = resolved_guides
    livetv["tuners"] = resolved_tuners
    runtime_platform.log(
        f"[INFO] Live TV: {len(resolved_guides)} guides resolved, "
        f"{len(resolved_tuners)} tuners selected "
        f"(load_all_tuners={load_all}, skipped={len(raw_tuners) - len(resolved_tuners)})"
    )
