"""Integration tests that talk to a real *arr. Requires env vars:
  INTEGRATION_RADARR_URL, INTEGRATION_RADARR_API_KEY
  INTEGRATION_SONARR_URL, INTEGRATION_SONARR_API_KEY
  INTEGRATION_BAZARR_URL, INTEGRATION_BAZARR_API_KEY

Run with:
  pytest tests/integration/test_media_integrity_real_servarr.py -m integration

Do not enable these against production instances.

Each test is gated on its required env vars via ``@pytest.mark.skipif``
so a developer without a real *arr running sees clean skips. The
file must collect cleanly (parse + import) even when no env vars are
set — the imports below use ``importorskip`` for any module that
might genuinely be missing in a stripped-down dev install.
"""

from __future__ import annotations

import os
from typing import Any

import pytest


# Adapters live behind the standard package path; if the package
# isn't installed in this environment, skip the entire module rather
# than fail collection.
_adapters = pytest.importorskip(
    "media_stack.services.media_integrity.adapters",
    reason="media_stack.services.media_integrity.adapters not importable",
)
_policy_mod = pytest.importorskip(
    "media_stack.services.media_integrity.policy",
    reason="media_stack.services.media_integrity.policy not importable",
)
_subtitle_reconciler_mod = pytest.importorskip(
    "media_stack.services.media_integrity.subtitle_reconciler",
    reason="bazarr settings enforcer not importable",
)
_enforcer_mod = pytest.importorskip(
    "media_stack.services.media_integrity.enforcer",
    reason="servarr config enforcer not importable",
)
_arr_protocol_mod = pytest.importorskip(
    "media_stack.services.media_integrity.arr_protocol",
    reason="arr protocol not importable",
)


RadarrAdapter = _adapters.RadarrAdapter
SonarrAdapter = _adapters.SonarrAdapter
BazarrAdapter = _adapters.BazarrAdapter
ServarrPolicy = _policy_mod.ServarrPolicy
ServarrConfigEnforcer = _enforcer_mod.ServarrConfigEnforcer
BazarrSettingsEnforcer = _subtitle_reconciler_mod.BazarrSettingsEnforcer
MediaRelease = _arr_protocol_mod.MediaRelease


# ---------------------------------------------------------------------------
# Env-var gates
# ---------------------------------------------------------------------------


_RADARR_URL = os.environ.get("INTEGRATION_RADARR_URL", "")
_RADARR_KEY = os.environ.get("INTEGRATION_RADARR_API_KEY", "")
_SONARR_URL = os.environ.get("INTEGRATION_SONARR_URL", "")
_SONARR_KEY = os.environ.get("INTEGRATION_SONARR_API_KEY", "")
_BAZARR_URL = os.environ.get("INTEGRATION_BAZARR_URL", "")
_BAZARR_KEY = os.environ.get("INTEGRATION_BAZARR_API_KEY", "")


_radarr_skip = pytest.mark.skipif(
    not (_RADARR_URL and _RADARR_KEY),
    reason="INTEGRATION_RADARR_URL + INTEGRATION_RADARR_API_KEY not set",
)
_sonarr_skip = pytest.mark.skipif(
    not (_SONARR_URL and _SONARR_KEY),
    reason="INTEGRATION_SONARR_URL + INTEGRATION_SONARR_API_KEY not set",
)
_bazarr_skip = pytest.mark.skipif(
    not (_BAZARR_URL and _BAZARR_KEY),
    reason="INTEGRATION_BAZARR_URL + INTEGRATION_BAZARR_API_KEY not set",
)


# ---------------------------------------------------------------------------
# Tests — every one is gated.
# ---------------------------------------------------------------------------


@pytest.mark.integration
@_radarr_skip
def test_integration_radarr_enforce_and_revert() -> None:
    """Round-trip enforce against a real Radarr.

    1. GET current /config/mediamanagement.
    2. Flip ``copyUsingHardlinks`` to the OPPOSITE of policy
       (policy is ``True``, so we set it to ``False``).
    3. Run the enforcer. Verify the field was flipped back to True.
    4. Restore the original blob unconditionally so the test leaves
       no residual state — even if assertions fail mid-test, the
       finally block restores.
    """
    adapter = RadarrAdapter(
        base_url=_RADARR_URL,
        api_key=_RADARR_KEY,
        media_root="/media/movies",
    )
    original = adapter.get_media_management()
    drifted = dict(original)
    drifted["copyUsingHardlinks"] = not original.get("copyUsingHardlinks", True)
    try:
        adapter.put_media_management(drifted)
        # Confirm drift is in place before enforcing.
        check_drift = adapter.get_media_management()
        assert (
            check_drift["copyUsingHardlinks"] != original.get("copyUsingHardlinks", True)
        ), "pre-condition: drifted PUT should have stuck"

        enforcer = ServarrConfigEnforcer(policy=ServarrPolicy())
        report = enforcer.apply([adapter])
        assert report.total_failures == 0, report

        after = adapter.get_media_management()
        assert after["copyUsingHardlinks"] is True, "policy should pull it back to True"
    finally:
        # Always restore the original config — even on assertion fail.
        adapter.put_media_management(original)


@pytest.mark.integration
@_radarr_skip
def test_integration_radarr_list_releases_returns_shape() -> None:
    """Smoke: real Radarr returns a list[MediaRelease]; every entry
    has string id/title/path. Doesn't assert specific content because
    the real instance's library is operator-dependent."""
    adapter = RadarrAdapter(
        base_url=_RADARR_URL,
        api_key=_RADARR_KEY,
        media_root="/media/movies",
    )
    releases = adapter.list_releases()
    assert isinstance(releases, list)
    for r in releases:
        assert isinstance(r, MediaRelease)
        assert isinstance(r.id, str) and r.id
        assert isinstance(r.title, str)
        assert isinstance(r.path, str)


@pytest.mark.integration
@_sonarr_skip
def test_integration_sonarr_list_releases_flattens_to_episodes() -> None:
    """Sonarr's adapter must flatten series → season → episode so
    each row in ``list_releases()`` is a unique episode. We verify
    every id is unique (no duplicate rows) and is a stringified
    integer-like id."""
    adapter = SonarrAdapter(
        base_url=_SONARR_URL,
        api_key=_SONARR_KEY,
        media_root="/media/tv",
    )
    releases = adapter.list_releases()
    assert isinstance(releases, list)
    ids = [r.id for r in releases]
    assert len(ids) == len(set(ids)), "episode ids must be unique"
    for r in releases:
        assert isinstance(r, MediaRelease)
        assert isinstance(r.id, str) and r.id


@pytest.mark.integration
@_bazarr_skip
def test_integration_bazarr_settings_round_trip() -> None:
    """Round-trip enforce against a real Bazarr.

    1. GET /api/system/settings.
    2. Run the BazarrSettingsEnforcer with the canonical policy.
    3. Confirm policy-managed fields match policy after enforce.
    4. Restore the original blob.

    The test doesn't assert pre-state (operator may already be
    compliant); the assertion is post-enforce + clean restore.
    """
    adapter = BazarrAdapter(
        base_url=_BAZARR_URL,
        api_key=_BAZARR_KEY,
    )
    original = adapter.get_settings()
    try:
        enforcer = BazarrSettingsEnforcer(policy=ServarrPolicy())
        report = enforcer.apply(adapter)
        assert report.failures == (), report

        after = adapter.get_settings()
        general: dict[str, Any] = after.get("general", {}) if isinstance(
            after.get("general"), dict
        ) else {}
        # Canonical policy demands these are True.
        assert general.get("upgrade_subs") is True
    finally:
        adapter.put_settings(original)
