"""Tests for the compose-deploy qBittorrent preflight shim.

ADR-0013 Phase 3b moved the rotation body to
``QbittorrentLifecycle.ensure_credentials``; the shim's job is now
narrow:

* Resolve STACK_ADMIN_* defaults from compose_env + namespace.
* Write them to the compose ``.env`` file.
* Build an ``OrchestrationContext`` with a ``ComposeContainerAccess``
  extra and call the lifecycle.
* Translate the lifecycle's typed ``Outcome`` failure into the
  legacy ``RuntimeError`` shape callers expect.

The pre-2026-05-11 tests in this file patched a flock of static
helpers (``_set_credentials_with_container``, ``_wait_for_login``,
``_read_temporary_password``, ``_reset_auth_config_in_container``,
``_restart_container``) that were dead code post Phase 3b — kept as
module aliases only for these tests to patch. Now that the helpers
are removed, the tests are reshaped around the live shim shape:
inject a mock ``QbittorrentLifecycle`` instead of patching deleted
implementation details, and assert on the OrchestrationContext the
shim hands it.
"""
from __future__ import annotations

import tempfile
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.domain.services import Outcome  # noqa: E402
from media_stack.infrastructure.qbittorrent.compose_preflight import (  # noqa: E402
    ComposeEnvFileWriter,
    QbittorrentComposePreflight,
    ensure_compose_torrent_client_credentials,
)


class ComposeEnvFileWriterTests(unittest.TestCase):
    """Upsert semantics for ``KEY=VALUE`` rows in the compose .env."""

    def test_appends_new_keys_when_file_did_not_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "subdir" / ".env"
            ComposeEnvFileWriter(path).upsert({
                "A": "alpha",
                "B": "bravo",
            })
            payload = path.read_text(encoding="utf-8")
        self.assertIn("A=alpha", payload)
        self.assertIn("B=bravo", payload)

    def test_replaces_existing_key_in_place(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text(
                "TZ=America/Chicago\nA=old\n# a comment\n",
                encoding="utf-8",
            )
            ComposeEnvFileWriter(path).upsert({"A": "new"})
            payload = path.read_text(encoding="utf-8")
        self.assertIn("A=new", payload)
        self.assertNotIn("A=old", payload)
        self.assertIn("TZ=America/Chicago", payload)
        self.assertIn("# a comment", payload)

    def test_upsert_of_empty_dict_is_a_no_op(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("TZ=UTC\n", encoding="utf-8")
            ComposeEnvFileWriter(path).upsert({})
            payload = path.read_text(encoding="utf-8")
        self.assertEqual(payload, "TZ=UTC\n")


def _stub_lifecycle(outcome: Outcome) -> mock.Mock:
    lifecycle = mock.Mock()
    lifecycle.ensure_credentials.return_value = outcome
    return lifecycle


class QbittorrentComposePreflightTests(unittest.TestCase):

    def test_resolves_default_stack_admin_when_compose_env_blank(self) -> None:
        """Username defaults to ``admin``; password defaults to the
        namespace. Mirrors the v1.0.0 contract for fresh installs that
        don't set STACK_ADMIN_*."""
        docker = mock.Mock()
        docker.get_container.return_value = None  # qB not up yet
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text("TZ=America/Chicago\n", encoding="utf-8")
            env: dict[str, str] = {"TZ": "America/Chicago"}

            result = QbittorrentComposePreflight().ensure_compose_torrent_client_credentials(
                compose_env=env,
                compose_env_file=env_file,
                namespace="media-dev",
                docker=docker,
                info=mock.Mock(),
            )

            payload = env_file.read_text(encoding="utf-8")
        self.assertEqual(result["STACK_ADMIN_USERNAME"], "admin")
        self.assertEqual(result["STACK_ADMIN_PASSWORD"], "media-dev")
        self.assertEqual(env["STACK_ADMIN_USERNAME"], "admin")
        self.assertEqual(env["STACK_ADMIN_PASSWORD"], "media-dev")
        self.assertIn("STACK_ADMIN_USERNAME=admin", payload)
        self.assertIn("STACK_ADMIN_PASSWORD=media-dev", payload)

    def test_skips_lifecycle_when_container_not_yet_up(self) -> None:
        """``compose up`` not done with qB yet → return stack creds,
        don't call the lifecycle (orchestrator picks it up on first
        post-up reconcile tick)."""
        docker = mock.Mock()
        docker.get_container.return_value = None
        lifecycle = _stub_lifecycle(Outcome.success())

        result = QbittorrentComposePreflight(lifecycle=lifecycle).ensure_compose_torrent_client_credentials(
            compose_env={
                "STACK_ADMIN_USERNAME": "alice",
                "STACK_ADMIN_PASSWORD": "pw-1",
            },
            compose_env_file=None,
            namespace="media-stack",
            docker=docker,
            info=mock.Mock(),
        )

        self.assertEqual(result["STACK_ADMIN_USERNAME"], "alice")
        self.assertEqual(result["STACK_ADMIN_PASSWORD"], "pw-1")
        lifecycle.ensure_credentials.assert_not_called()

    def test_dispatches_to_lifecycle_with_container_access(self) -> None:
        """Container is up → build an OrchestrationContext with
        ComposeContainerAccess + stack-admin secrets and call
        lifecycle.ensure_credentials."""
        container_handle = mock.Mock()
        docker = mock.Mock()
        docker.get_container.return_value = container_handle
        lifecycle = _stub_lifecycle(Outcome.success(evidence={"rotated": True}))

        QbittorrentComposePreflight(lifecycle=lifecycle).ensure_compose_torrent_client_credentials(
            compose_env={
                "STACK_ADMIN_USERNAME": "ops",
                "STACK_ADMIN_PASSWORD": "secret",
            },
            compose_env_file=None,
            namespace="media-stack",
            docker=docker,
            info=mock.Mock(),
        )

        lifecycle.ensure_credentials.assert_called_once()
        ctx = lifecycle.ensure_credentials.call_args.args[0]
        self.assertEqual(ctx.service_id, "qbittorrent")
        self.assertEqual(ctx.secrets["STACK_ADMIN_USERNAME"], "ops")
        self.assertEqual(ctx.secrets["STACK_ADMIN_PASSWORD"], "secret")
        # ``container_access`` is the ComposeContainerAccess wrapping
        # the docker-py handle. We just assert the docker handle is
        # reachable through it — the impl is tested in its own suite.
        self.assertIsNotNone(ctx.extra.get("container_access"))

    def test_failure_outcome_translates_to_runtime_error_with_phase_tag(self) -> None:
        """Lifecycle returning ``failure`` must surface as a
        RuntimeError mentioning the rotation phase (``verify`` /
        ``set_preferences`` / ``rotate``) so operators trace it."""
        docker = mock.Mock()
        docker.get_container.return_value = mock.Mock()
        lifecycle = _stub_lifecycle(
            Outcome.failure(
                "qBittorrent rejected the password rotation request",
                transient=False,
                evidence={"phase": "set_preferences"},
            ),
        )

        with self.assertRaises(RuntimeError) as cm:
            QbittorrentComposePreflight(lifecycle=lifecycle).ensure_compose_torrent_client_credentials(
                compose_env={},
                compose_env_file=None,
                namespace="media-stack",
                docker=docker,
                info=mock.Mock(),
            )
        self.assertIn("set_preferences", str(cm.exception))
        self.assertIn("rejected the password rotation", str(cm.exception))

    def test_module_level_shim_dispatches_to_singleton(self) -> None:
        """The contract YAML's ``compose_preflight_handler`` resolves
        to the module-level alias. Confirm the alias still routes to
        a working instance after the Phase 3b cleanup."""
        docker = mock.Mock()
        docker.get_container.return_value = None
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            result = ensure_compose_torrent_client_credentials(
                compose_env={},
                compose_env_file=env_file,
                namespace="media-dev",
                docker=docker,
                info=mock.Mock(),
            )
        self.assertEqual(result["STACK_ADMIN_USERNAME"], "admin")


if __name__ == "__main__":
    unittest.main()
