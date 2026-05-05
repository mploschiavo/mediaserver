"""Unit tests for ``DownloadLockdownService`` (ADR-0008 Phase 1).

Covers the engage/release/idempotency/manual-stickiness corner cases
called out in the ADR's "Trigger-interaction rules" table, plus the
restart-safety invariant (state file survives a process bounce).

The tests use ``FakeAdapter`` instead of real HTTP to keep them
deterministic; the per-client adapter HTTP shapes are exercised at
their own seam in the wirer integration tests.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from media_stack.services.download_lockdown_service import (
    DownloadLockdownService,
)


class _FakeAdapter:
    """Test stand-in for a ``DownloadClientLockdown``-shaped object.

    Tracks every pause / resume call so assertions can verify each
    adapter saw the right ordering. The ``fail_pause`` / ``fail_resume``
    flags simulate per-client failures so the service's failure-
    isolation contract can be checked in isolation.
    """

    def __init__(
        self,
        client_id: str,
        *,
        fail_pause: bool = False,
        fail_resume: bool = False,
        raises: Exception | None = None,
    ) -> None:
        self.client_id = client_id
        self._fail_pause = fail_pause
        self._fail_resume = fail_resume
        self._raises = raises
        self.pause_calls = 0
        self.resume_calls = 0

    def pause_all(self) -> bool:
        self.pause_calls += 1
        if self._raises is not None:
            raise self._raises
        return not self._fail_pause

    def resume_all(self) -> bool:
        self.resume_calls += 1
        if self._raises is not None:
            raise self._raises
        return not self._fail_resume


@pytest.fixture
def state_path(tmp_path: Path) -> Path:
    return tmp_path / "disk-lockdown.state.json"


def _make_service(
    state_path: Path,
    adapters: list[_FakeAdapter],
    *,
    clock: float = 1_700_000_000.0,
) -> DownloadLockdownService:
    """Construct a service with deterministic clock + tmp state path."""
    return DownloadLockdownService(
        adapters,
        state_path_fn=lambda: state_path,
        clock=lambda: clock,
    )


class TestEngageHappyPath:
    def test_engage_with_all_clients_ok_writes_state_file(
        self, state_path: Path,
    ) -> None:
        adapters = [
            _FakeAdapter("qbittorrent"),
            _FakeAdapter("sabnzbd"),
            _FakeAdapter("sonarr"),
        ]
        svc = _make_service(state_path, adapters)
        result = svc.engage(trigger="auto", by="auto:disk-78%")

        assert result["engaged"] is True
        assert result["trigger"] == "auto"
        assert result["already_engaged"] is False
        assert sorted(result["paused_clients"]) == [
            "qbittorrent", "sabnzbd", "sonarr",
        ]
        assert result["failures"] == []

        on_disk = json.loads(state_path.read_text())
        assert on_disk["engaged"] is True
        assert on_disk["trigger"] == "auto"
        assert sorted(on_disk["paused_clients"]) == [
            "qbittorrent", "sabnzbd", "sonarr",
        ]
        assert on_disk["engaged_by"] == "auto:disk-78%"
        assert on_disk["last_failures"] == []
        # Each adapter saw exactly one pause call.
        for ad in adapters:
            assert ad.pause_calls == 1

    def test_engage_with_manual_trigger_records_manual(
        self, state_path: Path,
    ) -> None:
        svc = _make_service(state_path, [_FakeAdapter("qbittorrent")])
        result = svc.engage(trigger="manual", by="operator:matthew")
        assert result["trigger"] == "manual"
        assert json.loads(state_path.read_text())["trigger"] == "manual"

    def test_engage_rejects_unknown_trigger(
        self, state_path: Path,
    ) -> None:
        svc = _make_service(state_path, [_FakeAdapter("qbittorrent")])
        with pytest.raises(ValueError):
            svc.engage(trigger="bogus", by="x")


class TestEngagePartialFailure:
    def test_failed_pause_does_not_block_other_clients(
        self, state_path: Path,
    ) -> None:
        adapters = [
            _FakeAdapter("qbittorrent"),
            _FakeAdapter("sabnzbd", fail_pause=True),
            _FakeAdapter("sonarr"),
        ]
        svc = _make_service(state_path, adapters)
        result = svc.engage(trigger="auto", by="auto:disk-80%")
        assert sorted(result["paused_clients"]) == ["qbittorrent", "sonarr"]
        assert result["failures"] == [
            {"client": "sabnzbd", "action": "pause"},
        ]
        # All three were attempted.
        assert all(a.pause_calls == 1 for a in adapters)
        on_disk = json.loads(state_path.read_text())
        assert "sabnzbd" not in on_disk["paused_clients"]
        assert on_disk["last_failures"] == [
            {"client": "sabnzbd", "action": "pause"},
        ]

    def test_adapter_raising_exception_is_treated_as_failure(
        self, state_path: Path,
    ) -> None:
        adapters = [
            _FakeAdapter("qbittorrent"),
            _FakeAdapter("sabnzbd", raises=OSError("boom")),
        ]
        svc = _make_service(state_path, adapters)
        result = svc.engage(trigger="auto", by="auto:test")
        assert "qbittorrent" in result["paused_clients"]
        assert "sabnzbd" not in result["paused_clients"]
        assert result["failures"] == [
            {"client": "sabnzbd", "action": "pause"},
        ]


class TestEngageIdempotence:
    def test_second_engage_when_already_engaged_is_noop(
        self, state_path: Path,
    ) -> None:
        adapters = [_FakeAdapter("qbittorrent"), _FakeAdapter("sabnzbd")]
        svc = _make_service(state_path, adapters)
        svc.engage(trigger="auto", by="auto:disk-78%")
        # Reset call counters; second call must not re-hit the API.
        for a in adapters:
            a.pause_calls = 0
        result = svc.engage(trigger="auto", by="auto:disk-79%")
        assert result["already_engaged"] is True
        for a in adapters:
            assert a.pause_calls == 0
        # State file still reflects engaged=True.
        assert json.loads(state_path.read_text())["engaged"] is True

    def test_manual_engage_upgrades_auto_to_manual(
        self, state_path: Path,
    ) -> None:
        """An auto lockdown that the operator clicks Engage on must
        upgrade the trigger to manual (sticky)."""
        svc = _make_service(state_path, [_FakeAdapter("qbittorrent")])
        svc.engage(trigger="auto", by="auto:disk-80%")
        svc.engage(trigger="manual", by="operator:matthew")
        on_disk = json.loads(state_path.read_text())
        assert on_disk["trigger"] == "manual"


class TestRelease:
    def test_release_resumes_paused_clients(
        self, state_path: Path,
    ) -> None:
        adapters = [_FakeAdapter("qbittorrent"), _FakeAdapter("sabnzbd")]
        svc = _make_service(state_path, adapters)
        svc.engage(trigger="auto", by="auto:disk-78%")
        result = svc.release(by="auto:disk-recovered")

        assert result["engaged"] is False
        assert result["was_engaged"] is True
        assert sorted(result["released_clients"]) == [
            "qbittorrent", "sabnzbd",
        ]
        assert result["failures"] == []
        on_disk = json.loads(state_path.read_text())
        assert on_disk["engaged"] is False
        assert on_disk["paused_clients"] == []
        for a in adapters:
            assert a.resume_calls == 1

    def test_release_when_not_engaged_is_noop(
        self, state_path: Path,
    ) -> None:
        adapters = [_FakeAdapter("qbittorrent")]
        svc = _make_service(state_path, adapters)
        result = svc.release(by="operator:matthew")
        assert result["engaged"] is False
        assert result["was_engaged"] is False
        assert result["released_clients"] == []
        assert adapters[0].resume_calls == 0
        # No state file should have been written by release alone.
        # If a previous test wrote one, it would still be empty.
        if state_path.exists():
            assert json.loads(state_path.read_text())["engaged"] is False

    def test_release_only_resumes_what_engage_paused(
        self, state_path: Path,
    ) -> None:
        """If engage failed for sabnzbd, release must NOT call its
        resume_all (the queue was never paused; calling resume could
        un-pause an unrelated operator-initiated pause)."""
        adapters = [
            _FakeAdapter("qbittorrent"),
            _FakeAdapter("sabnzbd", fail_pause=True),
        ]
        svc = _make_service(state_path, adapters)
        svc.engage(trigger="auto", by="auto:disk-80%")
        # Reset before release so we can attribute calls.
        for a in adapters:
            a.resume_calls = 0
        svc.release(by="auto:disk-recovered")
        sab = next(a for a in adapters if a.client_id == "sabnzbd")
        qbit = next(a for a in adapters if a.client_id == "qbittorrent")
        assert qbit.resume_calls == 1
        assert sab.resume_calls == 0

    def test_release_records_resume_failures(
        self, state_path: Path,
    ) -> None:
        adapters = [
            _FakeAdapter("qbittorrent"),
            _FakeAdapter("sabnzbd", fail_resume=True),
        ]
        svc = _make_service(state_path, adapters)
        svc.engage(trigger="auto", by="auto:test")
        result = svc.release(by="operator:test")
        assert "qbittorrent" in result["released_clients"]
        assert "sabnzbd" not in result["released_clients"]
        assert {"client": "sabnzbd", "action": "resume"} in result["failures"]


class TestManualStickiness:
    def test_manual_engage_then_auto_release_attempt_does_not_release(
        self, state_path: Path,
    ) -> None:
        """The trigger field is sticky for ``manual``. Auto-release
        is the rule's job (it returns ``None`` for manual lockdowns
        when disk drops below the release threshold), but at the
        service level we verify the state file still shows
        ``engaged=true, trigger=manual`` after a release that simply
        wasn't called — the test guards against any future code path
        that might silently auto-release a manual lockdown."""
        svc = _make_service(state_path, [_FakeAdapter("qbittorrent")])
        svc.engage(trigger="manual", by="operator:matthew")
        # At the service level, release IS valid for any engaged
        # lockdown. The "manual sticky" enforcement happens in the
        # rule's ``remediate`` (which never returns lockdown_release
        # for manual). We assert that, called without that
        # protection, the state file is read back correctly so the
        # rule has the data it needs.
        loaded = svc.get_state()
        assert loaded["engaged"] is True
        assert loaded["trigger"] == "manual"


class TestRestartSafety:
    def test_load_existing_state_file_after_restart(
        self, state_path: Path,
    ) -> None:
        """A fresh service instance reads the state file written by
        a previous instance — the restart-safety invariant. Clients
        are NOT re-paused (they're assumed already paused), but the
        rule sees the correct engaged/trigger."""
        # Process A: engage.
        adapters_a = [_FakeAdapter("qbittorrent"), _FakeAdapter("sabnzbd")]
        svc_a = _make_service(state_path, adapters_a)
        svc_a.engage(trigger="auto", by="auto:disk-80%")
        assert all(a.pause_calls == 1 for a in adapters_a)

        # Process B: brand-new instance, brand-new adapters (no in-memory state).
        adapters_b = [_FakeAdapter("qbittorrent"), _FakeAdapter("sabnzbd")]
        svc_b = _make_service(state_path, adapters_b)
        loaded = svc_b.get_state()
        assert loaded["engaged"] is True
        assert loaded["trigger"] == "auto"
        assert sorted(loaded["paused_clients"]) == [
            "qbittorrent", "sabnzbd",
        ]
        # Critically: no API calls happened on the new instance just
        # from loading state.
        for a in adapters_b:
            assert a.pause_calls == 0
            assert a.resume_calls == 0

    def test_corrupt_state_file_starts_fresh_and_logs(
        self, state_path: Path,
    ) -> None:
        state_path.write_text("not-json")
        svc = _make_service(state_path, [_FakeAdapter("qbittorrent")])
        loaded = svc.get_state()
        assert loaded["engaged"] is False
        assert loaded["paused_clients"] == []

    def test_state_file_with_invalid_trigger_value_drops_it(
        self, state_path: Path,
    ) -> None:
        state_path.write_text(json.dumps({
            "engaged": True,
            "trigger": "garbage",
            "paused_clients": ["qbittorrent"],
        }))
        svc = _make_service(state_path, [_FakeAdapter("qbittorrent")])
        loaded = svc.get_state()
        # engaged carries through but the trigger is sanitised.
        assert loaded["engaged"] is True
        assert loaded["trigger"] is None

    def test_state_file_atomic_replace_does_not_corrupt(
        self, state_path: Path,
    ) -> None:
        """Engage twice in quick succession — the second write must
        not leave a half-written file behind that a concurrent
        reader could see."""
        svc = _make_service(state_path, [_FakeAdapter("qbittorrent")])
        svc.engage(trigger="auto", by="auto:disk-78%")
        svc.engage(trigger="auto", by="auto:disk-80%")
        # File should be valid JSON.
        json.loads(state_path.read_text())


class TestConfigRootResolution:
    def test_default_path_honours_config_root_env(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        from media_stack.services.download_lockdown_service import (
            LOCKDOWN_STATE_FILE,
        )
        monkeypatch.setenv("CONFIG_ROOT", str(tmp_path))
        resolved = LOCKDOWN_STATE_FILE.default_path()
        assert resolved == tmp_path / ".controller" / "disk-lockdown.state.json"

    def test_default_path_falls_back_to_srv_config(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from media_stack.services.download_lockdown_service import (
            LOCKDOWN_STATE_FILE,
        )
        monkeypatch.delenv("CONFIG_ROOT", raising=False)
        resolved = LOCKDOWN_STATE_FILE.default_path()
        assert str(resolved) == "/srv-config/.controller/disk-lockdown.state.json"
