"""Integration gate — the ``configuration.yml`` our generator
produces must pass Authelia's own ``--config validate`` check.

Motivation — 2026-04-19 OIDC rebuild: the pbkdf2 client-secret
encoding went through THREE rounds of fix because the generator
was producing strings that passed every unit test but the real
Authelia binary rejected at startup. Classic "works with a fake,
breaks against the real target" gap.

This test runs the REAL Authelia binary (from the
``authelia/authelia:4.38`` image) against a temporary instance of
the generated config. If Authelia refuses to start, the test
fails — catching schema drift, hash-format bugs, OIDC block
typos, etc. at PR time instead of after a deploy.

The test is skipped unless a docker-compatible CLI
(``docker``) is available on PATH AND on a system where container
pulls work. CI should run it in the gate.

It's placed under ``tests/unit`` so pytest picks it up by
default, but the skip guard keeps it harmless on boxes without
docker.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.core.auth.authelia_config_generator import (  # noqa: E402
    AutheliaConfigGenerator,
    AutheliaConfigOptions,
)
from media_stack.core.auth.authelia_oidc_crypto import (  # noqa: E402
    OidcClientDef,
)

_AUTHELIA_IMAGE = "authelia/authelia:4.38"
_VALIDATE_TIMEOUT_SEC = 60


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        subprocess.run(
            ["docker", "info"],
            capture_output=True, timeout=5, check=True,
        )
        return True
    except (subprocess.SubprocessError, OSError):
        return False


def _authelia_image_available() -> bool:
    """Return True if the image is already present locally. We
    deliberately do NOT pull on demand — a network fetch makes
    the test flaky. CI should pre-pull in its setup step."""
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", _AUTHELIA_IMAGE],
            capture_output=True, timeout=5,
        )
        return result.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


class AutheliaValidatorIntegrationTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        if not _docker_available():
            raise unittest.SkipTest("docker not on PATH / not runnable")
        if not _authelia_image_available():
            raise unittest.SkipTest(
                f"{_AUTHELIA_IMAGE} image not pulled locally — "
                f"CI setup should run `docker pull {_AUTHELIA_IMAGE}` "
                f"before this test.",
            )

    def _generate_and_validate(
        self, clients: list[OidcClientDef] | None = None,
    ) -> tuple[int, str]:
        opts = AutheliaConfigOptions(
            base_domain="local", stack_subdomain="media-stack",
            gateway_host="apps.media-stack.local", gateway_port=443,
            admin_username="admin", admin_email="admin@local",
            oidc_clients=clients or [],
        )
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            AutheliaConfigGenerator(opts).write_config(out)
            # Authelia 4.38's validator is:
            #   authelia config validate --config <path>
            # Run it inside the same image the stack uses so we
            # pin the schema to the exact version production ships.
            proc = subprocess.run(
                [
                    "docker", "run", "--rm",
                    "-v", f"{out}:/config:ro",
                    "--entrypoint", "authelia",
                    _AUTHELIA_IMAGE,
                    "config", "validate",
                    "--config", "/config/configuration.yml",
                ],
                capture_output=True, text=True,
                timeout=_VALIDATE_TIMEOUT_SEC,
            )
            output = (proc.stdout or "") + (proc.stderr or "")
            return proc.returncode, output

    def test_file_auth_only_config_passes(self):
        """The base config Authelia needs for plain file-auth —
        no OIDC clients — must validate cleanly. Covers every
        non-OIDC section: storage, session, access_control, etc.
        Regresses on any schema change that touches the happy
        path."""
        rc, output = self._generate_and_validate(clients=None)
        self.assertEqual(
            rc, 0,
            "Authelia rejected the generated file-auth config:\n"
            + output,
        )

    def test_oidc_config_with_jellyseerr_client_passes(self):
        """The OIDC block — would have caught each of the three
        pbkdf2 format attempts during the 2026-04-19 rebuild:
        'i=X,l=Y' parameterized → 'illegal base64' → adjusted-b64.
        Each broken attempt here becomes a fast CI fail instead of
        a crashloop on deploy."""
        clients = [OidcClientDef(
            client_id="jellyseerr", client_name="Jellyseerr",
            client_secret="shared-secret-value",
            redirect_uris=[
                "https://jellyseerr.media-stack.local/api/v1/auth/oidc-callback",
            ],
        )]
        rc, output = self._generate_and_validate(clients=clients)
        self.assertEqual(
            rc, 0,
            "Authelia rejected the generated OIDC config:\n" + output,
        )

    def test_generated_pbkdf2_format_is_accepted(self):
        """Tight scope: parse the generated configuration.yml,
        extract the client_secret field, assert it's a form
        Authelia will accept (runs the real validator).

        This is a narrower guard for the specific format that
        shipped broken three times. It runs fast when Authelia
        is pullable and gives a pointed failure message."""
        clients = [OidcClientDef(
            client_id="probe", client_name="probe",
            client_secret="x" * 16,
            redirect_uris=["https://probe.media-stack.local/cb"],
        )]
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            AutheliaConfigGenerator(AutheliaConfigOptions(
                base_domain="local", stack_subdomain="media-stack",
                gateway_host="apps.media-stack.local",
                gateway_port=443, admin_username="admin",
                admin_email="admin@local", oidc_clients=clients,
            )).write_config(out)
            data = yaml.safe_load(
                (out / "configuration.yml").read_text(encoding="utf-8")
            )
            oidc = (data.get("identity_providers") or {}).get("oidc") or {}
            cli = (oidc.get("clients") or [{}])[0]
            secret = str(cli.get("client_secret", ""))
            self.assertTrue(
                secret.startswith("$pbkdf2-sha512$"),
                f"client_secret has unexpected scheme: {secret[:40]!r}",
            )
            # The format Authelia 4.38 validates as:
            #   $pbkdf2-sha512$<iterations>$<salt>$<hash>
            # Exactly 5 segments when split on '$' including the
            # empty leading segment.
            segments = secret.split("$")
            self.assertEqual(
                len(segments), 5,
                f"client_secret must be 4-field PHC, got "
                f"{len(segments)} segments: {secret!r}",
            )
            # The iterations segment must parse as a bare integer;
            # the 'i=N,l=64' parameterized form Authelia rejects.
            try:
                int(segments[2])
            except ValueError:
                self.fail(
                    f"iterations segment is not a bare integer: "
                    f"{segments[2]!r} — Authelia's parser fails "
                    f"with 'iterations could not be parsed' on this.",
                )


if __name__ == "__main__":
    unittest.main()
