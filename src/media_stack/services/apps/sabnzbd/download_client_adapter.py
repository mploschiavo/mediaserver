"""SABnzbd bootstrap adapter."""

from __future__ import annotations

from media_stack.services.enums import RunnerEvent
from media_stack.services.download_client_adapters.base import DownloadClientAdapterBase


class SabnzbdDownloadClientAdapter(DownloadClientAdapterBase):
    """Resolves SAB API key and reconciles defaults/categories."""

    def precheck(self) -> None:
        if not self.is_enabled():
            return
        self.deps.wait_for_service(
            "SABnzbd",
            self.context.url or self.deps.normalize_url("http://sabnzbd:8080"),
            "/",
            self.context.wait_timeout,
        )

    def prepare(self) -> None:
        if not self.is_enabled():
            self.context.status["api_key"] = ""
            return

        sab_api_key = self.deps.invoke_handler(
            RunnerEvent.ACQUIRE,
            "read_sabnzbd_api_key",
            self.context.config_root,
            self.context.cfg,
        )
        if sab_api_key:
            self.deps.log("[OK] SABnzbd: resolved API key for bootstrap automation")
            self.deps.invoke_handler(
                RunnerEvent.ENSURE,
                "ensure_sabnzbd_defaults",
                self.context.cfg,
                sab_api_key,
            )
            if self.deps.bool_cfg(self.context.cfg, "set_categories_in_sab", True):
                self.deps.invoke_handler(
                    RunnerEvent.ENSURE,
                    "ensure_sabnzbd_categories",
                    self.context.arr_apps_raw,
                    self.context.cfg,
                    sab_api_key,
                )
            self.context.status["api_key"] = str(sab_api_key)
            return

        if self.deps.bool_cfg(
            self.context.cfg,
            "api_key_required",
            self.context.fully_preconfigured,
        ):
            raise RuntimeError(
                "SABnzbd API key not found. Set SABNZBD_API_KEY or ensure "
                "download_clients.sabnzbd.api_key_config_path points to sabnzbd.ini."
            )
        self.deps.log(
            "[WARN] SABnzbd API key not found; skipping Arr -> SABnzbd "
            "download client wiring. Set SABNZBD_API_KEY to enforce."
        )
        self.context.status["api_key"] = ""
