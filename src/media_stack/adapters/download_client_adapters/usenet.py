"""Generic usenet download-client adapters."""

from __future__ import annotations

import os

from media_stack.domain.download_client_adapters.base import DownloadClientAdapterBase


class GenericUsenetDownloadClientAdapter(DownloadClientAdapterBase):
    """Usenet adapter with lightweight precheck and optional API-key resolution."""

    def is_enabled(self) -> bool:
        return bool(self.context.configure_arr_clients)

    def precheck(self) -> None:
        if not self.is_enabled():
            return
        url = str(self.context.url or "").strip()
        if not url:
            return
        self.deps.wait_for_service(
            self.context.display_name or self.context.key or "Usenet client",
            url,
            "/",
            self.context.wait_timeout,
        )

    def _resolve_api_key(self) -> str:
        env_name = str(self.context.cfg.get("api_key_env") or "").strip()
        if not env_name:
            return ""
        return str(os.environ.get(env_name) or "").strip()

    def prepare(self) -> None:
        if not self.is_enabled():
            self.context.status["api_key"] = ""
            return

        api_key = self._resolve_api_key()
        api_key_required = bool(self.context.cfg.get("api_key_required", False))
        if api_key_required and not api_key:
            env_name = str(self.context.cfg.get("api_key_env") or "").strip()
            raise RuntimeError(
                f"{self.context.display_name or self.context.key}: missing required API key "
                f"(set environment variable '{env_name}' or disable api_key_required)."
            )
        self.context.status["api_key"] = api_key
