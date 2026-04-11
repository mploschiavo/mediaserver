"""EPG provider registry — resolves the best guide URL for a country.

Loads providers from contracts/epg_providers.yaml and tries them in
priority order. Caches health check results to avoid re-probing.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


_PROVIDER_CACHE: dict[str, Any] | None = None
_HEALTH_CACHE: dict[str, Any] | None = None


def _find_providers_yaml() -> Path | None:
    candidates = [
        Path(os.environ.get("EPG_PROVIDERS_FILE", "")) if os.environ.get("EPG_PROVIDERS_FILE") else None,
        Path("/opt/media-stack/contracts/epg_providers.yaml"),
        Path(__file__).resolve().parents[3] / "contracts" / "epg_providers.yaml",
        Path("contracts/epg_providers.yaml"),
    ]
    for p in candidates:
        if p and p.is_file():
            return p
    return None


def _load_providers() -> dict[str, Any]:
    global _PROVIDER_CACHE
    if _PROVIDER_CACHE is not None:
        return _PROVIDER_CACHE
    path = _find_providers_yaml()
    if not path:
        _PROVIDER_CACHE = {}
        return _PROVIDER_CACHE
    import yaml
    _PROVIDER_CACHE = yaml.safe_load(path.read_text()) or {}
    return _PROVIDER_CACHE


def get_guide_providers() -> list[dict[str, Any]]:
    """Return enabled guide providers sorted by priority."""
    data = _load_providers()
    providers = data.get("guide_providers", [])
    return sorted(
        [p for p in providers if p.get("enabled", True)],
        key=lambda p: p.get("priority", 99),
    )


def get_tuner_providers() -> list[dict[str, Any]]:
    """Return enabled tuner providers sorted by priority."""
    data = _load_providers()
    providers = data.get("tuner_providers", [])
    return sorted(
        [p for p in providers if p.get("enabled", True)],
        key=lambda p: p.get("priority", 99),
    )


def _expand_url(provider: dict[str, Any], country_code: str) -> str:
    """Build the URL for a country from a provider's template or country_urls."""
    code = country_code.lower()
    # Check explicit country_urls first
    country_urls = provider.get("country_urls", {})
    if isinstance(country_urls, dict) and code in country_urls:
        return country_urls[code]
    # Use template
    template = provider.get("url_template", "")
    if not template:
        return ""
    return template.replace("{code}", code).replace("{CODE}", code.upper())


def _health_cache_path() -> Path:
    config_root = os.environ.get("CONFIG_ROOT", "/srv-config")
    return Path(config_root) / ".controller" / "epg-provider-health.json"


def _load_health_cache() -> dict[str, Any]:
    global _HEALTH_CACHE
    if _HEALTH_CACHE is not None:
        return _HEALTH_CACHE
    path = _health_cache_path()
    if path.is_file():
        try:
            _HEALTH_CACHE = json.loads(path.read_text())
            return _HEALTH_CACHE
        except Exception:
            pass
    _HEALTH_CACHE = {}
    return _HEALTH_CACHE


def _save_health_cache(cache: dict[str, Any]) -> None:
    global _HEALTH_CACHE
    _HEALTH_CACHE = cache
    try:
        path = _health_cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cache, indent=2))
    except Exception:
        pass


def _probe_url(url: str, timeout: int = 10) -> bool:
    """Check if a URL is reachable (HEAD request, or GET for GitHub raw)."""
    if not url:
        return False
    try:
        # GitHub raw and some CDNs don't support HEAD well — use GET with range
        headers = {"Range": "bytes=0-0", "User-Agent": "media-stack-controller/1.0"}
        req = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status in (200, 206, 301, 302)
    except urllib.error.HTTPError as exc:
        return exc.code in (200, 206, 301, 302)
    except Exception:
        return False


def check_provider_health(provider_id: str, country_code: str, url: str) -> bool:
    """Check if a provider URL is healthy, using cache if fresh."""
    data = _load_providers()
    hc = data.get("health_check", {})
    interval = hc.get("interval_hours", 24) * 3600
    timeout = hc.get("timeout_seconds", 10)

    cache = _load_health_cache()
    cache_key = f"{provider_id}:{country_code}"
    entry = cache.get(cache_key, {})
    last_check = entry.get("ts", 0)

    if time.time() - last_check < interval:
        return entry.get("ok", False)

    ok = _probe_url(url, timeout=timeout)
    cache[cache_key] = {"ts": time.time(), "ok": ok, "url": url}
    _save_health_cache(cache)
    return ok


def resolve_guide_url(country_code: str, log: Any = None) -> str:
    """Find the best working guide URL for a country.

    Tries providers in priority order, returns the first healthy URL.
    """
    code = country_code.lower()
    providers = get_guide_providers()

    for provider in providers:
        url = _expand_url(provider, code)
        if not url:
            continue
        pid = provider.get("id", "unknown")
        if check_provider_health(pid, code, url):
            if log:
                log(f"[INFO] EPG: using {pid} for {code}: {url}")
            return url

    # Nothing cached as healthy — try each one live
    for provider in providers:
        url = _expand_url(provider, code)
        if not url:
            continue
        pid = provider.get("id", "unknown")
        if _probe_url(url, timeout=10):
            # Update cache
            cache = _load_health_cache()
            cache[f"{pid}:{code}"] = {"ts": time.time(), "ok": True, "url": url}
            _save_health_cache(cache)
            if log:
                log(f"[OK] EPG: found working provider {pid} for {code}: {url}")
            return url
        else:
            cache = _load_health_cache()
            cache[f"{pid}:{code}"] = {"ts": time.time(), "ok": False, "url": url}
            _save_health_cache(cache)
            if log:
                log(f"[WARN] EPG: {pid} unavailable for {code}: {url}")

    if log:
        log(f"[WARN] EPG: no working guide provider found for {code}")
    return ""


def resolve_tuner_url(country_code: str, log: Any = None) -> str:
    """Find the best working tuner URL for a country."""
    code = country_code.lower()
    for provider in get_tuner_providers():
        url = _expand_url(provider, code)
        if url:
            return url
    return ""


def run_health_check(log: Any = None) -> dict[str, Any]:
    """Probe all providers for all known countries. Returns health report."""
    from .api.services.config import get_iptv_countries
    countries_data = get_iptv_countries()
    countries = countries_data.get("countries", [])

    providers = get_guide_providers()
    results: dict[str, dict[str, bool]] = {}
    healthy = 0
    unhealthy = 0

    for country in countries:
        code = country.get("code", "")
        if not code:
            continue
        results[code] = {}
        for provider in providers:
            url = _expand_url(provider, code)
            if not url:
                continue
            pid = provider.get("id", "unknown")
            ok = _probe_url(url, timeout=10)
            results[code][pid] = ok
            cache = _load_health_cache()
            cache[f"{pid}:{code}"] = {"ts": time.time(), "ok": ok, "url": url}
            _save_health_cache(cache)
            if ok:
                healthy += 1
            else:
                unhealthy += 1

    if log:
        log(f"[INFO] EPG health check: {healthy} healthy, {unhealthy} unhealthy across {len(countries)} countries")

    return {
        "healthy": healthy,
        "unhealthy": unhealthy,
        "countries": len(countries),
        "providers": len(providers),
        "details": results,
    }


def invalidate_cache() -> None:
    """Clear cached provider data (forces reload from YAML)."""
    global _PROVIDER_CACHE, _HEALTH_CACHE
    _PROVIDER_CACHE = None
    _HEALTH_CACHE = None
