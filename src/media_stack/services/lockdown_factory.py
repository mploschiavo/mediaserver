"""Lockdown service factory (ADR-0008 Phase 2).

Constructs a single ``DownloadLockdownService`` from the deployment's
download-client environment variables and shares the instance between
the auto-heal evaluation tick and the manual ``/api/disk-guardrails``
route module so both code paths see the same persisted state.

The factory is stateful: the first ``LockdownFactory.singleton()`` call
builds the service from the live environment; subsequent calls return
the cached instance until ``reset_for_tests()`` clears it.

Per-client failure isolation is the *adapter's* job, not the
constructor's: missing creds (env var unset, blank URL) skip that
client cleanly. A subsequent ``engage()`` call simply iterates a
shorter adapter list. Adding a download client at runtime is not
supported — the deployment edits env, restarts the controller, and
the next factory call picks up the new adapter.
"""

from __future__ import annotations

import os
import threading
from typing import Iterable, Mapping

from media_stack.adapters._shared.download_client_lockdown import (
    ArrLockdownAdapter,
    DownloadClientLockdown,
    QBittorrentLockdownAdapter,
    SabnzbdLockdownAdapter,
)
from media_stack.services.download_lockdown_service import (
    DownloadLockdownService,
)


# Env-var names. Bundled here as constants so the no-magic-strings
# ratchet sees one canonical site for each download-client's
# credentials lookup.
_QBIT_URL_ENV = "QBIT_URL"
_QBIT_USERNAME_ENV = "QBIT_USERNAME"
_QBIT_PASSWORD_ENV = "QBIT_PASSWORD"

_SAB_URL_ENV = "SABNZBD_URL"
_SAB_API_KEY_ENV = "SABNZBD_API_KEY"

# arr v3 quartet — Sonarr/Radarr/Lidarr/Readarr — shares one adapter
# class. Each entry is ``(client_id, url_env, key_env)``.
_ARR_CLIENTS: tuple[tuple[str, str, str], ...] = (
    ("sonarr", "SONARR_URL", "SONARR_API_KEY"),
    ("radarr", "RADARR_URL", "RADARR_API_KEY"),
    ("lidarr", "LIDARR_URL", "LIDARR_API_KEY"),
    ("readarr", "READARR_URL", "READARR_API_KEY"),
)


class LockdownFactory:
    """Builds + caches a ``DownloadLockdownService`` per process.

    Class-based so the codebase-wide no-loose-functions ratchet stays
    clean and so tests can inject a custom env getter without
    monkey-patching ``os.environ`` directly. Production constructs a
    single instance via ``LockdownFactory.singleton()``; tests build
    a fresh ``LockdownFactory`` with an explicit env-mapping fake.
    """

    _instance: DownloadLockdownService | None = None
    _instance_lock: threading.Lock = threading.Lock()

    def __init__(
        self,
        *,
        env_getter: "callable[[str, str], str] | None" = None,
    ) -> None:
        self._env = env_getter or (lambda k, default="": os.environ.get(k, default))

    # -- public API --------------------------------------------------

    def build_adapters(self) -> list[DownloadClientLockdown]:
        """Walk the deployment env vars, build one adapter per
        configured download client. A client is considered configured
        when its URL env var is non-blank; missing creds are detected
        per-class and result in the adapter being skipped (returning
        an empty list slot, not a half-built adapter).
        """
        out: list[DownloadClientLockdown] = []
        qbit = self._build_qbit()
        if qbit is not None:
            out.append(qbit)
        sab = self._build_sab()
        if sab is not None:
            out.append(sab)
        out.extend(self._build_arr_quartet())
        return out

    def build(self) -> DownloadLockdownService:
        """Build a fresh ``DownloadLockdownService`` from the env.

        Tests use this directly (with a fake env mapping) to verify
        the adapter-selection logic. Production prefers
        ``singleton()`` so the auto-heal loop and the route module
        share one instance.
        """
        return DownloadLockdownService(self.build_adapters())

    @classmethod
    def singleton(cls) -> DownloadLockdownService:
        """Return the process-wide service, lazily constructed on
        first call."""
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls().build()
        return cls._instance

    @classmethod
    def reset_for_tests(cls) -> None:
        """Test-only — discard the cached singleton so the next
        ``singleton()`` call rebuilds from the (potentially mutated)
        env."""
        with cls._instance_lock:
            cls._instance = None

    # -- internals ---------------------------------------------------

    def _build_qbit(self) -> QBittorrentLockdownAdapter | None:
        url = (self._env(_QBIT_URL_ENV, "") or "").strip()
        if not url:
            return None
        username = (self._env(_QBIT_USERNAME_ENV, "") or "").strip()
        password = self._env(_QBIT_PASSWORD_ENV, "") or ""
        return QBittorrentLockdownAdapter(
            base_url=url, username=username, password=password,
        )

    def _build_sab(self) -> SabnzbdLockdownAdapter | None:
        url = (self._env(_SAB_URL_ENV, "") or "").strip()
        api_key = (self._env(_SAB_API_KEY_ENV, "") or "").strip()
        if not url or not api_key:
            return None
        return SabnzbdLockdownAdapter(base_url=url, api_key=api_key)

    def _build_arr_quartet(self) -> list[ArrLockdownAdapter]:
        out: list[ArrLockdownAdapter] = []
        for client_id, url_env, key_env in _ARR_CLIENTS:
            url = (self._env(url_env, "") or "").strip()
            api_key = (self._env(key_env, "") or "").strip()
            if not url or not api_key:
                continue
            out.append(
                ArrLockdownAdapter(
                    client_id=client_id,
                    base_url=url,
                    api_key=api_key,
                )
            )
        return out


__all__ = ["LockdownFactory"]
