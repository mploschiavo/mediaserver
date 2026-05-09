"""Tests for ``application.jellyfin.ensure_api_key``.

The handler is the first concrete promise-style ensurer in the
codebase (Phase 0 of ADR-0003). Its contract:

  1. Idempotent — if the key is already discoverable, return
     ``skipped: already_minted`` without calling the preflight.
  2. Service-aware — if Jellyfin is unreachable, return
     ``skipped: service_not_ready`` (the auto-heal cycle retries).
  3. Mint path — when the probe says we need a key and Jellyfin is
     reachable, call the canonical http_preflight, persist the
     result into ``os.environ``, and return ``status: minted``.
  4. Failure surfaces — preflight returning without a key raises,
     so JobRunner records terminal status ``error`` (operator
     signal, not silent gap).
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from media_stack.application.jellyfin.ensure_api_key import (
    ensure_jellyfin_api_key,
)


class _StubCtx:
    """Minimal JobContext stand-in. The handler doesn't read from
    ctx; it consults the live registry + env directly."""


@pytest.fixture(autouse=True)
def _scrub_env():
    """Don't let host env leak ``JELLYFIN_API_KEY`` into tests."""
    saved = os.environ.pop("JELLYFIN_API_KEY", None)
    saved_uid = os.environ.pop("JELLYFIN_USER_ID", None)
    yield
    if saved is not None:
        os.environ["JELLYFIN_API_KEY"] = saved
    elif "JELLYFIN_API_KEY" in os.environ:
        del os.environ["JELLYFIN_API_KEY"]
    if saved_uid is not None:
        os.environ["JELLYFIN_USER_ID"] = saved_uid
    elif "JELLYFIN_USER_ID" in os.environ:
        del os.environ["JELLYFIN_USER_ID"]


def test_skips_when_key_already_discovered() -> None:
    """The handler MUST short-circuit without invoking the preflight
    when ``discover_api_keys()`` already returns a jellyfin entry."""
    with patch(
        "media_stack.api.services.health.discover_api_keys",
        return_value={"jellyfin": "abc123"},
    ), patch(
        "media_stack.infrastructure.jellyfin.http_preflight.run_preflight",
    ) as mock_preflight:
        result = ensure_jellyfin_api_key(_StubCtx())
    assert result["skipped"] == "already_minted"
    assert result["key_length"] == 6
    mock_preflight.assert_not_called()


def test_skips_when_jellyfin_not_reachable() -> None:
    """Don't enter the mint flow if Jellyfin's public endpoint is
    unresponsive — the auto-heal cycle will retry next tick."""
    with patch(
        "media_stack.api.services.health.discover_api_keys",
        return_value={},
    ), patch(
        "media_stack.core.service_registry.registry.service_internal_url",
        return_value="http://jellyfin:8096",
    ), patch(
        "media_stack.application.jellyfin.ensure_api_key._jellyfin_reachable",
        return_value=False,
    ), patch(
        "media_stack.infrastructure.jellyfin.http_preflight.run_preflight",
    ) as mock_preflight:
        result = ensure_jellyfin_api_key(_StubCtx())
    assert result["skipped"] == "service_not_ready"
    assert "jellyfin" in result["url"]
    mock_preflight.assert_not_called()


def test_mints_persists_and_returns_status_when_jellyfin_ready() -> None:
    """Happy path: discover empty + jellyfin reachable + preflight
    returns a key → handler persists it to env and reports
    ``status: minted``."""
    with patch(
        "media_stack.api.services.health.discover_api_keys",
        return_value={},
    ), patch(
        "media_stack.core.service_registry.registry.service_internal_url",
        return_value="http://jellyfin:8096",
    ), patch(
        "media_stack.application.jellyfin.ensure_api_key._jellyfin_reachable",
        return_value=True,
    ), patch(
        "media_stack.infrastructure.jellyfin.http_preflight.run_preflight",
        return_value={
            "JELLYFIN_API_KEY": "freshly-minted-token-xyz",
            "JELLYFIN_USER_ID": "user-uuid-1",
        },
    ), patch(
        "media_stack.application.jellyfin.ensure_api_key._persist_to_secret_if_possible",
        return_value="ok",
    ), patch(
        "media_stack.application.jellyfin.ensure_api_key._bust_runtime_keys_cache",
    ):
        result = ensure_jellyfin_api_key(_StubCtx())
    assert result["status"] == "minted"
    assert result["key_length"] == len("freshly-minted-token-xyz")
    assert os.environ["JELLYFIN_API_KEY"] == "freshly-minted-token-xyz"
    assert os.environ["JELLYFIN_USER_ID"] == "user-uuid-1"


def test_raises_when_preflight_returns_no_key() -> None:
    """A preflight that returns without a JELLYFIN_API_KEY value is a
    real bug — propagate so JobRunner records a terminal ``error``
    run. Operators see this in /api/runs and the dashboard's RunDrawer."""
    with patch(
        "media_stack.api.services.health.discover_api_keys",
        return_value={},
    ), patch(
        "media_stack.core.service_registry.registry.service_internal_url",
        return_value="http://jellyfin:8096",
    ), patch(
        "media_stack.application.jellyfin.ensure_api_key._jellyfin_reachable",
        return_value=True,
    ), patch(
        "media_stack.infrastructure.jellyfin.http_preflight.run_preflight",
        return_value={"JELLYFIN_USER_ID": "user-uuid-1"},  # no key!
    ):
        with pytest.raises(RuntimeError, match="without an API key"):
            ensure_jellyfin_api_key(_StubCtx())


def test_persist_failure_does_not_raise() -> None:
    """Best-effort secret persist: if the K8s patch fails (RBAC, no
    cluster, etc.), the env-var update alone is enough for the
    running process. Don't fail the handler."""
    with patch(
        "media_stack.api.services.health.discover_api_keys",
        return_value={},
    ), patch(
        "media_stack.core.service_registry.registry.service_internal_url",
        return_value="http://jellyfin:8096",
    ), patch(
        "media_stack.application.jellyfin.ensure_api_key._jellyfin_reachable",
        return_value=True,
    ), patch(
        "media_stack.infrastructure.jellyfin.http_preflight.run_preflight",
        return_value={"JELLYFIN_API_KEY": "tok"},
    ), patch(
        "media_stack.application.jellyfin.ensure_api_key._persist_to_secret_if_possible",
        return_value="persist_skipped: no kube client",
    ), patch(
        "media_stack.application.jellyfin.ensure_api_key._bust_runtime_keys_cache",
    ):
        result = ensure_jellyfin_api_key(_StubCtx())
    assert result["status"] == "minted"
    assert "persist_skipped" in result["persist"]
