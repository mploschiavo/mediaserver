"""Backward-compatible shim -- canonical home is services.apps.sabnzbd.download_client_adapter."""

from media_stack.services.apps.sabnzbd.download_client_adapter import (  # noqa: F401
    SabnzbdDownloadClientAdapter,
)

__all__ = ["SabnzbdDownloadClientAdapter"]
