"""Bazarr configuration wiring for the language-profile family.

Lifecycle-method port of the legacy ``ensure_bazarr_language_profile``
job handler (ADR-0005 Phase 3 cutover). The class lives here rather
than inline in ``bazarr/lifecycle.py`` so the lifecycle module stays
focused on the core ``ServiceLifecycle`` Protocol surface
(probe_running / probe_has_api_key / mint_api_key / persist_api_key /
discover_api_key).

``BazarrConfigWirer`` owns:

  * The HTTP shape of Bazarr's settings + profiles APIs (GET
    ``/api/system/settings`` + ``/api/system/languages/profiles``,
    form-encoded POST to ``/api/system/settings``)
  * The form-encoded payload structure (``settings-general-*`` keys,
    repeated ``languages-enabled`` / ``settings-general-enabled_providers``
    pairs, JSON-encoded ``languages-profiles`` blob)
  * The curated provider set (opensubtitlescom / podnapisi / gestdown
    / yifysubtitles / embeddedsubtitles)
  * The cross-service *arr-integration block (use_sonarr / use_radarr +
    per-arr ip / port / base_url / apikey / ssl)
  * The Jellyfin Bazarr-plugin XML config writer (filename pinned to
    ``Jellyfin.Plugin.Bazarr.xml`` — the assembly name; writing to
    ``Bazarr.xml`` silently does nothing per the v1.0.146 finding)
  * Idempotent skip logic for the language-profile creation (preserves
    operator-customised profile id) — defaults + provider list +
    arr integration are always re-asserted (so drift gets corrected
    on every run)

Why one ensurer for five promises?
==================================

The legacy ``ensure_bazarr_language_profile`` does all five things in
one form-encoded POST plus one file write. Splitting into five
ensurers would mean five redundant POSTs that each clobber the
shared settings document. So the family shares a single ``ensure``
method and the five promises differ ONLY in their per-promise probe
(each asserting its own slice of the settings response). Choice (a)
in ADR-0005 Phase 3 lingo.

Cross-service Sonarr/Radarr api keys
====================================

The ``arr-integration`` block needs Sonarr/Radarr's api keys (Bazarr
auths to the *arr REST API to poll series/movies). These flow in via
``ctx.secrets`` — the orchestrator resolves them from
``SONARR_API_KEY`` / ``RADARR_API_KEY`` env vars before calling the
ensurer. If they're missing, the ensurer still POSTs the rest of the
config and reports the missing services in evidence so the operator
can see which integration didn't land.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Mapping

from media_stack.adapters._shared.lifecycle_wirer_base import (
    LifecycleWirerBase,
)
from media_stack.domain.services import (
    OrchestrationContext,
    Outcome,
    ProbeResult,
)


logger = logging.getLogger(__name__)


# --- HTTP timing -----------------------------------------------------

_BAZARR_HTTP_GET_TIMEOUT_SECONDS = 10
_BAZARR_HTTP_POST_TIMEOUT_SECONDS = 15

# --- Settings + Profiles paths --------------------------------------

_PROFILES_PATH = "/api/system/languages/profiles"
_SETTINGS_PATH = "/api/system/settings"

# --- Default English profile shape ----------------------------------

_DEFAULT_PROFILE_NAME = "English"
_DEFAULT_PROFILE_LANGUAGE = "en"
_DEFAULT_PROFILE_ID = 1

# --- Curated provider set (legacy ensurer choice) -------------------
#
# Replaces the OOTB Bazarr provider list with this hand-picked set:
#
#   opensubtitlescom   — broad: movies + TV
#   podnapisi          — broad: secondary
#   gestdown           — TV (modern Addic7ed replacement; Addic7ed
#                        has anti-scrape issues so we drop it)
#   yifysubtitles      — movies (pairs with YTS releases)
#   embeddedsubtitles  — zero-network: extracts existing .mkv tracks
#
# Order matters for the form-encoded POST (Bazarr preserves it).
_CURATED_PROVIDERS: tuple[str, ...] = (
    "opensubtitlescom",
    "podnapisi",
    "gestdown",
    "yifysubtitles",
    "embeddedsubtitles",
)

# --- *arr integration table -----------------------------------------
#
# Per-arr ``(port, base_url)``. Bazarr defaults ``ip`` to
# ``127.0.0.1`` and apikey to empty on a fresh install — without this,
# the UI shows "Use Sonarr / Radarr — not configured" and Bazarr never
# polls so no subtitles get fetched. Hostname is the docker DNS name
# (also the contract YAML host); base_url matches the ``/app/<svc>/``
# preflight prefix.
_ARR_INTEGRATION_TABLE: tuple[tuple[str, int, str], ...] = (
    ("sonarr", 8989, "/app/sonarr"),
    ("radarr", 7878, "/app/radarr"),
)

# --- Jellyfin Bazarr-plugin config XML -------------------------------
#
# Plugin: enoch85/bazarr-jellyfin (GPLv3). The plugin config lives at
# ``<jellyfin_config>/plugins/configurations/Jellyfin.Plugin.Bazarr.xml``
# (NOT ``Bazarr.xml`` — Jellyfin names config files by the assembly
# name; the wrong filename silently does nothing per the v1.0.146
# finding). Jellyfin reads it on plugin load so writing pre-install
# is safe.
_JELLYFIN_PLUGIN_CONFIG_DIRECTORY = Path("jellyfin/plugins/configurations")
_JELLYFIN_PLUGIN_CONFIG_FILENAME = "Jellyfin.Plugin.Bazarr.xml"
_JELLYFIN_PLUGIN_STRAY_FILENAME = "Bazarr.xml"  # cleaned up on the way through
_JELLYFIN_PLUGIN_SEARCH_TIMEOUT_SECONDS = 25

_DEFAULT_CONFIG_ROOT = "/srv-config"
_CONFIG_ROOT_ENV = "CONFIG_ROOT"


class BazarrConfigWirer(LifecycleWirerBase):
    """Bazarr settings + profile + plugin-XML wiring.

    Stateless beyond the constructor-injected curated provider list,
    *arr integration table, and plugin config path. Per-call
    parameterized by the orchestration context.
    """

    def __init__(
        self,
        *,
        curated_providers: tuple[str, ...] = _CURATED_PROVIDERS,
        arr_integration_table: tuple[tuple[str, int, str], ...] = _ARR_INTEGRATION_TABLE,
        plugin_config_dir: Path = _JELLYFIN_PLUGIN_CONFIG_DIRECTORY,
        plugin_config_filename: str = _JELLYFIN_PLUGIN_CONFIG_FILENAME,
        plugin_stray_filename: str = _JELLYFIN_PLUGIN_STRAY_FILENAME,
    ) -> None:
        self._curated_providers = curated_providers
        self._arr_integration_table = arr_integration_table
        self._plugin_config_dir = plugin_config_dir
        self._plugin_config_filename = plugin_config_filename
        self._plugin_stray_filename = plugin_stray_filename

    # === Probes =========================================================
    #
    # Five probes, one per promise. Each asserts a different slice of
    # Bazarr's REST surface. They share the same prereq logic
    # (host/port/api-key) — extracted into ``_prereq_url_and_key``.

    def probe_language_profile(
        self, api_key: str | None, ctx: OrchestrationContext,
    ) -> ProbeResult:
        url, ok_or_unknown = self._prereq_url_and_key(api_key, ctx)
        if ok_or_unknown is not None:
            return ok_or_unknown
        body = self._http_get_json(f"{url}{_PROFILES_PATH}", api_key or "")
        if body is None:
            return ProbeResult.unknown(
                f"could not list profiles at {url}{_PROFILES_PATH}",
                evidence={"url": f"{url}{_PROFILES_PATH}"},
                evaluated_at=ctx.now(),
            )
        if isinstance(body, list) and body:
            return ProbeResult.ok(
                f"language profile present (count={len(body)})",
                evidence={"profile_count": len(body)},
                evaluated_at=ctx.now(),
            )
        return ProbeResult.failed(
            "no language profile configured",
            evidence={"profile_count": 0},
            evaluated_at=ctx.now(),
        )

    def probe_default_profile_toggles(
        self, api_key: str | None, ctx: OrchestrationContext,
    ) -> ProbeResult:
        return self._probe_settings_field(
            api_key, ctx,
            extract=self._extract_default_toggles,
            ok_detail="default-profile auto-assignment enabled",
            fail_detail="default-profile toggle off for series and/or movies",
        )

    def probe_providers(
        self, api_key: str | None, ctx: OrchestrationContext,
    ) -> ProbeResult:
        return self._probe_settings_field(
            api_key, ctx,
            extract=self._extract_providers_match,
            ok_detail="curated provider set enabled",
            fail_detail="curated provider set incomplete",
        )

    def probe_arr_integration(
        self, api_key: str | None, ctx: OrchestrationContext,
    ) -> ProbeResult:
        return self._probe_settings_field(
            api_key, ctx,
            extract=self._extract_arr_integration_match,
            ok_detail="sonarr+radarr integration configured",
            fail_detail="sonarr/radarr integration not fully configured",
        )

    def probe_jellyfin_plugin_config(
        self, ctx: OrchestrationContext,
    ) -> ProbeResult:
        path = self._plugin_xml_path(ctx)
        if not path.is_file():
            return ProbeResult.failed(
                f"plugin XML not present at {path}",
                evidence={"path": str(path)},
                evaluated_at=ctx.now(),
            )
        try:
            data = path.read_text(encoding="utf-8")
        except OSError as exc:
            return ProbeResult.unknown(
                f"plugin XML unreadable: {exc}",
                evidence={"path": str(path), "error": str(exc)},
                evaluated_at=ctx.now(),
            )
        if "<BazarrUrl>http://bazarr:6767</BazarrUrl>" in data and (
            "<BazarrApiKey>" in data
            and "<BazarrApiKey></BazarrApiKey>" not in data
        ):
            return ProbeResult.ok(
                "plugin XML has expected URL + non-empty api key",
                evidence={"path": str(path)},
                evaluated_at=ctx.now(),
            )
        return ProbeResult.failed(
            "plugin XML missing expected URL or api-key tag",
            evidence={"path": str(path)},
            evaluated_at=ctx.now(),
        )

    # === Ensurer =======================================================
    #
    # One ensurer for all five promises (rationale at module top).
    # Always re-asserts defaults + providers + arr integration so
    # operator-side drift is corrected on every reconcile. Profile
    # creation is the only idempotent skip (preserves operator-
    # customised name/cutoff/items).

    def ensure(
        self,
        api_key: str | None,
        ctx: OrchestrationContext,
    ) -> Outcome[None]:
        url, prereq_outcome = self._ensure_prereq(api_key, ctx)
        if prereq_outcome is not None:
            return prereq_outcome
        # We're here, so api_key is non-empty.
        assert api_key
        existing = self._http_get_json(f"{url}{_PROFILES_PATH}", api_key)
        if existing is None:
            return Outcome.failure(
                f"profile list failed at {url}{_PROFILES_PATH}",
                transient=True,
                evidence={"url": f"{url}{_PROFILES_PATH}"},
            )
        profile_id, profile_action = self._resolve_profile_id(existing)
        form_pairs = self._build_form_pairs(profile_id, ctx.secrets)
        integrations_added = [
            arr for (arr, _, _) in self._arr_integration_table
            if (ctx.secrets.get(f"{arr.upper()}_API_KEY") or "").strip()
        ]
        post_outcome = self._post_settings(url, api_key, form_pairs)
        if not post_outcome.ok:
            return post_outcome
        plugin_written = self._write_jellyfin_plugin_xml(url, api_key, ctx)
        return Outcome.success(
            None,
            evidence={
                "url": url,
                "profile": (
                    f"{_DEFAULT_PROFILE_NAME} ({_DEFAULT_PROFILE_LANGUAGE}) "
                    f"— {profile_action} (id={profile_id})"
                ),
                "providers": list(self._curated_providers),
                "arr_integrations": integrations_added,
                "jellyfin_plugin_config": (
                    "written" if plugin_written else "skipped"
                ),
            },
        )

    # === Probe helpers ================================================

    def _probe_settings_field(
        self,
        api_key: str | None,
        ctx: OrchestrationContext,
        *,
        extract: Any,
        ok_detail: str,
        fail_detail: str,
    ) -> ProbeResult:
        url, ok_or_unknown = self._prereq_url_and_key(api_key, ctx)
        if ok_or_unknown is not None:
            return ok_or_unknown
        body = self._http_get_json(f"{url}{_SETTINGS_PATH}", api_key or "")
        if not isinstance(body, dict):
            return ProbeResult.unknown(
                f"could not load settings at {url}{_SETTINGS_PATH}",
                evidence={"url": f"{url}{_SETTINGS_PATH}"},
                evaluated_at=ctx.now(),
            )
        outcome = extract(body)
        if outcome["ok"]:
            return ProbeResult.ok(
                ok_detail,
                evidence=outcome["evidence"],
                evaluated_at=ctx.now(),
            )
        return ProbeResult.failed(
            fail_detail,
            evidence=outcome["evidence"],
            evaluated_at=ctx.now(),
        )

    def _extract_default_toggles(self, body: Mapping[str, Any]) -> dict:
        general = body.get("general") or {}
        if not isinstance(general, dict):
            return {"ok": False, "evidence": {"reason": "no_general_block"}}
        serie_on = bool(general.get("serie_default_enabled"))
        movie_on = bool(general.get("movie_default_enabled"))
        return {
            "ok": serie_on and movie_on,
            "evidence": {
                "serie_default_enabled": serie_on,
                "movie_default_enabled": movie_on,
            },
        }

    def _extract_providers_match(self, body: Mapping[str, Any]) -> dict:
        general = body.get("general") or {}
        enabled = general.get("enabled_providers") if isinstance(general, dict) else None
        enabled_set = set(enabled or [])
        required = set(self._curated_providers)
        return {
            "ok": required.issubset(enabled_set),
            "evidence": {
                "enabled_providers": sorted(enabled_set),
                "missing": sorted(required - enabled_set),
            },
        }

    def _extract_arr_integration_match(self, body: Mapping[str, Any]) -> dict:
        general = body.get("general") or {}
        if not isinstance(general, dict):
            return {"ok": False, "evidence": {"reason": "no_general_block"}}
        ok = True
        evidence: dict[str, Any] = {}
        for arr, _, _ in self._arr_integration_table:
            arr_block = body.get(arr) or {}
            if not isinstance(arr_block, dict):
                arr_block = {}
            use_flag = bool(general.get(f"use_{arr}"))
            ip_match = arr_block.get("ip") == arr
            apikey_present = bool(arr_block.get("apikey"))
            evidence[arr] = {
                "use_flag": use_flag,
                "ip_match": ip_match,
                "apikey_present": apikey_present,
            }
            if not (use_flag and ip_match and apikey_present):
                ok = False
        return {"ok": ok, "evidence": evidence}

    # === Ensurer helpers ==============================================

    def _ensure_prereq(
        self, api_key: str | None, ctx: OrchestrationContext,
    ) -> tuple[str, Outcome[None] | None]:
        url = self._bazarr_base_url(ctx)
        if not url:
            return "", Outcome.failure(
                "no host/port in config — cannot ensure",
                transient=False,
                evidence={"config_keys": sorted(ctx.config.keys())},
            )
        if not api_key:
            return url, Outcome.failure(
                "no bazarr api key — orchestrator will retry "
                "after probe_has_api_key reaches ok",
                transient=True,
                evidence={"url": url},
            )
        return url, None

    def _resolve_profile_id(
        self, existing: list[Any],
    ) -> tuple[int, str]:
        """Return ``(profile_id, action)``. ``action`` is ``"created"``
        when no profile exists yet, ``"skipped"`` when the operator
        already has one (we preserve their choice)."""
        if existing:
            first = existing[0]
            if isinstance(first, dict) and first.get("profileId") is not None:
                return int(first["profileId"]), "skipped"
            return _DEFAULT_PROFILE_ID, "skipped"
        return _DEFAULT_PROFILE_ID, "created"

    def _build_form_pairs(
        self,
        profile_id: int,
        secrets: Mapping[str, str],
    ) -> list[tuple[str, str]]:
        profile_payload = {
            "profileId": profile_id,
            "name": _DEFAULT_PROFILE_NAME,
            "items": [{
                "id": 1, "language": _DEFAULT_PROFILE_LANGUAGE,
                "audio_exclude": "False", "hi": "False", "forced": "False",
            }],
            "cutoff": None,
            "originalFormat": None,
            "mustContain": [],
            "mustNotContain": [],
            "tag": None,
        }
        form_pairs: list[tuple[str, str]] = [
            ("languages-enabled", _DEFAULT_PROFILE_LANGUAGE),
            ("languages-profiles", json.dumps([profile_payload])),
            ("settings-general-serie_default_enabled", "true"),
            ("settings-general-serie_default_profile", str(profile_id)),
            ("settings-general-movie_default_enabled", "true"),
            ("settings-general-movie_default_profile", str(profile_id)),
        ]
        for provider in self._curated_providers:
            form_pairs.append(("settings-general-enabled_providers", provider))
        for arr, port, base_url in self._arr_integration_table:
            arr_key = (secrets.get(f"{arr.upper()}_API_KEY") or "").strip()
            if not arr_key:
                continue
            form_pairs.extend([
                (f"settings-general-use_{arr}", "true"),
                (f"settings-{arr}-ip", arr),
                (f"settings-{arr}-port", str(port)),
                (f"settings-{arr}-base_url", base_url),
                (f"settings-{arr}-apikey", arr_key),
                (f"settings-{arr}-ssl", "false"),
            ])
        return form_pairs

    def _post_settings(
        self,
        url: str,
        api_key: str,
        form_pairs: list[tuple[str, str]],
    ) -> Outcome[None]:
        body = urllib.parse.urlencode(form_pairs).encode()
        endpoint = f"{url}{_SETTINGS_PATH}"
        try:
            req = urllib.request.Request(
                endpoint,
                data=body,
                method="POST",
                headers={
                    "X-Api-Key": api_key,
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )
            with urllib.request.urlopen(
                req, timeout=_BAZARR_HTTP_POST_TIMEOUT_SECONDS,
            ) as resp:
                return Outcome.success(
                    None,
                    evidence={"http_status": resp.status, "url": endpoint},
                )
        except urllib.error.HTTPError as exc:
            return Outcome.failure(
                f"settings POST failed (HTTP {exc.code})",
                transient=False,
                evidence={"http_status": exc.code, "url": endpoint},
            )
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            return Outcome.failure(
                f"unreachable at {endpoint}: {exc}",
                transient=True,
                evidence={"url": endpoint, "error": str(exc)},
            )

    def _write_jellyfin_plugin_xml(
        self, url: str, api_key: str, ctx: OrchestrationContext,
    ) -> bool:
        """Write the Bazarr-jellyfin plugin XML config. Non-fatal on
        failure — the plugin still works, the user can fill in the
        fields manually via Jellyfin → Dashboard → Plugins → Bazarr."""
        try:
            config_dir = self._plugin_xml_dir(ctx)
            config_dir.mkdir(parents=True, exist_ok=True)
            xml = self._render_plugin_xml(url, api_key)
            (config_dir / self._plugin_config_filename).write_text(
                xml, encoding="utf-8",
            )
            stray = config_dir / self._plugin_stray_filename
            if stray.exists():
                stray.unlink()
            return True
        except OSError as exc:
            logger.debug(
                "bazarr plugin XML write failed: %s", exc,
            )
            return False

    def _render_plugin_xml(self, url: str, api_key: str) -> str:
        return (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<PluginConfiguration xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
            'xmlns:xsd="http://www.w3.org/2001/XMLSchema">\n'
            f'  <BazarrUrl>{url.rstrip("/")}</BazarrUrl>\n'
            f'  <BazarrApiKey>{api_key}</BazarrApiKey>\n'
            '  <EnableForMovies>true</EnableForMovies>\n'
            '  <EnableForEpisodes>true</EnableForEpisodes>\n'
            f'  <SearchTimeoutSeconds>{_JELLYFIN_PLUGIN_SEARCH_TIMEOUT_SECONDS}</SearchTimeoutSeconds>\n'
            '</PluginConfiguration>\n'
        )

    # === Shared helpers ===============================================

    def _prereq_url_and_key(
        self, api_key: str | None, ctx: OrchestrationContext,
    ) -> tuple[str, ProbeResult | None]:
        url = self._bazarr_base_url(ctx)
        if not url:
            return "", ProbeResult.unknown(
                "no host/port in config — cannot probe",
                evidence={"config_keys": sorted(ctx.config.keys())},
                evaluated_at=ctx.now(),
            )
        if not api_key:
            return url, ProbeResult.unknown(
                "no bazarr api key — cannot probe",
                evidence={"url": url},
                evaluated_at=ctx.now(),
            )
        return url, None

    def _bazarr_base_url(self, ctx: OrchestrationContext) -> str:
        host = (ctx.config.get("host") or "").strip()
        port = ctx.config.get("port")
        if not host or not port:
            return ""
        scheme = (ctx.config.get("scheme") or "http").strip()
        return f"{scheme}://{host}:{port}"

    def _http_get_json(self, url: str, api_key: str) -> Any:
        try:
            req = urllib.request.Request(
                url, headers={"X-Api-Key": api_key},
            )
            with urllib.request.urlopen(
                req, timeout=_BAZARR_HTTP_GET_TIMEOUT_SECONDS,
            ) as resp:
                raw = resp.read()
        except (
            urllib.error.HTTPError, urllib.error.URLError,
            OSError, TimeoutError,
        ):
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    def _plugin_xml_dir(self, ctx: OrchestrationContext) -> Path:
        return Path(self._config_root(ctx)) / self._plugin_config_dir

    def _plugin_xml_path(self, ctx: OrchestrationContext) -> Path:
        return self._plugin_xml_dir(ctx) / self._plugin_config_filename

    def _config_root(self, ctx: OrchestrationContext) -> str:
        return (
            (ctx.config.get("config_root") or "").strip()
            or (ctx.extra.get("config_root") or "").strip()
            or os.environ.get(_CONFIG_ROOT_ENV, "").strip()
            or _DEFAULT_CONFIG_ROOT
        )


__all__ = ["BazarrConfigWirer"]
