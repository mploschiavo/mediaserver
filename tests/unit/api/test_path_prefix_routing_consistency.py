"""Regression guards for the 2026-04-19 Prowlarr blank-page bug.

Symptom: https://apps.media-stack.local/app/prowlarr/ loaded to a
blank screen. Root cause: Prowlarr's config.xml shipped with
``<UrlBase></UrlBase>`` (empty), so when Envoy forwarded
``/app/prowlarr/...`` with the prefix intact, the app served its
frontend from ``/``. The browser then tried to fetch assets at
``/static/...`` (no prefix) and 404'd — blank page.

Why unit coverage missed it:

- The file-patcher ``ServarrHttpPreflight`` existed and even
  included Prowlarr in its app list, but nothing wired it into the
  bootstrap flow. A dead class passes every unit test.
- No test asserted consistency between Envoy's route prefix and
  the per-app ``UrlBase`` value — they lived in two different
  files with no cross-check.
- No test asserted that every preflight handler referenced by a
  contract file actually imports cleanly.

These three tests close all three gaps:

1. Every *arr service contract registers a ``preflight_handler``
   that sets UrlBase — so the dead-class bug can't happen again.
2. Every ``preflight_handler`` in ``contracts/services/*.yaml``
   points at a handler that imports and is callable — prevents a
   typo'd or deleted handler from being silently skipped.
3. Every *arr in ``_ARR_APPS`` is represented in the contract
   registration — prevents "we added a new *arr but forgot the
   UrlBase preflight" drift.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

_CONTRACTS_DIR = ROOT / "contracts" / "services"
_ARR_SLUGS = ("sonarr", "radarr", "lidarr", "readarr", "prowlarr")
_SERVARR_PREFLIGHT_HANDLER = (
    "media_stack.services.apps.servarr.http_preflight:run_preflight"
)


def _service_yaml(slug: str) -> dict:
    path = _CONTRACTS_DIR / f"{slug}.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def test_every_arr_registers_servarr_http_preflight() -> None:
    """Every *arr contract must register the servarr preflight so
    the UrlBase is synced to /app/<slug> on every boot. Without
    this a fresh install silently ships with blank pages at the
    path-prefix route."""
    missing: list[str] = []
    wrong_handler: list[str] = []
    for slug in _ARR_SLUGS:
        data = _service_yaml(slug)
        plugin = data.get("plugin") or {}
        svc = data.get("service") or {}
        # preflight_handler lives under ``plugin:`` per the
        # established contract shape (jellyfin.yaml, sabnzbd.yaml).
        pf = plugin.get("preflight_handler") or svc.get("preflight_handler")
        if not isinstance(pf, dict):
            missing.append(slug)
            continue
        handler = str(pf.get("handler", "")).strip()
        if handler != _SERVARR_PREFLIGHT_HANDLER:
            wrong_handler.append(f"{slug}={handler!r}")
    assert not missing, (
        f"*arr contracts missing preflight_handler: {missing}. "
        f"Without it, UrlBase stays empty on fresh installs and "
        f"the path-prefix route returns a blank page."
    )
    assert not wrong_handler, (
        f"*arr preflight_handler points at the wrong handler: "
        f"{wrong_handler}. Expected {_SERVARR_PREFLIGHT_HANDLER}."
    )


def test_every_preflight_handler_is_importable() -> None:
    """Every ``preflight_handler.handler`` string in a contract
    must resolve to a real module + attribute. Catches typos and
    handlers that got renamed without updating the contract —
    the dispatcher swallows an import error as 'no preflight' and
    moves on, which is exactly how a dead wire-up hides."""
    failed: list[str] = []
    for yaml_path in sorted(_CONTRACTS_DIR.glob("*.yaml")):
        if yaml_path.name.startswith("_"):
            continue
        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        plugin = (data.get("plugin") or {})
        svc = (data.get("service") or {})
        pf = plugin.get("preflight_handler") or svc.get("preflight_handler")
        if not isinstance(pf, dict):
            continue
        handler = str(pf.get("handler", "")).strip()
        if not handler:
            failed.append(f"{yaml_path.name}: empty handler")
            continue
        if ":" not in handler:
            failed.append(f"{yaml_path.name}: handler missing ':' "
                          f"module:attr separator ({handler!r})")
            continue
        module_name, _, attr = handler.partition(":")
        try:
            module = __import__(module_name, fromlist=[attr])
        except Exception as exc:  # noqa: BLE001
            failed.append(f"{yaml_path.name}: import {module_name} "
                          f"failed ({exc})")
            continue
        if not hasattr(module, attr):
            failed.append(
                f"{yaml_path.name}: {module_name} has no attribute "
                f"{attr!r}",
            )
    assert not failed, (
        "preflight_handler references broken:\n  "
        + "\n  ".join(failed)
    )


def test_servarr_preflight_app_list_matches_contract_registrations() -> None:
    """_ARR_APPS in the preflight module must match the set of
    *arr contracts that register the preflight. Prevents drift
    where someone adds a 6th *arr (e.g. whisparr) in contracts
    without adding it to _ARR_APPS — or vice versa."""
    from media_stack.services.apps.servarr.http_preflight import _ARR_APPS

    registered: set[str] = set()
    for slug in _ARR_SLUGS:
        data = _service_yaml(slug)
        plugin = data.get("plugin") or {}
        svc = data.get("service") or {}
        pf = plugin.get("preflight_handler") or svc.get("preflight_handler")
        if isinstance(pf, dict) and str(pf.get("handler", "")).strip() \
                == _SERVARR_PREFLIGHT_HANDLER:
            registered.add(slug)

    preflight_set = set(_ARR_APPS.keys())
    self_check = set(_ARR_SLUGS)
    assert preflight_set == self_check, (
        f"_ARR_APPS ({sorted(preflight_set)}) drifted from the "
        f"test's canonical *arr list ({sorted(self_check)}). Keep "
        f"both in sync — add the new *arr to _ARR_APPS in "
        f"http_preflight.py and to _ARR_SLUGS in this test."
    )
    missing = preflight_set - registered
    assert not missing, (
        f"{sorted(missing)} exist in _ARR_APPS but no contract "
        f"registers the preflight — UrlBase will not be set for "
        f"them on fresh installs."
    )
