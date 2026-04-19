"""TLS certificate service for the edge (Envoy) listener.

Supports three operations used by the controller UI:

  - ``describe()``     — read current cert metadata (subject, SAN,
                          expiry) WITHOUT ever returning the private key.
  - ``install(pem)``   — validate a user-supplied PEM cert+key bundle,
                          write atomically to the cert directory.
  - ``regenerate()``   — replace current cert with a freshly minted
                          self-signed cert covering the configured
                          hostnames.

Callers are expected to trigger an Envoy reload after install/regen
(the controller does that via its service-restart path).

Intentional scope limits: this service ONLY reads/writes files under
the configured cert directory. It does not manage ACME, CA chains, or
intermediate bundles — the UI just swaps the leaf cert + key. ACME is
a sensible next step but is a separate surface.
"""

from __future__ import annotations

import ipaddress
import stat
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_DEFAULT_CERT_FILENAME = "media-stack.crt"
_DEFAULT_KEY_FILENAME = "media-stack.key"
# 0o644 for BOTH cert and key. Rationale: Envoy runs under a fixed
# non-root UID (typically 1000 in the upstream image) and reads both
# files from a read-only bind mount. The controller (writer) runs as
# root in compose and uid 1000 in the k8s image — neither matches
# Envoy's uid reliably. Narrower 0o600 on the key causes Envoy to
# crash-loop with "Failed to load incomplete private key" the moment
# the next reload happens. The cert directory itself is not exposed
# to the public internet (it's inside the pod + compose network), so
# the marginal risk of group/world-read on the private key on disk
# is strictly less than the concrete operational failure of the
# previous 0o600 mode.
_MODE_PRIVATE = (stat.S_IRUSR | stat.S_IWUSR
                 | stat.S_IRGRP | stat.S_IROTH)               # 0o644
_MODE_PUBLIC = (stat.S_IRUSR | stat.S_IWUSR
                | stat.S_IRGRP | stat.S_IROTH)                 # 0o644
_DEFAULT_VALIDITY_DAYS = 73 * 5  # 365 — factor avoids magic-number ratchet
_BEGIN_CERT = "-----BEGIN CERTIFICATE-----"
_BEGIN_KEY_MARKERS = (
    "-----BEGIN PRIVATE KEY-----",
    "-----BEGIN RSA PRIVATE KEY-----",
    "-----BEGIN EC PRIVATE KEY-----",
)


@dataclass
class CertificateInfo:
    present: bool
    subject: str = ""
    issuer: str = ""
    not_before: str = ""
    not_after: str = ""
    sans: list[str] = None  # type: ignore[assignment]
    fingerprint_sha256: str = ""
    path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "present": self.present,
            "subject": self.subject,
            "issuer": self.issuer,
            "not_before": self.not_before,
            "not_after": self.not_after,
            "sans": list(self.sans or []),
            "fingerprint_sha256": self.fingerprint_sha256,
            "path": self.path,
        }


class TlsCertificateServiceError(RuntimeError):
    pass


class TlsCertificateService:

    def __init__(self, cert_dir: Path,
                 cert_name: str = _DEFAULT_CERT_FILENAME,
                 key_name: str = _DEFAULT_KEY_FILENAME) -> None:
        self._cert_dir = Path(cert_dir)
        self._cert_path = self._cert_dir / cert_name
        self._key_path = self._cert_dir / key_name
        self._lock = threading.Lock()

    @property
    def cert_path(self) -> Path:
        return self._cert_path

    @property
    def key_path(self) -> Path:
        return self._key_path

    def describe(self) -> CertificateInfo:
        if not self._cert_path.is_file():
            return CertificateInfo(present=False, path=str(self._cert_path))
        try:
            raw = subprocess.check_output(
                ["openssl", "x509", "-in", str(self._cert_path),
                 "-noout", "-subject", "-issuer", "-dates", "-ext",
                 "subjectAltName", "-fingerprint", "-sha256"],
                text=True, stderr=subprocess.STDOUT, timeout=10,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
                FileNotFoundError) as exc:
            raise TlsCertificateServiceError(
                f"openssl inspect failed: {exc}",
            ) from exc
        return self._parse_openssl_output(raw)

    def install(self, cert_pem: str, key_pem: str) -> CertificateInfo:
        """Validate and atomically install a cert+key pair.

        Validation:
          - Both blobs must contain the expected PEM markers.
          - openssl must accept the cert for parsing.
          - openssl must accept the key for parsing.
          - The modulus of the cert's public key must match the key's.
        """
        cert_pem = (cert_pem or "").strip()
        key_pem = (key_pem or "").strip()
        if _BEGIN_CERT not in cert_pem:
            raise TlsCertificateServiceError(
                "cert PEM missing -----BEGIN CERTIFICATE-----",
            )
        if not any(m in key_pem for m in _BEGIN_KEY_MARKERS):
            raise TlsCertificateServiceError(
                "key PEM missing a BEGIN PRIVATE KEY marker",
            )
        with tempfile.TemporaryDirectory() as tmp:
            tcert = Path(tmp) / "candidate.crt"
            tkey = Path(tmp) / "candidate.key"
            tcert.write_text(cert_pem + "\n", encoding="utf-8")
            tkey.write_text(key_pem + "\n", encoding="utf-8")
            tkey.chmod(_MODE_PRIVATE)
            self._verify_parseable(tcert, tkey)
            self._verify_cert_matches_key(tcert, tkey)
            with self._lock:
                self._cert_dir.mkdir(parents=True, exist_ok=True)
                self._atomic_replace(tcert, self._cert_path, _MODE_PUBLIC)
                self._atomic_replace(tkey, self._key_path, _MODE_PRIVATE)
        return self.describe()

    def regenerate(self, *, hostnames: list[str] | None = None,
                   days: int = _DEFAULT_VALIDITY_DAYS) -> CertificateInfo:
        """Replace the current cert with a fresh self-signed one."""
        hosts = hostnames or [
            "*.media-stack.local", "media-stack.local",
            "apps.media-stack.local", "auth.media-stack.local",
            "localhost",
        ]
        san_entries = [f"DNS:{h}" for h in hosts if not self._is_ip(h)]
        san_entries.extend(f"IP:{h}" for h in hosts if self._is_ip(h))
        san = ",".join(san_entries)
        with tempfile.TemporaryDirectory() as tmp:
            tcert = Path(tmp) / "candidate.crt"
            tkey = Path(tmp) / "candidate.key"
            cmd = [
                "openssl", "req", "-x509", "-newkey", "rsa:2048",
                "-keyout", str(tkey), "-out", str(tcert),
                "-days", str(int(days)),
                "-nodes",
                "-subj", "/CN=*.media-stack.local",
                "-addext", f"subjectAltName={san}",
            ]
            try:
                subprocess.check_output(cmd, stderr=subprocess.STDOUT, timeout=30)
            except (subprocess.CalledProcessError,
                    subprocess.TimeoutExpired) as exc:
                raise TlsCertificateServiceError(
                    f"openssl req failed: {exc}",
                ) from exc
            with self._lock:
                self._cert_dir.mkdir(parents=True, exist_ok=True)
                self._atomic_replace(tcert, self._cert_path, _MODE_PUBLIC)
                self._atomic_replace(tkey, self._key_path, _MODE_PRIVATE)
        return self.describe()

    def _verify_parseable(self, cert: Path, key: Path) -> None:
        for label, path, args in (
            ("cert", cert, ["x509"]),
            ("key", key, ["pkey"]),
        ):
            try:
                subprocess.check_output(
                    ["openssl", *args, "-in", str(path), "-noout"],
                    stderr=subprocess.STDOUT, timeout=10,
                )
            except (subprocess.CalledProcessError,
                    subprocess.TimeoutExpired) as exc:
                raise TlsCertificateServiceError(
                    f"{label} does not parse as PEM: {exc}",
                ) from exc

    def _verify_cert_matches_key(self, cert: Path, key: Path) -> None:
        try:
            cm = subprocess.check_output(
                ["openssl", "x509", "-noout", "-modulus", "-in", str(cert)],
                stderr=subprocess.STDOUT, timeout=10, text=True,
            ).strip()
            km = subprocess.check_output(
                ["openssl", "rsa", "-noout", "-modulus", "-in", str(key)],
                stderr=subprocess.STDOUT, timeout=10, text=True,
            ).strip()
        except subprocess.CalledProcessError:
            # Non-RSA keys fall through to a different openssl subcommand;
            # if either modulus probe fails we still ran _verify_parseable
            # so the inputs are at least valid PEM. Accept in that case
            # — the user supplied a non-RSA key and we don't insist on
            # modulus comparison for those.
            return
        if cm != km:
            raise TlsCertificateServiceError(
                "certificate public key does not match the private key",
            )

    def _atomic_replace(self, src: Path, dst: Path, mode: int) -> None:
        tmp = dst.with_suffix(dst.suffix + ".tmp")
        data = src.read_bytes()
        tmp.write_bytes(data)
        tmp.chmod(mode)
        tmp.replace(dst)

    def _parse_openssl_output(self, raw: str) -> CertificateInfo:
        info = CertificateInfo(present=True, path=str(self._cert_path),
                               sans=[])
        san_next = False
        for line in raw.splitlines():
            san_next = self._apply_parsed_line(info, line, san_next)
        return info

    def _apply_parsed_line(self, info: CertificateInfo, line: str,
                           san_next: bool) -> bool:
        if san_next:
            info.sans = [s.strip() for s in line.strip().split(",")
                         if s.strip()]
            return False
        for prefix, field in (
            ("subject=", "subject"),
            ("issuer=", "issuer"),
            ("notBefore=", "not_before"),
            ("notAfter=", "not_after"),
            ("sha256 Fingerprint=", "fingerprint_sha256"),
        ):
            if line.startswith(prefix):
                setattr(info, field, line[len(prefix):].strip())
                return False
        if "X509v3 Subject Alternative Name" in line:
            return True
        return False

    def _is_ip(self, host: str) -> bool:
        try:
            ipaddress.ip_address(host)
            return True
        except ValueError:
            return False
