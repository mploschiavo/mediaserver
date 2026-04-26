"""Tests for ``ConfigIntegrityService``.

The probe must report each of {ok, corrupt, missing, unknown,
skipped} for the right reason, never raise, and not depend on
any global state — so each test builds its own service registry
fixtures.

The 2026-04-20 Prowlarr corruption is included as a fixture so
the regression is pinned: if a future refactor "tolerates" the
trailing-junk artifact again, this test fails."""

from __future__ import annotations

import json
import sys
import sqlite3
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.api.services.config_integrity import (  # noqa: E402
    ConfigIntegrityService,
)
from media_stack.api.services.registry import ServiceDef  # noqa: E402


def _svc(
    sid: str, *, cfg: str = "", fmt: str = "",
) -> ServiceDef:
    return ServiceDef(
        id=sid,
        name=sid.title(),
        api_key_config=cfg,
        api_key_format=fmt,
    )


class IntegrityProbeTests(unittest.TestCase):

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _service_dir(self, name: str) -> Path:
        d = self.root / name
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ------------------------------------------------------------------
    # XML
    # ------------------------------------------------------------------

    def test_xml_ok(self) -> None:
        self._service_dir("prowlarr").joinpath("config.xml").write_bytes(
            b"<Config><UrlBase>/app/prowlarr</UrlBase></Config>",
        )
        svc = ConfigIntegrityService(
            config_root=self.root,
            services=[_svc("prowlarr", cfg="prowlarr/config.xml", fmt="xml")],
        )
        result = svc.check_service("prowlarr")
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.reason, "")

    def test_xml_corrupt_2026_04_20_artifact(self) -> None:
        """Pinning the real Prowlarr artifact — trailing
        ``</Config>sm>\n</Config>``. Status must be 'corrupt' with
        an XML parse error in the reason."""
        self._service_dir("prowlarr").joinpath("config.xml").write_bytes(
            b"<Config><Port>9696</Port></Config>sm>\n</Config>\n",
        )
        svc = ConfigIntegrityService(
            config_root=self.root,
            services=[_svc("prowlarr", cfg="prowlarr/config.xml", fmt="xml")],
        )
        result = svc.check_service("prowlarr")
        self.assertEqual(result.status, "corrupt")
        self.assertIn("XML parse error", result.reason)

    # ------------------------------------------------------------------
    # YAML
    # ------------------------------------------------------------------

    def test_yaml_ok(self) -> None:
        self._service_dir("bazarr/config").joinpath("config.yaml").write_bytes(
            b"general:\n  api_key: deadbeef\n",
        )
        svc = ConfigIntegrityService(
            config_root=self.root,
            services=[_svc("bazarr", cfg="bazarr/config/config.yaml", fmt="yaml")],
        )
        self.assertEqual(svc.check_service("bazarr").status, "ok")

    def test_yaml_corrupt_unclosed_block(self) -> None:
        self._service_dir("bazarr/config").joinpath("config.yaml").write_bytes(
            b"general:\n  api_key: 'unclosed\n",
        )
        svc = ConfigIntegrityService(
            config_root=self.root,
            services=[_svc("bazarr", cfg="bazarr/config/config.yaml", fmt="yaml")],
        )
        result = svc.check_service("bazarr")
        self.assertEqual(result.status, "corrupt")
        self.assertIn("YAML parse error", result.reason)

    # ------------------------------------------------------------------
    # JSON
    # ------------------------------------------------------------------

    def test_json_ok(self) -> None:
        self._service_dir("jellyseerr").joinpath("settings.json").write_bytes(
            json.dumps({"clientId": "x"}).encode(),
        )
        svc = ConfigIntegrityService(
            config_root=self.root,
            services=[_svc("jellyseerr", cfg="jellyseerr/settings.json", fmt="json")],
        )
        self.assertEqual(svc.check_service("jellyseerr").status, "ok")

    def test_json_corrupt(self) -> None:
        self._service_dir("jellyseerr").joinpath("settings.json").write_bytes(
            b"{ not valid json",
        )
        svc = ConfigIntegrityService(
            config_root=self.root,
            services=[_svc("jellyseerr", cfg="jellyseerr/settings.json", fmt="json")],
        )
        self.assertEqual(svc.check_service("jellyseerr").status, "corrupt")

    # ------------------------------------------------------------------
    # INI
    # ------------------------------------------------------------------

    def test_ini_ok_with_percent_in_value(self) -> None:
        """SABnzbd-style ini with a bare ``%`` in a value must
        still parse; we disable interpolation deliberately."""
        self._service_dir("sabnzbd").joinpath("sabnzbd.ini").write_bytes(
            b"[misc]\nfoo = 50%%\nhost = 0.0.0.0\n",
        )
        svc = ConfigIntegrityService(
            config_root=self.root,
            services=[_svc("sabnzbd", cfg="sabnzbd/sabnzbd.ini", fmt="ini")],
        )
        self.assertEqual(svc.check_service("sabnzbd").status, "ok")

    def test_ini_ok_with_sabnzbd_preamble(self) -> None:
        """The actual SABnzbd file shape: ``__version__ = 19`` on
        line 1, BEFORE any ``[section]`` header. Stdlib
        configparser rejects this with 'File contains no section
        headers' — but the file IS valid SABnzbd config. The
        2026-04-20 dashboard bug: SABnzbd showed Config=Corrupt
        forever because of this. The probe now injects a
        synthetic ``[__top__]`` section so the preamble parses.
        """
        self._service_dir("sabnzbd").joinpath("sabnzbd.ini").write_bytes(
            b"__version__ = 19\n"
            b"\n"
            b"[misc]\n"
            b"host = 0.0.0.0\n"
            b"port = 8080\n",
        )
        svc = ConfigIntegrityService(
            config_root=self.root,
            services=[_svc("sabnzbd", cfg="sabnzbd/sabnzbd.ini", fmt="ini")],
        )
        result = svc.check_service("sabnzbd")
        self.assertEqual(
            result.status, "ok",
            f"SABnzbd preamble rejected: {result.reason}. "
            "The probe must accept top-level keys that precede "
            "the first section header.",
        )

    def test_ini_truncated_garbage_still_corrupt(self) -> None:
        """The synthetic-section workaround must NOT rescue a
        genuinely corrupt file. A truncated INI with broken
        syntax inside the ``[__top__]`` section still has to
        parse — and if it doesn't, status stays ``corrupt``."""
        self._service_dir("sabnzbd").joinpath("sabnzbd.ini").write_bytes(
            b"foo = 1\n[unclosed_section_no_bracket\n",
        )
        svc = ConfigIntegrityService(
            config_root=self.root,
            services=[_svc("sabnzbd", cfg="sabnzbd/sabnzbd.ini", fmt="ini")],
        )
        self.assertEqual(svc.check_service("sabnzbd").status, "corrupt")

    def test_ini_corrupt_section_missing_bracket(self) -> None:
        self._service_dir("sabnzbd").joinpath("sabnzbd.ini").write_bytes(
            b"[misc\nfoo = 1\n",
        )
        svc = ConfigIntegrityService(
            config_root=self.root,
            services=[_svc("sabnzbd", cfg="sabnzbd/sabnzbd.ini", fmt="ini")],
        )
        self.assertEqual(svc.check_service("sabnzbd").status, "corrupt")

    # ------------------------------------------------------------------
    # SQLite
    # ------------------------------------------------------------------

    def test_sqlite_ok(self) -> None:
        path = self._service_dir("jellyfin/data").joinpath("jellyfin.db")
        con = sqlite3.connect(path)
        con.execute("CREATE TABLE x (id INTEGER)")
        con.commit()
        con.close()
        svc = ConfigIntegrityService(
            config_root=self.root,
            services=[_svc("jellyfin", cfg="jellyfin/data/jellyfin.db", fmt="sqlite")],
        )
        self.assertEqual(svc.check_service("jellyfin").status, "ok")

    def test_sqlite_corrupt_random_bytes(self) -> None:
        path = self._service_dir("jellyfin/data").joinpath("jellyfin.db")
        path.write_bytes(b"this is not a sqlite database header at all")
        svc = ConfigIntegrityService(
            config_root=self.root,
            services=[_svc("jellyfin", cfg="jellyfin/data/jellyfin.db", fmt="sqlite")],
        )
        result = svc.check_service("jellyfin")
        self.assertEqual(result.status, "corrupt")
        self.assertIn("SQLite", result.reason)

    # ------------------------------------------------------------------
    # missing / unknown / skipped
    # ------------------------------------------------------------------

    def test_missing_returns_missing(self) -> None:
        svc = ConfigIntegrityService(
            config_root=self.root,
            services=[_svc("prowlarr", cfg="prowlarr/config.xml", fmt="xml")],
        )
        self.assertEqual(svc.check_service("prowlarr").status, "missing")

    def test_unknown_service_id_returns_unknown(self) -> None:
        svc = ConfigIntegrityService(
            config_root=self.root, services=[],
        )
        result = svc.check_service("nonexistent")
        self.assertEqual(result.status, "unknown")

    def test_service_without_config_returns_unknown(self) -> None:
        svc = ConfigIntegrityService(
            config_root=self.root, services=[_svc("homepage")],
        )
        result = svc.check_service("homepage")
        self.assertEqual(result.status, "unknown")

    def test_unsupported_format_returns_skipped(self) -> None:
        self._service_dir("custom").joinpath("config.toml").write_bytes(b"")
        svc = ConfigIntegrityService(
            config_root=self.root,
            services=[_svc("custom", cfg="custom/config.toml", fmt="toml")],
        )
        self.assertEqual(svc.check_service("custom").status, "skipped")

    # ------------------------------------------------------------------
    # check_all
    # ------------------------------------------------------------------

    def test_check_all_returns_one_entry_per_service(self) -> None:
        self._service_dir("prowlarr").joinpath("config.xml").write_bytes(
            b"<Config/>",
        )
        # Second service: no config declared.
        svc = ConfigIntegrityService(
            config_root=self.root,
            services=[
                _svc("prowlarr", cfg="prowlarr/config.xml", fmt="xml"),
                _svc("homepage"),
            ],
        )
        results = svc.check_all()
        # Authelia is added unconditionally via _INFRA_CONFIGS — its
        # presence here keeps the probe covering SSO even though
        # Authelia isn't a registry service.
        self.assertIn("authelia", results.keys())
        self.assertEqual(
            {k for k in results.keys() if k != "authelia"},
            {"prowlarr", "homepage"},
        )
        self.assertEqual(results["prowlarr"]["status"], "ok")
        self.assertEqual(results["homepage"]["status"], "unknown")
        # The Authelia config doesn't exist in this fixture's
        # config_root, so its status is 'missing' — not invalid.
        self.assertEqual(results["authelia"]["status"], "missing")


if __name__ == "__main__":
    unittest.main()
