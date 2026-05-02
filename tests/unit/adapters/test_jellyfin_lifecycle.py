"""Tests for ``JellyfinLifecycle`` — ADR-0003 Phase 2.

Pin the structural contract (Protocol conformance) and the per-method
behaviors that matter end-to-end:

  * ``probe_running`` distinguishes ``ok`` (200) from ``failed`` (non-
    200 / HTTPError) from ``unknown`` (network error / timeout). The
    tri-state matters because the orchestrator treats ``unknown`` as
    "retry next tick" but logs it differently for operators.
  * ``discover_api_key`` honours the env-var short-circuit before
    touching the SQLite db. Saves a useless db read on every probe.
  * ``mint_api_key`` is idempotent — if the key is already
    discoverable, return ``Outcome.success`` with ``attempts=0``
    instead of calling ``http_preflight``.
  * ``persist_api_key`` writes the env var even if the k8s secret
    patch fails (env-only is enough for the running process; secret
    failure is transient and retryable).

Tests don't hit the network or the file-system; the ``http_preflight``
and ``read_jellyfin_api_key_from_db`` calls are stubbed via
``unittest.mock.patch``.
"""

from __future__ import annotations

import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from media_stack.adapters.jellyfin.lifecycle import JellyfinLifecycle
from media_stack.domain.services import (
    OrchestrationContext,
    Outcome,
    ProbeResult,
    ServiceLifecycle,
)


@pytest.fixture(autouse=True)
def _clear_jellyfin_api_key_env():
    """Production ``persist_api_key`` writes ``JELLYFIN_API_KEY`` into
    ``os.environ``. ``monkeypatch.delenv`` at the start of a test only
    clears any pre-existing value; the post-call write would otherwise
    leak into unrelated tests (e.g. test_bootstrap_secret_priming
    seeds its own ``jellyfin-key`` and reads back ``new-key`` from
    here). Always delete after each test so the slot is clean."""
    import os as _os
    yield
    _os.environ.pop("JELLYFIN_API_KEY", None)


def _ctx(**overrides) -> OrchestrationContext:
    cfg = {
        "host": "jellyfin",
        "port": 8096,
        "scheme": "http",
        "health_path": "/System/Info/Public",
        "api_key_env": "JELLYFIN_API_KEY",
        "api_key_db_path": "jellyfin/data/jellyfin.db",
        "api_key_name_preference": ["Jellyfin", "Jellyseerr"],
    }
    cfg.update(overrides.pop("config", {}))
    return OrchestrationContext(
        service_id="jellyfin",
        config=cfg,
        secrets=overrides.pop("secrets", {}),
        now=overrides.pop("now", lambda: 1700000000.0),
        **overrides,
    )


class TestProtocolConformance:
    def test_isinstance_passes(self) -> None:
        # Pinned in the lifecycle module's import-time `_check`, but
        # also verified here so failures surface in the test report
        # rather than as an ImportError.
        assert isinstance(JellyfinLifecycle(), ServiceLifecycle)


class TestProbeRunning:
    @patch("urllib.request.urlopen")
    def test_ok_on_http_200(self, mock_open: MagicMock) -> None:
        resp = MagicMock()
        resp.status = 200
        resp.__enter__ = lambda self_: self_
        resp.__exit__ = lambda *_: None
        mock_open.return_value = resp

        r = JellyfinLifecycle().probe_running(_ctx())
        assert r.is_ok
        assert r.evidence["http_status"] == 200
        assert r.evidence["url"] == "http://jellyfin:8096/System/Info/Public"

    @patch("urllib.request.urlopen")
    def test_failed_on_http_error(self, mock_open: MagicMock) -> None:
        # HTTPError is "verifiably broken" — the service answered, just
        # not happily. Distinct from "couldn't tell".
        mock_open.side_effect = urllib.error.HTTPError(
            url="x", code=503, msg="warming up", hdrs=None, fp=None,
        )
        r = JellyfinLifecycle().probe_running(_ctx())
        assert r.status == "failed"
        assert r.evidence["http_status"] == 503

    @patch("urllib.request.urlopen")
    def test_unknown_on_network_error(self, mock_open: MagicMock) -> None:
        # URLError, OSError, TimeoutError → unknown, NOT failed. The
        # operator can tell "we know it's broken" from "we couldn't
        # ask" by reading the status field.
        mock_open.side_effect = urllib.error.URLError("Name or service not known")
        r = JellyfinLifecycle().probe_running(_ctx())
        assert r.status == "unknown"

    def test_failed_when_host_or_port_missing(self) -> None:
        ctx = _ctx(config={"host": "", "port": None})
        r = JellyfinLifecycle().probe_running(ctx)
        assert r.status == "failed"
        assert "no host/port" in r.detail


class TestDiscoverApiKey:
    def test_env_var_short_circuits_db_read(self, monkeypatch) -> None:
        # Pre-condition: env has the key. The lifecycle MUST NOT call
        # into the SQLite reader at all in this path.
        monkeypatch.setenv("JELLYFIN_API_KEY", "from-env-1234")
        with patch(
            "media_stack.infrastructure.jellyfin.api_key_db.read_jellyfin_api_key_from_db",
        ) as mock_db:
            result = JellyfinLifecycle().discover_api_key(_ctx())
        assert result == "from-env-1234"
        mock_db.assert_not_called()

    def test_secrets_dict_short_circuits_db_read(self) -> None:
        # Same shape — but the key comes from ctx.secrets, not the
        # process env. The orchestrator pre-resolves and passes via
        # context to keep the lifecycle pure.
        with patch(
            "media_stack.infrastructure.jellyfin.api_key_db.read_jellyfin_api_key_from_db",
        ) as mock_db:
            result = JellyfinLifecycle().discover_api_key(
                _ctx(secrets={"JELLYFIN_API_KEY": "from-secrets-9876"}),
            )
        assert result == "from-secrets-9876"
        mock_db.assert_not_called()

    def test_db_read_when_env_empty(self, monkeypatch) -> None:
        monkeypatch.delenv("JELLYFIN_API_KEY", raising=False)
        with patch(
            "media_stack.infrastructure.jellyfin.api_key_db.read_jellyfin_api_key_from_db",
            return_value=("token-from-db", "Jellyfin"),
        ):
            result = JellyfinLifecycle().discover_api_key(_ctx())
        assert result == "token-from-db"

    def test_returns_none_when_db_read_raises(self, monkeypatch) -> None:
        # The canonical reader raises RuntimeError when the DB isn't
        # there yet, the table is empty, etc. The lifecycle MUST treat
        # that as "key not discoverable right now", not surface the
        # error — the orchestrator decides whether to mint.
        monkeypatch.delenv("JELLYFIN_API_KEY", raising=False)
        with patch(
            "media_stack.infrastructure.jellyfin.api_key_db.read_jellyfin_api_key_from_db",
            side_effect=RuntimeError("no such file"),
        ):
            result = JellyfinLifecycle().discover_api_key(_ctx())
        assert result is None


class TestProbeHasApiKey:
    def test_ok_when_key_discoverable(self, monkeypatch) -> None:
        monkeypatch.setenv("JELLYFIN_API_KEY", "abc123")
        r = JellyfinLifecycle().probe_has_api_key(_ctx())
        assert r.is_ok
        assert r.evidence["key_length"] == 6
        assert r.evidence["source"] == "env"

    def test_failed_when_no_key(self, monkeypatch) -> None:
        monkeypatch.delenv("JELLYFIN_API_KEY", raising=False)
        with patch(
            "media_stack.infrastructure.jellyfin.api_key_db.read_jellyfin_api_key_from_db",
            side_effect=RuntimeError("empty"),
        ):
            r = JellyfinLifecycle().probe_has_api_key(_ctx())
        assert r.status == "failed"
        assert r.evidence["env_var_checked"] == "JELLYFIN_API_KEY"


class TestMintApiKey:
    def test_idempotent_short_circuit_when_already_discoverable(
        self, monkeypatch,
    ) -> None:
        # If the key is already there, the lifecycle MUST NOT call
        # http_preflight. The whole point of the idempotent contract.
        monkeypatch.setenv("JELLYFIN_API_KEY", "existing-key-zzz")
        with patch(
            "media_stack.infrastructure.jellyfin.http_preflight.run_preflight",
        ) as mock_preflight:
            outcome = JellyfinLifecycle().mint_api_key(_ctx())
        assert outcome.ok
        assert outcome.value == "existing-key-zzz"
        assert outcome.attempts == 0
        assert outcome.evidence["reason"] == "already_discoverable"
        mock_preflight.assert_not_called()

    def test_mints_via_http_preflight_when_missing(self, monkeypatch) -> None:
        monkeypatch.delenv("JELLYFIN_API_KEY", raising=False)
        with patch(
            "media_stack.infrastructure.jellyfin.api_key_db.read_jellyfin_api_key_from_db",
            side_effect=RuntimeError("no db"),
        ), patch(
            "media_stack.infrastructure.jellyfin.http_preflight.run_preflight",
            return_value={
                "JELLYFIN_API_KEY": "minted-token-abc",
                "JELLYFIN_USER_ID": "user-uuid-1",
            },
        ) as mock_preflight:
            outcome = JellyfinLifecycle().mint_api_key(_ctx())
        assert outcome.ok
        assert outcome.value == "minted-token-abc"
        assert outcome.attempts == 1
        mock_preflight.assert_called_once()

    def test_transient_failure_when_preflight_returns_no_key(
        self, monkeypatch,
    ) -> None:
        # Preflight returned but didn't yield a key — operator-visible
        # but probably retryable on next tick (warmup race).
        monkeypatch.delenv("JELLYFIN_API_KEY", raising=False)
        with patch(
            "media_stack.infrastructure.jellyfin.api_key_db.read_jellyfin_api_key_from_db",
            side_effect=RuntimeError("no db"),
        ), patch(
            "media_stack.infrastructure.jellyfin.http_preflight.run_preflight",
            return_value={},
        ):
            outcome = JellyfinLifecycle().mint_api_key(_ctx())
        assert not outcome.ok
        assert outcome.transient is True
        assert "without an api key" in outcome.error.lower()

    def test_transient_failure_when_preflight_raises(
        self, monkeypatch,
    ) -> None:
        monkeypatch.delenv("JELLYFIN_API_KEY", raising=False)
        with patch(
            "media_stack.infrastructure.jellyfin.api_key_db.read_jellyfin_api_key_from_db",
            side_effect=RuntimeError("no db"),
        ), patch(
            "media_stack.infrastructure.jellyfin.http_preflight.run_preflight",
            side_effect=RuntimeError("connection refused"),
        ):
            outcome = JellyfinLifecycle().mint_api_key(_ctx())
        assert not outcome.ok
        assert outcome.transient is True
        assert "connection refused" in outcome.error

    def test_non_transient_when_config_lacks_host(
        self, monkeypatch,
    ) -> None:
        # No host = no minting endpoint. Permanent until config is
        # fixed; orchestrator should NOT retry.
        monkeypatch.delenv("JELLYFIN_API_KEY", raising=False)
        ctx = _ctx(config={"host": "", "port": None})
        with patch(
            "media_stack.infrastructure.jellyfin.api_key_db.read_jellyfin_api_key_from_db",
            side_effect=RuntimeError("no db"),
        ):
            outcome = JellyfinLifecycle().mint_api_key(ctx)
        assert not outcome.ok
        assert outcome.transient is False


class TestPersistApiKey:
    def test_refuses_empty_key(self) -> None:
        outcome = JellyfinLifecycle().persist_api_key("", _ctx())
        assert not outcome.ok
        assert outcome.transient is False

    def test_writes_env_var(self, monkeypatch) -> None:
        monkeypatch.delenv("JELLYFIN_API_KEY", raising=False)
        with patch(
            "media_stack.services.apps.core.job_adapters._persist_preflight_keys_to_secret_safe",
            return_value={"status": "ok"},
        ), patch(
            "media_stack.services.apps.core.job_adapters._stub_state",
            return_value=object(),
        ):
            outcome = JellyfinLifecycle().persist_api_key("new-key", _ctx())
        import os
        assert outcome.ok
        assert os.environ.get("JELLYFIN_API_KEY") == "new-key"
        assert outcome.evidence["env_written"] == "JELLYFIN_API_KEY"

    def test_transient_failure_when_secret_patch_fails(
        self, monkeypatch,
    ) -> None:
        # Env is still written (the running process gets the key);
        # the secret patch failure is a separate retryable concern.
        monkeypatch.delenv("JELLYFIN_API_KEY", raising=False)
        with patch(
            "media_stack.services.apps.core.job_adapters._persist_preflight_keys_to_secret_safe",
            side_effect=RuntimeError("kubectl unauthorized"),
        ), patch(
            "media_stack.services.apps.core.job_adapters._stub_state",
            return_value=object(),
        ):
            outcome = JellyfinLifecycle().persist_api_key("k", _ctx())
        import os
        assert os.environ.get("JELLYFIN_API_KEY") == "k", (
            "env MUST be written even when secret patch fails"
        )
        assert not outcome.ok
        assert outcome.transient is True
