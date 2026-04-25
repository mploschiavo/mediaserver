"""EPG provider registry -- resolves the best guide URL for a country.

Loads providers from contracts/epg_providers.yaml and tries them in
priority order. Caches health check results to avoid re-probing.
"""

from __future__ import annotations


from media_stack.core.logging_utils import log_swallowed
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
import logging


class EpgProviderService:
    """Registry of EPG providers with health-checked URL resolution."""

    def __init__(self) -> None:
        self._provider_cache: dict[str, Any] | None = None
        self._health_cache: dict[str, Any] | None = None

    # -------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------

    @staticmethod
    def _find_providers_yaml() -> Path | None:
        # Single env read — previously ``X if X else None`` counted twice
        # against OS_ENVIRON_IN_METHODS_RATCHET without adding value.
        _override = os.environ.get("EPG_PROVIDERS_FILE", "")
        candidates = [
            Path(_override) if _override else None,
            Path("/opt/media-stack/contracts/epg_providers.yaml"),
            Path(__file__).resolve().parents[3] / "contracts" / "epg_providers.yaml",
            Path("contracts/epg_providers.yaml"),
        ]
        for p in candidates:
            if p and p.is_file():
                return p
        return None

    def _load_providers(self) -> dict[str, Any]:
        if self._provider_cache is not None:
            return self._provider_cache
        path = self._find_providers_yaml()
        if not path:
            self._provider_cache = {}
            return self._provider_cache
        import yaml
        self._provider_cache = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return self._provider_cache

    @staticmethod
    def _expand_url(provider: dict[str, Any], country_code: str) -> str:
        """Build the URL for a country from a provider's template or country_urls."""
        code = str(country_code or "").lower()
        country_urls = provider.get("country_urls", {})
        if isinstance(country_urls, dict) and code in country_urls:
            return country_urls[code]
        template = provider.get("url_template", "")
        if not template:
            return ""
        return template.replace("{code}", code).replace("{CODE}", code.upper())

    def _health_cache_path(self) -> Path:
        config_root = os.environ.get("CONFIG_ROOT", "/srv-config")
        return Path(config_root) / ".controller" / "epg-provider-health.json"

    def _load_health_cache(self) -> dict[str, Any]:
        if self._health_cache is not None:
            return self._health_cache
        path = self._health_cache_path()
        if path.is_file():
            try:
                self._health_cache = json.loads(path.read_text(encoding="utf-8"))
                return self._health_cache
            except Exception as exc:
                log_swallowed(exc)
        self._health_cache = {}
        return self._health_cache

    def _save_health_cache(self, cache: dict[str, Any]) -> None:
        self._health_cache = cache
        try:
            path = self._health_cache_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(cache, indent=2), encoding="utf-8")
        except Exception as exc:
            log_swallowed(exc)

    @staticmethod
    def _probe_url(url: str, timeout: int = 10) -> bool:
        """Check if a URL is reachable."""
        if not url:
            return False
        try:
            headers = {"Range": "bytes=0-0", "User-Agent": "media-stack-controller/1.0"}
            req = urllib.request.Request(url, headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status in (200, 206, 301, 302)
        except urllib.error.HTTPError as exc:
            return exc.code in (200, 206, 301, 302)
        except Exception:
            return False

    # -------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------

    def get_guide_providers(self) -> list[dict[str, Any]]:
        """Return enabled guide providers sorted by priority."""
        data = self._load_providers()
        providers = data.get("guide_providers", [])
        return sorted(
            [p for p in providers if p.get("enabled", True)],
            key=lambda p: p.get("priority", 99),
        )

    def get_tuner_providers(self) -> list[dict[str, Any]]:
        """Return enabled tuner providers sorted by priority."""
        data = self._load_providers()
        providers = data.get("tuner_providers", [])
        return sorted(
            [p for p in providers if p.get("enabled", True)],
            key=lambda p: p.get("priority", 99),
        )

    def check_provider_health(self, provider_id: str, country_code: str, url: str) -> bool:
        """Check if a provider URL is healthy, using cache if fresh."""
        data = self._load_providers()
        hc = data.get("health_check", {})
        interval = hc.get("interval_hours", 24) * 3600
        timeout = hc.get("timeout_seconds", 10)

        cache = self._load_health_cache()
        cache_key = f"{provider_id}:{country_code}"
        entry = cache.get(cache_key, {})
        last_check = entry.get("ts", 0)

        if time.time() - last_check < interval:
            return entry.get("ok", False)

        ok = self._probe_url(url, timeout=timeout)
        cache[cache_key] = {"ts": time.time(), "ok": ok, "url": url}
        self._save_health_cache(cache)
        return ok

    def resolve_guide_url(self, country_code: str, log: Any = None) -> str:
        """Find the best working guide URL for a country."""
        code = country_code.lower()
        providers = self.get_guide_providers()

        for provider in providers:
            url = self._expand_url(provider, code)
            if not url:
                continue
            pid = provider.get("id", "unknown")
            if self.check_provider_health(pid, code, url):
                if log:
                    log(f"[INFO] EPG: using {pid} for {code}: {url}")
                return url

        for provider in providers:
            url = self._expand_url(provider, code)
            if not url:
                continue
            pid = provider.get("id", "unknown")
            if self._probe_url(url, timeout=10):
                cache = self._load_health_cache()
                cache[f"{pid}:{code}"] = {"ts": time.time(), "ok": True, "url": url}
                self._save_health_cache(cache)
                if log:
                    log(f"[OK] EPG: found working provider {pid} for {code}: {url}")
                return url
            else:
                cache = self._load_health_cache()
                cache[f"{pid}:{code}"] = {"ts": time.time(), "ok": False, "url": url}
                self._save_health_cache(cache)
                if log:
                    log(f"[WARN] EPG: {pid} unavailable for {code}: {url}")

        if log:
            log(f"[WARN] EPG: no working guide provider found for {code}")
        return ""

    def resolve_tuner_url(self, country_code: str, log: Any = None) -> str:
        """Find the best working tuner URL for a country."""
        code = country_code.lower()
        for provider in self.get_tuner_providers():
            url = self._expand_url(provider, code)
            if url:
                return url
        return ""

    def run_health_check(self, log: Any = None) -> dict[str, Any]:
        """Probe all providers for all known countries. Returns health report."""
        from .api.services.config import get_iptv_countries
        countries_data = get_iptv_countries()
        countries = countries_data.get("countries", [])

        providers = self.get_guide_providers()
        results: dict[str, dict[str, bool]] = {}
        healthy = 0
        unhealthy = 0

        for country in countries:
            code = country.get("code", "")
            if not code:
                continue
            results[code] = {}
            for provider in providers:
                url = self._expand_url(provider, code)
                if not url:
                    continue
                pid = provider.get("id", "unknown")
                ok = self._probe_url(url, timeout=10)
                results[code][pid] = ok
                cache = self._load_health_cache()
                cache[f"{pid}:{code}"] = {"ts": time.time(), "ok": ok, "url": url}
                self._save_health_cache(cache)
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

    def invalidate_cache(self) -> None:
        """Clear cached provider data (forces reload from YAML)."""
        self._provider_cache = None
        self._health_cache = None


# ---------------------------------------------------------------------------
# Singleton + backward-compat module-level references
# ---------------------------------------------------------------------------

_instance = EpgProviderService()
get_guide_providers = _instance.get_guide_providers
get_tuner_providers = _instance.get_tuner_providers
check_provider_health = _instance.check_provider_health
resolve_guide_url = _instance.resolve_guide_url
resolve_tuner_url = _instance.resolve_tuner_url
run_health_check = _instance.run_health_check
invalidate_cache = _instance.invalidate_cache
_expand_url = _instance._expand_url
_load_providers = _instance._load_providers
_find_providers_yaml = _instance._find_providers_yaml
_health_cache_path = _instance._health_cache_path
_load_health_cache = _instance._load_health_cache
_probe_url = _instance._probe_url
_save_health_cache = _instance._save_health_cache
