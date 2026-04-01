#!/usr/bin/env python3
"""qBittorrent runtime operations."""

from __future__ import annotations

from .arr_ops import choose_category
from .factory import _qbit_service


def qbit_login(base_url, username, password):
    return _qbit_service().login(base_url, username, password)


def qbit_create_category(opener, base_url, category, save_path):
    return _qbit_service().create_category(opener, base_url, category, save_path)


def qbit_set_preferences(opener, base_url, preferences):
    return _qbit_service().set_preferences(opener, base_url, preferences)


def setup_qbit_storage_defaults(opener, qbit_url, qbit_cfg):
    return _qbit_service(qbit_cfg if isinstance(qbit_cfg, dict) else None).setup_storage_defaults(
        opener,
        qbit_url,
        qbit_cfg,
        set_preferences_fn=qbit_set_preferences,
    )


def setup_qbit_categories(arr_apps, qbit_cfg, qb_username, qb_password):
    return _qbit_service(qbit_cfg if isinstance(qbit_cfg, dict) else None).setup_categories(
        arr_apps,
        qbit_cfg,
        qb_username,
        qb_password,
        choose_category_fn=choose_category,
        setup_storage_defaults_fn=setup_qbit_storage_defaults,
        create_category_fn=qbit_create_category,
        login_fn=qbit_login,
    )
