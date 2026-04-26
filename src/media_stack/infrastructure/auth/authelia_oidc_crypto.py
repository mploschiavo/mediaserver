"""OIDC cryptographic helpers for AutheliaConfigGenerator.

Kept separate so authelia_config_generator.py stays under the
files-over-400-lines ratchet and so these small cryptographic
primitives can be unit-tested in isolation.

Two responsibilities:

1. Generate an RSA private key in PEM format for signing id_tokens
   (RS256). Shells out to openssl so we don't take a runtime
   dependency on the cryptography wheel — the container already has
   openssl for TLS.

2. Encode a shared OIDC client secret as the pbkdf2-sha512 hash
   string Authelia 4.38 expects. Plain-text secrets are technically
   accepted but leave a known credential in the config volume.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
import subprocess
from dataclasses import dataclass, field
from typing import Any

_log = logging.getLogger("media_stack")


@dataclass
class OidcClientDef:
    """Downstream OIDC client (Jellyseerr, future apps). The
    generator hashes the shared secret into pbkdf2 before writing
    it to Authelia's config; the app keeps the raw secret."""
    client_id: str
    client_name: str
    client_secret: str
    redirect_uris: list[str] = field(default_factory=list)
    scopes: list[str] = field(default_factory=lambda: [
        "openid", "email", "profile", "groups",
    ])
    authorization_policy: str = "one_factor"

_RSA_KEY_BITS = 2048
_RSA_KEYGEN_TIMEOUT_SEC = 30
_PBKDF2_ITERATIONS = 310_000
_PBKDF2_DKLEN = 64
_PBKDF2_SALT_BYTES = 16


class OidcCrypto:
    """Instance-based so callers can swap implementations in tests
    (e.g. stub out the RSA generator to return a fixed key)."""

    def generate_rsa_pem(self) -> str:
        """Shell out to ``openssl genpkey`` for an RSA private key
        in PEM format. Returns empty string on failure; callers
        treat that as 'OIDC not available' and skip emitting the
        identity_providers block rather than crashing the stack."""
        try:
            result = subprocess.run(
                ["openssl", "genpkey", "-algorithm", "RSA",
                 "-pkeyopt", f"rsa_keygen_bits:{_RSA_KEY_BITS}"],
                capture_output=True, text=True, check=True,
                timeout=_RSA_KEYGEN_TIMEOUT_SEC,
            )
            return result.stdout
        except (subprocess.SubprocessError, FileNotFoundError) as exc:
            _log.warning("[WARN] oidc rsa keygen failed: %s", exc)
            return ""

    def build_oidc_block(
        self, hmac_secret: str, rsa_pem: str,
        clients: list[OidcClientDef],
    ) -> dict[str, Any] | None:
        """Return the Authelia 4.38 ``identity_providers.oidc``
        block so downstream apps can use Authelia as their OIDC
        upstream. Returns None when signing key / hmac secret is
        missing OR when there are no client registrations — a
        provider with zero clients would advertise endpoints that
        nothing can actually use, so file-auth-only is the
        correct mode. ``.well-known/openid-configuration`` is the
        dashboard's check that OIDC is actually live."""
        if not hmac_secret or not rsa_pem or not clients:
            return None
        client_entries = [self._client_entry(c) for c in clients]
        return {
            "identity_providers": {
                "oidc": {
                    "hmac_secret": hmac_secret,
                    "jwks": [{
                        "key_id": "main",
                        "algorithm": "RS256",
                        "use": "sig",
                        "key": rsa_pem,
                    }],
                    "clients": client_entries,
                },
            },
        }

    def _client_entry(self, client: OidcClientDef) -> dict[str, Any]:
        """Shape one downstream-app registration. Applies pbkdf2 to
        the shared secret so it never sits in plain text on disk
        (the app keeps the raw value on its side)."""
        return {
            "client_id": client.client_id,
            "client_name": client.client_name or client.client_id,
            "client_secret": self.hash_client_secret(client.client_secret),
            "public": False,
            "authorization_policy": client.authorization_policy,
            "redirect_uris": list(client.redirect_uris),
            "scopes": list(client.scopes),
            "userinfo_signed_response_alg": "none",
            "token_endpoint_auth_method": "client_secret_post",
        }

    def hash_client_secret(self, plaintext: str) -> str:
        """Return the pbkdf2-sha512 encoding Authelia 4.38 accepts:
        ``$pbkdf2-sha512$<iters>$<salt-b64>$<hash-b64>``.

        This is the compact variant Authelia's schema.PasswordDigest
        parser expects — the parameterized PHC form
        ``$pbkdf2-sha512$i=<n>,l=64$...`` fails startup with
        'invalid syntax'. Salt is freshly generated per call."""
        salt = secrets.token_bytes(_PBKDF2_SALT_BYTES)
        dk = hashlib.pbkdf2_hmac(
            "sha512", plaintext.encode("utf-8"), salt,
            _PBKDF2_ITERATIONS, dklen=_PBKDF2_DKLEN,
        )
        # Authelia 4.38 uses a custom PHC parser that expects the
        # go-crypt / passlib adjusted-base64 alphabet — same chars
        # as standard base64 but with '+' → '.', '/' → '/', and no
        # padding. The '.'-substituted form is what passlib emits
        # and what Authelia's pbkdf2 validator parses cleanly.
        # (URL-safe with '-_' and straight standard with '+/' both
        # get rejected at byte boundaries that contain those chars.)
        salt_b64 = self._pbkdf2_b64(salt)
        hash_b64 = self._pbkdf2_b64(dk)
        return f"$pbkdf2-sha512${_PBKDF2_ITERATIONS}${salt_b64}${hash_b64}"

    def _pbkdf2_b64(self, raw: bytes) -> str:
        """Adjusted base64 used by passlib / go-crypt in PHC
        strings: standard alphabet with '+' replaced by '.', no
        padding. Matches Authelia 4.38's PasswordDigest parser."""
        return (base64.b64encode(raw)
                .decode("ascii")
                .rstrip("=")
                .replace("+", "."))
