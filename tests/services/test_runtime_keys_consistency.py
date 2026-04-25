"""Ratchet #6 — cross-resolver consistency.

There are two API-key resolvers in the controller:

1. Bootstrap-side: ``HealthService.discover_api_keys`` in
   ``src/media_stack/api/services/health.py`` — config files first,
   env fallback. Used at controller boot to populate the K8s Secret.
2. Runtime-side: ``runtime_keys.read_service_api_key`` — env first,
   on-disk file second, ``None`` otherwise. Used per-request from
   ``services/content.py`` and friends.

The two have **different** precedence rules — that asymmetry is
intentional (bootstrap has to *update* the env from disk; the API
server *trusts* env first because it's already authoritative).

This ratchet locks the canonical contract for the *runtime* call
site (``runtime_keys``) by exhaustively walking a 3×3 fixture
matrix:

    env   ∈ {empty, real, different}
    file  ∈ {missing, empty, real}

For each cell we assert ``read_service_api_key`` returns the
expected value AND we cross-check against the file-based reader so
a regression in one half (the wrong file format, the wrong env
fallthrough) is caught here, not in production.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
import unittest.mock as _mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


_ENV_REAL = "envrealkey"
_ENV_DIFFERENT = "envdifferentkey"
_FILE_REAL = "fileremembered"


def _write_arr_xml(target: Path, key: str | None) -> None:
    """Write a Sonarr-style config.xml with ``key`` (or no ApiKey
    tag at all if ``key is None``)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if key is None:
        body = "<Config>\n  <BindAddress>*</BindAddress>\n</Config>\n"
    else:
        body = (
            f"<Config>\n  <ApiKey>{key}</ApiKey>\n"
            "  <BindAddress>*</BindAddress>\n</Config>\n"
        )
    target.write_text(body, encoding="utf-8")


class CrossResolverConsistencyTests(unittest.TestCase):
    """3×3 fixture matrix. Documents the precedence:

        env wins, then file, then None.
    """

    SERVICE = "sonarr"
    ENV_KEY = "SONARR_API_KEY"

    def setUp(self) -> None:
        from media_stack.api.services import runtime_keys
        runtime_keys.invalidate_cache()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config_root = Path(self._tmp.name)
        # Set up a minimal SERVICES list with a real-looking sonarr
        # entry so ``read_api_key_from_file`` can find it.
        from media_stack.api.services.registry import ServiceDef
        self.svc = ServiceDef(
            id="sonarr",
            name="sonarr",
            category="indexer",
            host="sonarr",
            port=8989,
            api_key_env="SONARR_API_KEY",
            api_key_config="sonarr/config.xml",
            api_key_format="xml",
        )

    def _resolve(
        self, *, env_state: str, file_state: str,
    ) -> str | None:
        """Run one cell of the matrix and return what
        ``read_service_api_key`` produced."""
        from media_stack.api.services import runtime_keys

        # Build the env.
        env_patch = {"CONFIG_ROOT": str(self.config_root)}
        if env_state == "empty":
            env_patch[self.ENV_KEY] = ""
        elif env_state == "real":
            env_patch[self.ENV_KEY] = _ENV_REAL
        elif env_state == "different":
            env_patch[self.ENV_KEY] = _ENV_DIFFERENT
        else:
            self.fail(f"unknown env_state: {env_state}")

        # Build the file.
        target = self.config_root / "sonarr" / "config.xml"
        if file_state == "missing":
            if target.exists():
                target.unlink()
        elif file_state == "empty":
            _write_arr_xml(target, None)
        elif file_state == "real":
            _write_arr_xml(target, _FILE_REAL)
        else:
            self.fail(f"unknown file_state: {file_state}")

        runtime_keys.invalidate_cache()
        with _mock.patch.dict(os.environ, env_patch, clear=False), \
                _mock.patch(
                    "media_stack.api.services.registry.SERVICE_MAP",
                    {"sonarr": self.svc}), \
                _mock.patch(
                    "media_stack.api.services.registry.SERVICES",
                    [self.svc]):
            return runtime_keys.read_service_api_key(self.SERVICE)

    # ------------------------------------------------------------------
    # The 3x3 matrix. Each test is one cell.
    # ------------------------------------------------------------------

    def test_env_empty_file_missing_returns_none(self) -> None:
        self.assertIsNone(
            self._resolve(env_state="empty", file_state="missing"),
        )

    def test_env_empty_file_empty_returns_none(self) -> None:
        self.assertIsNone(
            self._resolve(env_state="empty", file_state="empty"),
        )

    def test_env_empty_file_real_returns_file(self) -> None:
        """The headline regression: empty Secret + real on-disk
        file → resolver returns the file's key (NOT None)."""
        self.assertEqual(
            self._resolve(env_state="empty", file_state="real"),
            _FILE_REAL,
        )

    def test_env_real_file_missing_returns_env(self) -> None:
        self.assertEqual(
            self._resolve(env_state="real", file_state="missing"),
            _ENV_REAL,
        )

    def test_env_real_file_empty_returns_env(self) -> None:
        self.assertEqual(
            self._resolve(env_state="real", file_state="empty"),
            _ENV_REAL,
        )

    def test_env_real_file_real_env_wins(self) -> None:
        """Documents the precedence rule: env wins over file."""
        self.assertEqual(
            self._resolve(env_state="real", file_state="real"),
            _ENV_REAL,
        )

    def test_env_different_file_missing_returns_env(self) -> None:
        self.assertEqual(
            self._resolve(env_state="different", file_state="missing"),
            _ENV_DIFFERENT,
        )

    def test_env_different_file_empty_returns_env(self) -> None:
        self.assertEqual(
            self._resolve(env_state="different", file_state="empty"),
            _ENV_DIFFERENT,
        )

    def test_env_different_file_real_env_wins(self) -> None:
        """Both halves yield distinct values → env wins (the
        runtime contract treats env as canonical)."""
        self.assertEqual(
            self._resolve(env_state="different", file_state="real"),
            _ENV_DIFFERENT,
        )

    # ------------------------------------------------------------------
    # Cross-check against the file-side reader directly. This is
    # the "both halves agree on the same canonical value" assertion
    # for cells where the file is the source of truth.
    # ------------------------------------------------------------------

    def test_file_reader_matches_runtime_when_env_empty(self) -> None:
        """When the env path is taken out of the picture, the
        runtime-side resolver and the registry's file-side reader
        must return identical values for the same fixture."""
        from media_stack.api.services.registry import read_api_key_from_file
        from media_stack.api.services import runtime_keys

        target = self.config_root / "sonarr" / "config.xml"
        _write_arr_xml(target, _FILE_REAL)

        env_patch = {
            "CONFIG_ROOT": str(self.config_root),
            self.ENV_KEY: "",
        }
        runtime_keys.invalidate_cache()
        with _mock.patch.dict(os.environ, env_patch, clear=False), \
                _mock.patch(
                    "media_stack.api.services.registry.SERVICE_MAP",
                    {"sonarr": self.svc}), \
                _mock.patch(
                    "media_stack.api.services.registry.SERVICES",
                    [self.svc]):
            via_runtime = runtime_keys.read_service_api_key("sonarr")
            via_file = read_api_key_from_file("sonarr", str(self.config_root))
        self.assertEqual(via_runtime, via_file)
        self.assertEqual(via_runtime, _FILE_REAL)


if __name__ == "__main__":
    unittest.main()
