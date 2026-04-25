"""Factory for the default TlsCertificateService.

Split from the API handlers so both handlers_get.py and handlers_post.py
can import it without a cycle through the controller server.

K8s deployments don't ship the edge cert as a file the controller pod
can read directly — the cert lives in an Ingress secret managed by
cert-manager (or imported manually by the operator). To keep the TLS
operator card populated on K8s without bind-mounting the secret into
the controller pod, the factory has a Secret-mirror fallback: when no
on-disk cert is found in the configured cert dir, it tries to read
the Ingress's TLS secret via the K8s API and materialises the cert
PEM into a controller-owned cache dir under ``/tmp``. Subsequent
calls re-read the secret on a short TTL (60s) so cert rotations are
picked up without restarting the controller.

The cache dir is process-local (``/tmp``); it is never bind-mounted
or shared with Envoy, and only ever contains the public cert PEM —
the secret's ``tls.key`` is intentionally NOT mirrored.
"""

from __future__ import annotations

import logging
import os
import tempfile
import threading
import time
from pathlib import Path

from media_stack.core.edge.tls_certificate_service import (
    TlsCertificateService,
)


_log = logging.getLogger("media_stack.tls_factory")

_DEFAULT_CERT_DIR_CANDIDATES = (
    "/certs",
    "/srv-config/certs",
    # K8s deployments that opt into a bind-mount of the ingress secret
    # mount it under one of these paths via the projected-volume names
    # cert-manager / kubernetes.io/tls produce by default.
    "/tls",
    "/etc/tls",
)

# How long we trust an in-memory mirror of the K8s Secret before
# re-reading. Long enough that a dashboard refresh doesn't hammer
# the API server, short enough that cert rotation is reflected in
# the operator card within a minute.
_K8S_SECRET_TTL_SECONDS = 60.0


class TlsServiceFactory:
    """Resolves the cert directory from env + filesystem probes and
    constructs a TlsCertificateService bound to it."""

    def __init__(self) -> None:
        self._env = os.environ
        self._cache_lock = threading.Lock()
        self._cache_dir: Path | None = None
        self._cache_loaded_at: float = 0.0

    def resolve_cert_dir(self) -> Path:
        env_dir = (self._env.get("CONTROLLER_TLS_CERT_DIR", "") or "").strip()
        if env_dir:
            return Path(env_dir)
        # Prefer a real on-disk cert dir — that's the compose case AND
        # the K8s case where the operator mounted the secret at /tls.
        for candidate in _DEFAULT_CERT_DIR_CANDIDATES:
            cand_path = Path(candidate)
            if cand_path.is_dir() and self._dir_has_cert(cand_path):
                return cand_path
        # K8s fallback: mirror the Ingress TLS secret into a process-
        # local cache dir. Returns that path (cert file present) when
        # the read works; otherwise returns the conventional fallback
        # so the existing "missing cert" branch in the service kicks in.
        mirrored = self._mirror_k8s_secret_if_available()
        if mirrored is not None:
            return mirrored
        config_root = self._env.get("CONFIG_ROOT", "/srv-config")
        return Path(config_root) / "certs"

    def build(self) -> TlsCertificateService:
        return TlsCertificateService(cert_dir=self.resolve_cert_dir())

    # ------------------------------------------------------------------
    # K8s-secret mirror
    # ------------------------------------------------------------------

    def _dir_has_cert(self, cand: Path) -> bool:
        """A candidate dir is "real" only if it actually contains a
        cert. An empty subdir of /srv-config (e.g. /srv-config/certs
        when nothing has been written) shouldn't shadow the K8s
        secret-mirror fallback."""
        for name in ("media-stack.crt", "tls.crt", "cert.pem"):
            if (cand / name).is_file():
                return True
        return False

    def _mirror_k8s_secret_if_available(self) -> Path | None:
        with self._cache_lock:
            now = time.monotonic()
            if (self._cache_dir is not None
                    and (now - self._cache_loaded_at) < _K8S_SECRET_TTL_SECONDS
                    and (self._cache_dir / "media-stack.crt").is_file()):
                return self._cache_dir
            secret_namespace = (
                self._env.get("CONTROLLER_TLS_SECRET_NAMESPACE")
                or self._env.get("K8S_NAMESPACE")
                or "media-stack"
            ).strip()
            secret_names = self._k8s_secret_candidates()
            cert_pem = self._read_k8s_secret_cert(secret_namespace, secret_names)
            if not cert_pem:
                return None
            cache_dir = self._cache_dir or Path(tempfile.mkdtemp(
                prefix="media-stack-tls-mirror-",
            ))
            try:
                (cache_dir / "media-stack.crt").write_bytes(cert_pem)
            except OSError as exc:
                _log.debug("tls-mirror write failed: %s", exc)
                return None
            self._cache_dir = cache_dir
            self._cache_loaded_at = now
            return cache_dir

    def _k8s_secret_candidates(self) -> list[str]:
        """Secret names to try, in priority order. Operator override
        first; then the two conventional names baked into the kustomize
        bases (``iomio-tls`` from base/edge/ingress-traefik.yaml,
        ``media-stack-tls`` from the power-user profile patch)."""
        explicit = (
            self._env.get("CONTROLLER_TLS_SECRET_NAME", "").strip()
        )
        names: list[str] = []
        if explicit:
            names.append(explicit)
        for default in ("iomio-tls", "media-stack-tls"):
            if default not in names:
                names.append(default)
        return names

    def _read_k8s_secret_cert(
        self, namespace: str, names: list[str],
    ) -> bytes | None:
        try:
            from kubernetes import client as k8s_client, config as k8s_config
        except ImportError:
            return None
        try:
            try:
                k8s_config.load_incluster_config()
            except Exception:
                k8s_config.load_kube_config()
        except Exception as exc:
            _log.debug("k8s config load failed in tls-mirror: %s", exc)
            return None
        api = k8s_client.CoreV1Api()
        for name in names:
            try:
                secret = api.read_namespaced_secret(name, namespace)
            except Exception as exc:
                _log.debug("read secret %s/%s failed: %s", namespace, name, exc)
                continue
            data = getattr(secret, "data", None) or {}
            cert_b64 = data.get("tls.crt") or data.get("ca.crt")
            if not cert_b64:
                continue
            try:
                import base64
                return base64.b64decode(cert_b64)
            except Exception as exc:
                _log.debug("base64 decode of secret %s failed: %s", name, exc)
                continue
        return None


_default_factory = TlsServiceFactory()
build_default_tls_service = _default_factory.build
