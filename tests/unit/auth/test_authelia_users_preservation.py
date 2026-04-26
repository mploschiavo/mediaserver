"""Round-trip tests for AutheliaConfigGenerator.write_config.

Regression guards for the 2026-04-19 bug: every call to
configure-auth silently wiped every non-admin user's password from
``users_database.yml`` because the generator overwrote the file
with ``{"users": {<admin only>}}``.

The bug surfaced as: admin resets jane's password in the UI, self-heal
correctly writes the row to users_database.yml, user tries to log in,
but in between a routine regen (triggered by any routing/auth edit)
clobbered the file. Jane's row disappears. Login fails. No error is
visible anywhere to the admin.

These tests write the DB twice in a row and assert that each regen
preserves every existing row and never clobbers a password.
"""

from __future__ import annotations

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


class UsersDatabasePreservationTests(unittest.TestCase):
    def _opts(self, **kw) -> AutheliaConfigOptions:
        defaults = dict(
            base_domain="local",
            stack_subdomain="media-stack",
            gateway_host="apps.media-stack.local",
            gateway_port=443,
            internet_exposed=False,
            admin_username="admin",
            admin_email="admin@local",
        )
        defaults.update(kw)
        return AutheliaConfigOptions(**defaults)

    def _read_users(self, path: Path) -> dict:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return data.get("users") or {}

    def test_regen_preserves_user_added_out_of_band(self):
        """The canonical bug: admin exists in the profile, jane was
        added by the controller's self-heal. A subsequent regen must
        NOT drop jane's row."""
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            gen = AutheliaConfigGenerator(self._opts())
            # First write — no existing file. Admin gets created.
            gen.write_config(out)
            # Simulate self-heal adding jane between regens.
            users_path = out / "users_database.yml"
            data = yaml.safe_load(users_path.read_text(encoding="utf-8"))
            data["users"]["jane"] = {
                "disabled": False,
                "displayname": "Jane",
                "email": "jane@local",
                "password": "$argon2id$v=19$m=65536,t=3,p=4$SALT$HASH",
                "groups": ["users"],
            }
            users_path.write_text(yaml.safe_dump(data), encoding="utf-8")

            # Second write — same config, triggers regen.
            gen.write_config(out)
            after = self._read_users(users_path)

            self.assertIn(
                "jane", after,
                "jane was dropped on regen — the generator is "
                "clobbering users_database.yml instead of merging.",
            )
            self.assertEqual(
                after["jane"]["password"],
                "$argon2id$v=19$m=65536,t=3,p=4$SALT$HASH",
                "jane's password was overwritten on regen — admin "
                "would be unable to log in after any routine edit.",
            )

    def test_regen_preserves_existing_admin_password(self):
        """When the profile has no admin_password_hash (the default
        path — admin is provisioned interactively), the generator
        emits an admin entry with no ``password`` key. A later regen
        MUST keep the password that the self-heal later wrote."""
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            gen = AutheliaConfigGenerator(self._opts())
            gen.write_config(out)
            users_path = out / "users_database.yml"
            # Simulate self-heal setting admin's password.
            data = yaml.safe_load(users_path.read_text(encoding="utf-8"))
            data["users"]["admin"]["password"] = "$argon2id$ADMINHASH"
            users_path.write_text(yaml.safe_dump(data), encoding="utf-8")

            gen.write_config(out)
            after = self._read_users(users_path)
            self.assertEqual(
                after["admin"]["password"], "$argon2id$ADMINHASH",
                "Admin's password was wiped on regen. An empty "
                "admin_password_hash in the profile must never "
                "clobber a real password on disk.",
            )

    def test_regen_applies_profile_changes_to_admin(self):
        """Merge must still PROPAGATE profile-driven changes (e.g.
        admin email, displayname). Only the password has the
        'don't-clobber-with-empty' rule."""
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            gen = AutheliaConfigGenerator(self._opts(
                admin_email="admin@old.example",
            ))
            gen.write_config(out)

            # Profile edit: email changed.
            gen2 = AutheliaConfigGenerator(self._opts(
                admin_email="admin@new.example",
            ))
            gen2.write_config(out)

            after = self._read_users(out / "users_database.yml")
            self.assertEqual(
                after["admin"]["email"], "admin@new.example",
                "Profile-driven admin fields didn't update on regen.",
            )

    def test_regen_without_existing_file_still_writes_admin(self):
        """Fresh install path: no users_database.yml exists; first
        write must produce one with admin present."""
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            gen = AutheliaConfigGenerator(self._opts())
            gen.write_config(out)
            after = self._read_users(out / "users_database.yml")
            self.assertIn("admin", after)


class FileBackendWatchTests(unittest.TestCase):
    """Regression: the rendered configuration.yml MUST set
    ``authentication_backend.file.watch: true`` so Authelia
    picks up ``users_database.yml`` edits made by the dashboard
    without a container restart. Without this, every newly-
    created user silently fails login ('user not found') until
    the operator notices they have to restart Authelia."""

    def test_watch_enabled_on_file_backend(self):
        with tempfile.TemporaryDirectory() as d:
            from media_stack.core.auth.authelia_config_generator import (
                AutheliaConfigOptions as _Opts,
            )
            opts = _Opts(
                base_domain="local", stack_subdomain="media-stack",
                gateway_host="apps.media-stack.local",
                gateway_port=443, admin_username="admin",
                admin_email="admin@local",
            )
            AutheliaConfigGenerator(opts).write_config(Path(d))
            cfg = yaml.safe_load(
                (Path(d) / "configuration.yml").read_text(encoding="utf-8")
            )
            self.assertTrue(
                cfg["authentication_backend"]["file"].get("watch"),
                "authentication_backend.file.watch=true is required "
                "for the dashboard's create-user flow to take effect "
                "without an Authelia restart.",
            )


class ConfigurationSecretsPreservationTests(unittest.TestCase):
    """Regression guards for 2026-04-19 Authelia crashloop:
    storage.encryption_key, session.secret, and
    identity_validation.reset_password.jwt_secret were
    regenerated on every ``write_config`` call. The encrypted
    rows in db.sqlite3 (which was written with a prior key)
    became undecryptable → 'the configured encryption key does
    not appear to be valid for this database' at startup → the
    whole auth gateway stays down.
    """

    def _opts(self, **kw):
        defaults = dict(
            base_domain="local",
            stack_subdomain="media-stack",
            gateway_host="apps.media-stack.local",
            gateway_port=443,
            internet_exposed=False,
            admin_username="admin",
            admin_email="admin@local",
        )
        defaults.update(kw)
        return AutheliaConfigOptions(**defaults)

    def _read_config(self, path: Path) -> dict:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    def test_storage_encryption_key_survives_regen(self):
        """The one that would have prevented the crashloop: once
        Authelia's db.sqlite3 has rows encrypted with key K, every
        subsequent regen must keep emitting K. A new random key
        would brick startup."""
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            AutheliaConfigGenerator(self._opts()).write_config(out)
            first = self._read_config(out / "configuration.yml")
            first_key = first["storage"]["encryption_key"]

            AutheliaConfigGenerator(self._opts()).write_config(out)
            second = self._read_config(out / "configuration.yml")
            self.assertEqual(
                second["storage"]["encryption_key"], first_key,
                "storage.encryption_key changed on regen. "
                "Authelia's db.sqlite3 would now be undecryptable, "
                "producing the 2026-04-19 crashloop.",
            )

    def test_session_secret_survives_regen(self):
        """Rotating session.secret silently invalidates every
        logged-in user's cookie. Not fatal, but recurring surprise
        logouts after any dashboard edit."""
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            AutheliaConfigGenerator(self._opts()).write_config(out)
            first_secret = self._read_config(
                out / "configuration.yml")["session"]["secret"]
            AutheliaConfigGenerator(self._opts()).write_config(out)
            second_secret = self._read_config(
                out / "configuration.yml")["session"]["secret"]
            self.assertEqual(first_secret, second_secret)

    def test_jwt_secret_survives_regen(self):
        """Password-reset jwt_secret rotation would invalidate
        any in-flight reset emails. Small blast radius but same
        root cause — regen must be idempotent for secrets."""
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            AutheliaConfigGenerator(self._opts()).write_config(out)
            first = self._read_config(
                out / "configuration.yml")[
                "identity_validation"]["reset_password"]["jwt_secret"]
            AutheliaConfigGenerator(self._opts()).write_config(out)
            second = self._read_config(
                out / "configuration.yml")[
                "identity_validation"]["reset_password"]["jwt_secret"]
            self.assertEqual(first, second)

    def test_corrupt_existing_config_falls_back_to_fresh_secrets(self):
        """If the existing configuration.yml is unreadable, the
        generator must still write a valid file with fresh secrets
        rather than crash. Startup of a brand-new install passes
        through this path."""
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            out.mkdir(exist_ok=True)
            (out / "configuration.yml").write_text(
                "::: not valid yaml :::",
                encoding="utf-8",
            )
            AutheliaConfigGenerator(self._opts()).write_config(out)
            cfg = self._read_config(out / "configuration.yml")
            self.assertTrue(cfg["storage"]["encryption_key"])
            self.assertTrue(cfg["session"]["secret"])

    def test_placeholder_secrets_are_replaced_on_first_regen(self):
        """Bootstrap defaults seed configuration.yml with
        PLACEHOLDER_* values so Authelia can start before the
        controller ever runs. The first real regen must replace
        them with actual random secrets — otherwise the whole
        stack sits on well-known values, letting anyone with
        source access forge sessions."""
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            (out / "configuration.yml").write_text(
                "storage:\n"
                "  encryption_key: PLACEHOLDER_STORAGE_REPLACE_ON_FIRST_REGEN\n"
                "session:\n"
                "  secret: PLACEHOLDER_SESSION_REPLACE_ON_FIRST_REGEN\n"
                "identity_validation:\n"
                "  reset_password:\n"
                "    jwt_secret: PLACEHOLDER_JWT_REPLACE_ON_FIRST_REGEN\n",
                encoding="utf-8",
            )
            AutheliaConfigGenerator(self._opts()).write_config(out)
            cfg = self._read_config(out / "configuration.yml")
            self.assertNotIn(
                "PLACEHOLDER", cfg["storage"]["encryption_key"],
                "encryption_key still starts with PLACEHOLDER — the "
                "bootstrap seed leaked into the live config.",
            )
            self.assertNotIn(
                "PLACEHOLDER", cfg["session"]["secret"],
                "session.secret still starts with PLACEHOLDER.",
            )
            self.assertNotIn(
                "PLACEHOLDER",
                cfg["identity_validation"]["reset_password"]["jwt_secret"],
                "jwt_secret still starts with PLACEHOLDER.",
            )

    def test_legacy_change_this_secrets_are_replaced_on_regen(self):
        """Older installs may have the pre-4.38 'change-this-*'
        placeholder secrets on disk. Treat them the same as the
        new PLACEHOLDER_* sentinels."""
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            (out / "configuration.yml").write_text(
                "storage:\n"
                "  encryption_key: change-this-storage-key-with-32-or-more-chars\n"
                "session:\n"
                "  secret: change-this-session-secret-with-32-or-more-chars\n",
                encoding="utf-8",
            )
            AutheliaConfigGenerator(self._opts()).write_config(out)
            cfg = self._read_config(out / "configuration.yml")
            self.assertFalse(
                cfg["storage"]["encryption_key"].startswith("change-this"),
                "Legacy change-this-* encryption_key leaked through.",
            )


class DomainTopologyTests(unittest.TestCase):
    """Regression for 2026-04-20 K8s login-loop: the generator
    built ``authelia_url=https://auth.m.iomio.io`` and
    ``cookie_domain=m.iomio.io``, but the deployment's real
    Authelia ingress was ``auth.iomio.io`` with cookie scope
    ``iomio.io``. Result: every sign-in redirected, cookie never
    got set in the right scope, login spun forever.

    Both topologies must produce an internally consistent config:

    Nested (compose): base=local, sub=media-stack →
    cookie=media-stack.local, authelia=auth.media-stack.local

    Flat (K8s): base=iomio.io, sub="" →
    cookie=iomio.io, authelia=auth.iomio.io
    """

    def _opts(self, **kw):
        from media_stack.core.auth.authelia_config_generator import (
            AutheliaConfigOptions as _Opts,
        )
        defaults = dict(
            gateway_port=443, admin_username="admin",
            admin_email="admin@local",
        )
        defaults.update(kw)
        return _Opts(**defaults)

    def _config(self, **kw):
        opts = self._opts(**kw)
        return AutheliaConfigGenerator(opts).generate_configuration()

    def test_nested_compose_layout(self):
        """Compose default: sub=media-stack, base=local."""
        cfg = self._config(
            base_domain="local", stack_subdomain="media-stack",
            gateway_host="apps.media-stack.local",
        )
        cookie = cfg["session"]["cookies"][0]
        self.assertEqual(cookie["domain"], "media-stack.local")
        self.assertEqual(
            cookie["authelia_url"], "https://auth.media-stack.local")

    def test_flat_k8s_layout(self):
        """K8s flat: sub='', base='iomio.io'. Authelia and gateway
        are direct subdomains of the base."""
        cfg = self._config(
            base_domain="iomio.io", stack_subdomain="",
            gateway_host="m.iomio.io",
        )
        cookie = cfg["session"]["cookies"][0]
        self.assertEqual(cookie["domain"], "iomio.io")
        self.assertEqual(
            cookie["authelia_url"], "https://auth.iomio.io",
            "Flat topology must put Authelia at auth.<base>, not "
            "auth.<sub>.<base>. Without this the login cookie never "
            "lands in the right scope and the portal spins forever.",
        )

    def test_authelia_host_is_under_cookie_domain_on_both_layouts(self):
        """The structural rule Authelia 4.38 enforces: the portal
        must be a subdomain of the cookie domain. Breaking this on
        either layout silently loops users at login."""
        for kw in (
            dict(base_domain="local", stack_subdomain="media-stack",
                 gateway_host="apps.media-stack.local"),
            dict(base_domain="iomio.io", stack_subdomain="",
                 gateway_host="m.iomio.io"),
            dict(base_domain="example.com", stack_subdomain="stack",
                 gateway_host="apps.stack.example.com"),
        ):
            cfg = self._config(**kw)
            cookie = cfg["session"]["cookies"][0]
            domain = cookie["domain"]
            url = cookie["authelia_url"]
            host = url.split("://", 1)[-1].split("/", 1)[0]
            self.assertTrue(
                host == domain or host.endswith("." + domain),
                f"authelia_url host {host!r} is not under cookie "
                f"domain {domain!r} for opts={kw}",
            )


class BootSealingInvariantTests(unittest.TestCase):
    """The fresh-install race that bit us in production on 2026-04-19:

    1. Compose init copies ``/defaults/configuration.yml`` → ``/config/``.
    2. Authelia starts, reads ``encryption_key: PLACEHOLDER_*``,
       encrypts db.sqlite3 rows with that placeholder value.
    3. Controller boots, configure-auth fires, generator's
       ``_real_secret`` detects the placeholder prefix and writes
       a fresh random encryption_key to ``configuration.yml``.
    4. Authelia restarts → current encryption_key doesn't match
       the placeholder used to encrypt db.sqlite3 → crashloop.

    The fix seals the config BEFORE Authelia starts: the controller
    runs configure-auth at boot (see controller_serve._run_boot_
    configure_auth), and Authelia's compose block waits on the
    controller's health probe via ``depends_on.condition=service_
    healthy``. These tests pin both parts of that contract so a
    future cleanup can't quietly re-open the race.
    """

    def _opts(self):
        from media_stack.core.auth.authelia_config_generator import (
            AutheliaConfigOptions as _Opts,
        )
        return _Opts(
            base_domain="local", stack_subdomain="media-stack",
            gateway_host="apps.media-stack.local",
            gateway_port=443, admin_username="admin",
            admin_email="admin@local",
        )

    def test_generator_emits_real_secret_on_cold_boot(self):
        """Simulate the boot-time configure-auth: no existing
        configuration.yml at all. The first write MUST produce a
        real random encryption_key — not a placeholder. This is
        the key the compose healthcheck+depends_on wiring relies
        on Authelia seeing on its very first start."""
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            AutheliaConfigGenerator(self._opts()).write_config(out)
            cfg = yaml.safe_load(
                (out / "configuration.yml").read_text(encoding="utf-8")
            )
            key = cfg["storage"]["encryption_key"]
            self.assertTrue(key)
            self.assertNotIn("PLACEHOLDER", key.upper())
            self.assertNotIn("CHANGE-THIS", key.upper())

    def test_generator_preserves_real_secret_across_second_boot(self):
        """Simulate the second cold boot: controller runs
        configure-auth again, existing configuration.yml already
        has a real key from the first boot. The regen MUST keep
        it so Authelia's db.sqlite3 (encrypted with the first-boot
        key) remains decryptable."""
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            AutheliaConfigGenerator(self._opts()).write_config(out)
            first = yaml.safe_load(
                (out / "configuration.yml").read_text(encoding="utf-8")
            )["storage"]["encryption_key"]
            AutheliaConfigGenerator(self._opts()).write_config(out)
            second = yaml.safe_load(
                (out / "configuration.yml").read_text(encoding="utf-8")
            )["storage"]["encryption_key"]
            self.assertEqual(first, second,
                             "encryption_key rotated between cold boots")


class ComposeAutheliaDependsOnControllerTests(unittest.TestCase):
    """Static check that docker-compose.yml keeps the Authelia →
    controller dependency that seals the boot-time secret race.

    A drive-by edit that removes ``depends_on`` without thinking
    about this invariant would silently reintroduce the crashloop;
    this test catches that in CI."""

    def test_authelia_depends_on_controller_healthy(self):
        from pathlib import Path as _P
        compose_path = _P(__file__).resolve().parents[2] / "docker" / "docker-compose.yml"
        data = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
        auth = (data.get("services") or {}).get("authelia")
        self.assertIsNotNone(auth, "authelia service missing from compose")
        dep = (auth.get("depends_on") or {})
        ctrl = dep.get("media-stack-controller")
        self.assertIsNotNone(
            ctrl,
            "authelia.depends_on is missing media-stack-controller — "
            "without it Authelia can start before configure-auth has "
            "replaced placeholder secrets, which crashlooops the stack.",
        )
        self.assertEqual(
            (ctrl or {}).get("condition"), "service_healthy",
            "authelia must wait on controller service_healthy, not just "
            "service_started. service_started fires when the process is "
            "up but configure-auth hasn't run yet.",
        )


class OidcProviderBlockTests(unittest.TestCase):
    """Authelia 4.38 OIDC generator rebuild. The 4.37-era config
    had to be ripped out during the upgrade (the old generator was
    leaving Authelia crashing on strict 4.38 validation), which
    took Jellyseerr SSO with it. These tests pin the shape of the
    rebuilt ``identity_providers.oidc`` block and verify the two
    invariants that matter for SSO stability:

    1. Secrets (hmac_secret, RSA signing key) survive a regen, so
       in-flight id_tokens / refresh_tokens don't get invalidated
       every time configure-auth runs.
    2. Downstream clients (Jellyseerr) end up in the rendered
       config with a pbkdf2-hashed client_secret and at least one
       redirect URI, so the OIDC discovery endpoint actually
       advertises the upstream."""

    def _opts(self, **kw):
        from media_stack.core.auth.authelia_config_generator import (
            AutheliaConfigOptions as _Opts,
            OidcClientDef as _Cli,
        )
        defaults = dict(
            base_domain="local", stack_subdomain="media-stack",
            gateway_host="apps.media-stack.local", gateway_port=443,
            admin_username="admin", admin_email="admin@local",
            oidc_clients=[_Cli(
                client_id="jellyseerr",
                client_name="Jellyseerr",
                client_secret="shared-plaintext-secret",
                redirect_uris=[
                    "https://jellyseerr.media-stack.local/api/v1/auth/oidc-callback",
                ],
            )],
        )
        defaults.update(kw)
        return _Opts(**defaults)

    def _read(self, out):
        return yaml.safe_load(
            (out / "configuration.yml").read_text(encoding="utf-8")
        )

    def test_oidc_block_emitted(self):
        """Fresh install: the rendered configuration.yml MUST have
        the identity_providers.oidc block Authelia expects. Without
        it, /.well-known/openid-configuration returns 404 and
        Jellyseerr's OIDC probe fails to parse JSON."""
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            AutheliaConfigGenerator(self._opts()).write_config(out)
            cfg = self._read(out)
            oidc = (cfg.get("identity_providers") or {}).get("oidc") or {}
            self.assertTrue(oidc.get("hmac_secret"))
            self.assertTrue(oidc.get("jwks"))
            self.assertEqual(oidc["jwks"][0]["algorithm"], "RS256")
            self.assertIn("BEGIN", oidc["jwks"][0]["key"])
            clients = oidc.get("clients") or []
            self.assertEqual(len(clients), 1)
            self.assertEqual(clients[0]["client_id"], "jellyseerr")
            # Secret must NOT land in plain text on disk
            self.assertNotIn("shared-plaintext-secret",
                             clients[0]["client_secret"])
            self.assertTrue(
                clients[0]["client_secret"].startswith("$pbkdf2-sha512$"),
                "client_secret must be pbkdf2 — plain text leaves a "
                "known credential in the config volume.",
            )

    def test_hmac_secret_and_rsa_key_survive_regen(self):
        """Rotating either the hmac_secret or the id_token signing
        key invalidates every active OIDC session. Preserve them."""
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            AutheliaConfigGenerator(self._opts()).write_config(out)
            first = self._read(out)["identity_providers"]["oidc"]
            AutheliaConfigGenerator(self._opts()).write_config(out)
            second = self._read(out)["identity_providers"]["oidc"]
            self.assertEqual(first["hmac_secret"], second["hmac_secret"])
            self.assertEqual(first["jwks"][0]["key"],
                             second["jwks"][0]["key"])

    def test_no_oidc_clients_means_no_block(self):
        """Deployments without any client registrations shouldn't
        advertise OIDC endpoints — file-auth only is a valid mode."""
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            AutheliaConfigGenerator(self._opts(oidc_clients=[])).write_config(out)
            cfg = self._read(out)
            oidc = (cfg.get("identity_providers") or {}).get("oidc") or {}
            # block should be present (empty clients list OK) OR
            # omitted entirely — either way no clients advertised
            self.assertFalse(oidc.get("clients") or [])


if __name__ == "__main__":
    unittest.main()
