"""Download client registry helpers for the content API.

Maps service IDs to their download-protocol category so that platform
code can iterate download clients without hardcoding service names.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from media_stack.api.services.registry import ServiceDef

# Service ID -> download category.  Extend for new client types.
DOWNLOAD_CLIENT_CATEGORIES: dict[str, str] = {
    "qbittorrent": "torrent",
    "transmission": "torrent",
    "sabnzbd": "usenet",
    "nzbget": "usenet",
}


class DownloadClientRegistryHelpers:

    def get_download_client_service(self, category: str) -> "ServiceDef | None":
        """Look up the first configured download client service by category.

        Categories: "torrent", "usenet".  Returns the first service whose ID
        appears in DOWNLOAD_CLIENT_CATEGORIES with the given category and that
        has a host/port in the registry, or None.
        """
        from media_stack.api.services.registry import SERVICE_MAP

        for svc_id, cat in DOWNLOAD_CLIENT_CATEGORIES.items():
            if cat == category:
                svc = SERVICE_MAP.get(svc_id)
                if svc and svc.host and svc.port:
                    return svc
        return None

    def default_torrent_client_url(self) -> str:
        """Return the default URL for the active torrent download client.

        Reads host/port from the service registry.  Returns a sensible
        fallback if no torrent client is registered.
        """
        svc = self.get_download_client_service("torrent")
        if svc:
            return f"http://{svc.host}:{svc.port}"
        return "http://localhost:8080"


_instance = DownloadClientRegistryHelpers()
get_download_client_service = _instance.get_download_client_service
default_torrent_client_url = _instance.default_torrent_client_url
