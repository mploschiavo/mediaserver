#!/usr/bin/env python3
"""qBittorrent runtime operations."""

from __future__ import annotations

from .arr_ops import choose_category
from .factory import _torrent_client_service


def _cfg_with_url_hint(cfg, url):
    merged = dict(cfg) if isinstance(cfg, dict) else {}
    merged.setdefault("technology", "qbittorrent")
    if str(url or "").strip() and not str(merged.get("url") or "").strip():
        merged["url"] = str(url).strip()
    return merged


def qbit_login(base_url, username, password):
    return _torrent_client_service(_cfg_with_url_hint({}, base_url)).login(
        base_url, username, password
    )


def torrent_client_login(base_url, username, password):
    return qbit_login(base_url, username, password)


def qbit_create_category(opener, base_url, category, save_path):
    return _torrent_client_service(_cfg_with_url_hint({}, base_url)).create_category(
        opener,
        base_url,
        category,
        save_path,
    )


def qbit_set_preferences(opener, base_url, preferences):
    return _torrent_client_service(_cfg_with_url_hint({}, base_url)).set_preferences(
        opener,
        base_url,
        preferences,
    )


def qbit_list_torrents(opener, base_url, filter_value="all"):
    return _torrent_client_service(_cfg_with_url_hint({}, base_url)).list_torrents(
        opener,
        base_url,
        filter_value=filter_value,
    )


def qbit_list_completed_torrents(opener, base_url):
    return _torrent_client_service(_cfg_with_url_hint({}, base_url)).list_completed_torrents(
        opener, base_url
    )


def qbit_delete_torrents(opener, base_url, hashes, delete_files=True):
    return _torrent_client_service(_cfg_with_url_hint({}, base_url)).delete_torrents(
        opener,
        base_url,
        hashes,
        delete_files=delete_files,
    )


def setup_qbit_storage_defaults(opener, qbit_url, qbit_cfg):
    return _torrent_client_service(_cfg_with_url_hint(qbit_cfg, qbit_url)).setup_storage_defaults(
        opener,
        qbit_url,
        qbit_cfg,
        set_preferences_fn=qbit_set_preferences,
    )


def setup_torrent_storage_defaults(opener, torrent_client_url, torrent_client_cfg):
    return setup_qbit_storage_defaults(opener, torrent_client_url, torrent_client_cfg)


def setup_qbit_categories(arr_apps, qbit_cfg, qb_username, qb_password):
    qbit_url = ""
    if isinstance(qbit_cfg, dict):
        qbit_url = str(qbit_cfg.get("url") or "")
    return _torrent_client_service(_cfg_with_url_hint(qbit_cfg, qbit_url)).setup_categories(
        arr_apps,
        qbit_cfg,
        qb_username,
        qb_password,
        choose_category_fn=choose_category,
        setup_storage_defaults_fn=setup_qbit_storage_defaults,
        create_category_fn=qbit_create_category,
        login_fn=qbit_login,
    )


def setup_torrent_categories(
    arr_apps,
    torrent_client_cfg,
    torrent_client_username,
    torrent_client_password,
):
    return setup_qbit_categories(
        arr_apps,
        torrent_client_cfg,
        torrent_client_username,
        torrent_client_password,
    )
