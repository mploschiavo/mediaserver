"""Ratchet #9 — boot smoke test.

Brings up a stripped-down stack via docker compose, polls
``/api/jobs`` for up to five minutes, and asserts that
``discover-api-keys`` lands in ``status: ok`` (NOT error) and
that the in-container env has non-empty ``*_API_KEY`` values for
at least Jellyfin + Sonarr.

Opt-in only — run with ``pytest -m smoke``. Operators are
expected to run it locally before pushing controller changes
that touch boot or key discovery; CI does not run it on every
PR (would need a much heavier runner).

If you're reading this and ``compose.smoke.yml`` is still a
sketch, see the docstring of that file for the service list it
needs.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import pytest


COMPOSE_FILE = Path(__file__).resolve().parent / "compose.smoke.yml"
CONTROLLER_URL = os.environ.get(
    "MEDIA_STACK_SMOKE_URL", "http://127.0.0.1:8080",
)
CONTROLLER_CONTAINER = os.environ.get(
    "MEDIA_STACK_SMOKE_CONTAINER", "controller",
)
TIMEOUT_SECONDS = 5 * 60
POLL_INTERVAL_SECONDS = 5


def _have_docker() -> bool:
    return bool(shutil.which("docker"))


def _compose_up() -> None:
    subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "up", "-d"],
        check=True,
    )


def _compose_down() -> None:
    subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "down", "-v"],
        check=False,
    )


def _poll_jobs() -> dict | None:
    """Hit ``/api/jobs`` and return the parsed payload, or ``None``
    on transient failure."""
    try:
        with urllib.request.urlopen(
            f"{CONTROLLER_URL}/api/jobs", timeout=5,
        ) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, ConnectionError, OSError, ValueError):
        return None


def _job_state(payload: dict, name: str) -> str | None:
    """Extract the most recent status of ``name`` from a ``/api/jobs``
    payload."""
    if not isinstance(payload, dict):
        return None
    history = payload.get("history") or []
    for batch in history:
        for item in (batch.get("results") or []):
            if str(item.get("name") or "") == name:
                return str(item.get("status") or "")
    # Some controller versions surface the latest under ``current``.
    current = payload.get("current") or {}
    if str(current.get("name") or "") == name:
        return str(current.get("status") or "")
    return None


def _container_env(container: str) -> dict[str, str]:
    """Return the environment of a running container as a dict."""
    proc = subprocess.run(
        ["docker", "exec", container, "env"],
        check=True, capture_output=True, text=True,
    )
    out: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k] = v
    return out


@pytest.mark.smoke
class BootSmokeTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        if not _have_docker():
            raise unittest.SkipTest("docker not available")
        if not COMPOSE_FILE.is_file():
            raise unittest.SkipTest(
                f"smoke compose file missing at {COMPOSE_FILE} — see "
                "its docstring for the service sketch"
            )
        _compose_up()

    @classmethod
    def tearDownClass(cls) -> None:
        _compose_down()

    def test_discover_api_keys_lands_ok_within_window(self) -> None:
        deadline = time.time() + TIMEOUT_SECONDS
        last_state: str | None = None
        last_payload: dict | None = None
        while time.time() < deadline:
            payload = _poll_jobs()
            if payload:
                last_payload = payload
                state = _job_state(payload, "discover-api-keys")
                if state:
                    last_state = state
                    if state == "ok":
                        break
                    if state == "error":
                        # Keep polling — a transient error early in
                        # boot can flip to ok on the next pass.
                        pass
            time.sleep(POLL_INTERVAL_SECONDS)
        self.assertEqual(
            last_state, "ok",
            f"discover-api-keys never reached status=ok in "
            f"{TIMEOUT_SECONDS}s. last_state={last_state!r}, "
            f"last_payload={last_payload!r}",
        )

    def test_jellyfin_and_sonarr_keys_in_env(self) -> None:
        env = _container_env(CONTROLLER_CONTAINER)
        self.assertTrue(
            (env.get("JELLYFIN_API_KEY") or "").strip(),
            "JELLYFIN_API_KEY is empty in controller env after boot",
        )
        self.assertTrue(
            (env.get("SONARR_API_KEY") or "").strip(),
            "SONARR_API_KEY is empty in controller env after boot",
        )


if __name__ == "__main__":
    unittest.main()
