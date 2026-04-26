"""qBittorrent bootstrap adapter."""

from __future__ import annotations

from media_stack.services.enums import RunnerEvent
from media_stack.services.download_client_adapters.base import DownloadClientAdapterBase
from media_stack.api.services.registry import service_internal_url


class QbittorrentDownloadClientAdapter(DownloadClientAdapterBase):
    """Handles qBittorrent login and category bootstrap."""

    def is_enabled(self) -> bool:
        return bool(self.context.configure_arr_clients or self.context.set_categories)

    def precheck(self) -> None:
        if not self.is_enabled():
            return
        self.deps.wait_for_service(
            "qBittorrent",
            self.context.url or self.deps.normalize_url(service_internal_url("qbittorrent")),
            "/",
            self.context.wait_timeout,
        )

    def prepare(self) -> None:
        if not self.is_enabled():
            self.context.status["login_ok"] = False
            return
        try:
            self.deps.invoke_handler(
                RunnerEvent.ACQUIRE,
                "torrent_client_login",
                self.context.url or self.deps.normalize_url(service_internal_url("qbittorrent")),
                self.context.username,
                self.context.password,
            )
            self.deps.log("[OK] qBittorrent: authenticated for bootstrap automation")
            self.context.status["login_ok"] = True
        except Exception as exc:
            if self.context.login_required:
                raise RuntimeError(
                    "qBittorrent login failed with secret credentials. "
                    "Update STACK_ADMIN_USERNAME/STACK_ADMIN_PASSWORD."
                ) from exc
            self.deps.log(
                "[WARN] qBittorrent login failed. "
                "Continuing because torrent-client login is not required in config "
                "(set download_clients.qbittorrent.login_required=true to fail hard)."
            )
            self.context.status["login_ok"] = False

    def configure(self) -> None:
        if not self.context.set_categories:
            return
        if not bool(self.context.status.get("login_ok", False)):
            return
        self.deps.invoke_handler(
            RunnerEvent.ENSURE,
            "setup_torrent_categories",
            self.context.arr_apps_raw,
            self.context.cfg,
            self.context.username,
            self.context.password,
        )


__all__ = ["QbittorrentDownloadClientAdapter"]
