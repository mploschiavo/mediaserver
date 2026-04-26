"""Bazarr language-profile proxy for the dashboard's subtitle
preferences card.

Bazarr handles subtitle download / language selection via a "language
profiles" abstraction (one profile per group of titles, each profile
listing the desired languages). For most home operators a single
default profile is fine; the dashboard surfaces:

  * The list of available language codes Bazarr knows about
  * The current default profile + the languages it pins
  * A POST endpoint to overwrite that list

Why proxy rather than embed Bazarr's UI:
  * Bazarr's full language-profile UI has hearing-impaired toggles,
    forced-only flags, cutoff scoring, per-language priorities — the
    operator-survey "I want subs in Spanish" doesn't need any of
    that. Surfacing a single language list is the 80% answer.
  * Operators wanting the full schema deep-link to Bazarr's own UI.

The shape returned mirrors Bazarr's REST API minus fields the
dashboard doesn't render. Errors fall through with a 502 so the
ApiErrorTile picks up "Server error" cleanly.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from .registry import service_internal_url
from .api_keys import discover_api_keys


def _bazarr_get(path: str) -> Any:
    """GET against Bazarr's API; returns parsed JSON."""
    api_keys = discover_api_keys()
    key = api_keys.get("bazarr", "")
    if not key:
        raise RuntimeError(
            "BAZARR_API_KEY not discovered — run discover-api-keys job",
        )
    url = service_internal_url("bazarr") + path
    req = urllib.request.Request(url, headers={"X-Api-Key": key})
    with urllib.request.urlopen(req, timeout=8) as resp:
        return json.loads(resp.read())


def _bazarr_post(path: str, body: dict[str, Any]) -> Any:
    api_keys = discover_api_keys()
    key = api_keys.get("bazarr", "")
    if not key:
        raise RuntimeError(
            "BAZARR_API_KEY not discovered — run discover-api-keys job",
        )
    url = service_internal_url("bazarr") + path
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "X-Api-Key": key,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=8) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else {"status": "ok"}


def _bazarr_patch(path: str, body: dict[str, Any]) -> Any:
    """PATCH against Bazarr's API; some endpoints require it."""
    api_keys = discover_api_keys()
    key = api_keys.get("bazarr", "")
    if not key:
        raise RuntimeError(
            "BAZARR_API_KEY not discovered — run discover-api-keys job",
        )
    url = service_internal_url("bazarr") + path
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "X-Api-Key": key,
            "Content-Type": "application/json",
        },
        method="PATCH",
    )
    with urllib.request.urlopen(req, timeout=8) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else {"status": "ok"}


def get_subtitle_config() -> dict[str, Any]:
    """Aggregate Bazarr's subtitle-related config into a single
    dashboard-friendly shape.

    Returned keys:
      * ``available_languages`` — list of Bazarr-known langs as
        ``{code, name}`` dicts.
      * ``profiles`` — list of language profiles with their pinned
        language list.
      * ``default_profile_id`` — operator-facing default (when set).
    """
    out: dict[str, Any] = {
        "available_languages": [],
        "profiles": [],
        "default_profile_id": None,
        "errors": [],
    }
    try:
        langs = _bazarr_get("/api/system/languages")
        # Bazarr returns ``[{code2, name, enabled}, ...]``. Filter to
        # enabled-only and normalise the keys we care about.
        if isinstance(langs, list):
            out["available_languages"] = [
                {
                    "code": str(l.get("code2") or l.get("code")),
                    "name": str(l.get("name") or ""),
                    "enabled": bool(l.get("enabled", True)),
                }
                for l in langs
                if isinstance(l, dict)
            ]
    except (urllib.error.URLError, urllib.error.HTTPError, RuntimeError) as exc:
        out["errors"].append(f"languages: {str(exc)[:120]}")

    try:
        profiles = _bazarr_get("/api/system/languages/profiles")
        if isinstance(profiles, list):
            out["profiles"] = [
                {
                    "id": p.get("profileId") or p.get("id"),
                    "name": str(p.get("name") or ""),
                    "items": [
                        {
                            "code": str(it.get("language") or ""),
                            "forced": bool(it.get("forced", False)),
                            "hi": bool(it.get("hi", False)),
                            "audio_exclude": bool(it.get("audio_exclude", False)),
                        }
                        for it in (p.get("items") or [])
                        if isinstance(it, dict)
                    ],
                }
                for p in profiles
                if isinstance(p, dict)
            ]
            # Default profile id — Bazarr exposes this on the
            # general settings; many deploys leave it unset and pick
            # per-series. Best-effort lookup.
            try:
                settings = _bazarr_get("/api/system/settings")
                if isinstance(settings, dict):
                    general = (settings.get("general") or {})
                    default_id = (
                        general.get("default_und_audio_lang")
                        or general.get("default_profile_id")
                    )
                    out["default_profile_id"] = default_id
            except Exception:  # noqa: BLE001
                pass
    except (urllib.error.URLError, urllib.error.HTTPError, RuntimeError) as exc:
        out["errors"].append(f"profiles: {str(exc)[:120]}")

    return out


def update_subtitle_languages(
    profile_id: int | str,
    language_codes: list[str],
    *,
    forced: bool = False,
    hi: bool = False,
) -> dict[str, Any]:
    """Overwrite the language list on the given profile.

    The simple home-use case is "I want English + Spanish subs"; we
    map that into Bazarr's items array (one item per language) with
    sensible defaults (forced=False, hi=False). Operators wanting
    per-language flags use Bazarr's UI.
    """
    if not isinstance(language_codes, list) or not language_codes:
        return {"error": "language_codes must be a non-empty list"}
    items = [
        {
            "language": str(code).strip(),
            "forced": bool(forced),
            "hi": bool(hi),
            "audio_exclude": False,
        }
        for code in language_codes
        if str(code).strip()
    ]
    body = {"items": items}
    try:
        result = _bazarr_patch(
            f"/api/system/languages/profiles/{profile_id}", body,
        )
        return {"status": "ok", "profile_id": profile_id, "result": result}
    except urllib.error.HTTPError as exc:
        return {"error": f"Bazarr returned {exc.code}: {str(exc)[:120]}"}
    except (urllib.error.URLError, RuntimeError) as exc:
        return {"error": str(exc)[:200]}
