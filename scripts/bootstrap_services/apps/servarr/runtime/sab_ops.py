#!/usr/bin/env python3
"""SABnzbd runtime operations."""

from __future__ import annotations

from .factory import _usenet_client_service


def _sab_service_cfg(sab_cfg):
    merged = dict(sab_cfg) if isinstance(sab_cfg, dict) else {}
    merged.setdefault("technology", "sabnzbd")
    return merged


def read_sabnzbd_api_key(config_root, sab_cfg):
    return _usenet_client_service(_sab_service_cfg(sab_cfg)).read_api_key(config_root, sab_cfg)


def sabnzbd_request(base_url, api_key, params, timeout=20):
    return _usenet_client_service({"technology": "sabnzbd"}).request(
        base_url=base_url,
        api_key=api_key,
        params=params,
        timeout=timeout,
    )


def sabnzbd_get_config_section(base_url, sab_api_key, section):
    return _usenet_client_service({"technology": "sabnzbd"}).get_config_section(
        base_url=base_url,
        sab_api_key=sab_api_key,
        section=section,
    )


def ensure_sabnzbd_defaults(sab_cfg, sab_api_key):
    _usenet_client_service(_sab_service_cfg(sab_cfg)).ensure_defaults(
        sab_cfg=sab_cfg,
        sab_api_key=sab_api_key,
    )


def ensure_sabnzbd_categories(arr_apps, sab_cfg, sab_api_key):
    _usenet_client_service(_sab_service_cfg(sab_cfg)).ensure_categories(
        arr_apps=arr_apps,
        sab_cfg=sab_cfg,
        sab_api_key=sab_api_key,
    )
