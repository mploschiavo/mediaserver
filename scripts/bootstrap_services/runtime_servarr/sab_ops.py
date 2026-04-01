#!/usr/bin/env python3
"""SABnzbd runtime operations."""

from __future__ import annotations

from .factory import _sabnzbd_service


def read_sabnzbd_api_key(config_root, sab_cfg):
    return _sabnzbd_service().read_api_key(config_root, sab_cfg)


def sabnzbd_request(base_url, api_key, params, timeout=20):
    return _sabnzbd_service().request(
        base_url=base_url,
        api_key=api_key,
        params=params,
        timeout=timeout,
    )


def sabnzbd_get_config_section(base_url, sab_api_key, section):
    return _sabnzbd_service().get_config_section(
        base_url=base_url,
        sab_api_key=sab_api_key,
        section=section,
    )


def ensure_sabnzbd_defaults(sab_cfg, sab_api_key):
    _sabnzbd_service(sab_cfg if isinstance(sab_cfg, dict) else None).ensure_defaults(
        sab_cfg=sab_cfg,
        sab_api_key=sab_api_key,
    )


def ensure_sabnzbd_categories(arr_apps, sab_cfg, sab_api_key):
    _sabnzbd_service(sab_cfg if isinstance(sab_cfg, dict) else None).ensure_categories(
        arr_apps=arr_apps,
        sab_cfg=sab_cfg,
        sab_api_key=sab_api_key,
    )
