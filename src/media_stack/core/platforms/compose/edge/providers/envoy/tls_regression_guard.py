"""Refuse to silently downgrade Envoy's listener from TLS to plain HTTP.

Context
-------
On the compose path, ``envoy.yaml`` is written by
``generate_envoy_config_main``. The generator injects a TLS transport
socket only when it can find (or mint) a cert on disk. Several callers
can trigger a regen — envoy-config-init, the controller in-process on
/api/routing and auth-config changes, TLS cert installs. If any of
those callers runs without access to the cert dir, the generator
silently emits a plain-HTTP listener, which overwrites the working
TLS config and breaks HTTPS on :443 until someone notices.

This module provides a single check: "would this write regress the
listener from TLS to plain HTTP?". The caller uses that answer to
fail closed before overwriting a known-good config.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any


_LOG = logging.getLogger("media_stack.envoy.tls_guard")

_TRANSPORT_SOCKET_TOKEN = "transport_socket:"


class TlsRegressionGuard:
    """Compares the on-disk Envoy config to the about-to-be-written
    payload and reports whether the write would silently lose TLS."""

    def would_lose_tls(self, output_path: Path, payload: dict) -> bool:
        had_tls = self._existing_has_tls(output_path)
        if not had_tls:
            return False
        return not self._payload_has_tls(payload)

    def _existing_has_tls(self, output_path: Path) -> bool:
        if not output_path.exists():
            return False
        try:
            return _TRANSPORT_SOCKET_TOKEN in output_path.read_text(encoding="utf-8")
        except OSError as exc:
            _LOG.warning("Could not read existing envoy.yaml: %s", exc)
            return False

    def _payload_has_tls(self, payload: dict) -> bool:
        for fc in self._iter_filter_chains(payload):
            if "transport_socket" in fc:
                return True
        return False

    def _iter_filter_chains(self, payload: dict):
        static_resources = payload.get("static_resources") or {}
        for listener in static_resources.get("listeners") or ():
            for fc in (listener.get("filter_chains") or ()):
                yield fc
