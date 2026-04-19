"""Factory for the default TlsCertificateService.

Split from the API handlers so both handlers_get.py and handlers_post.py
can import it without a cycle through the controller server.
"""

from __future__ import annotations

import os
from pathlib import Path

from media_stack.core.edge.tls_certificate_service import (
    TlsCertificateService,
)


_DEFAULT_CERT_DIR_CANDIDATES = (
    "/certs",
    "/srv-config/certs",
)


class TlsServiceFactory:
    """Resolves the cert directory from env + filesystem probes and
    constructs a TlsCertificateService bound to it."""

    def __init__(self) -> None:
        self._env = os.environ

    def resolve_cert_dir(self) -> Path:
        env_dir = (self._env.get("CONTROLLER_TLS_CERT_DIR", "") or "").strip()
        if env_dir:
            return Path(env_dir)
        for candidate in _DEFAULT_CERT_DIR_CANDIDATES:
            if Path(candidate).is_dir():
                return Path(candidate)
        config_root = self._env.get("CONFIG_ROOT", "/srv-config")
        return Path(config_root) / "certs"

    def build(self) -> TlsCertificateService:
        return TlsCertificateService(cert_dir=self.resolve_cert_dir())


_default_factory = TlsServiceFactory()
build_default_tls_service = _default_factory.build
