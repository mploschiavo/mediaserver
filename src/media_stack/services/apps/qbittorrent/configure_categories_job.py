"""Download categories job handler.

Sets up torrent and usenet download categories across all
download clients that support them.

Registered in contracts/services/qbittorrent.yaml as:
  configure-categories:
    handler: media_stack.services.apps.qbittorrent.configure_categories_job:configure_categories
"""

from __future__ import annotations

import importlib
from typing import Any

import media_stack.services.runtime_platform as runtime_platform


def configure_categories(ctx: Any) -> dict[str, Any]:
    """Set up download categories in torrent and usenet clients."""
    results = []
    from media_stack.api.services.registry import SERVICES
    for svc in SERVICES:
        try:
            mod = importlib.import_module(f"media_stack.services.apps.{svc.id}.runtime_ops")
            fn = getattr(mod, "setup_torrent_categories", None) or getattr(mod, "ensure_sabnzbd_categories", None)
            if fn:
                fn(ctx.cfg, ctx.config_root, ctx.wait_timeout)
                results.append(svc.id)
        except (ImportError, AttributeError):
            continue
        except Exception as exc:
            runtime_platform.log(f"[WARN] {svc.id} categories: {exc}")
    return {"configured": results}
