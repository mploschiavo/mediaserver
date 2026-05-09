"""Probe runs against real (sanitised) production config files.

Why this file exists: my synthesised fixtures repeatedly missed
the shapes that live services actually produce. The headline
miss was the SABnzbd ``__version__`` preamble — every test we
had used a stylised ``[misc]\nfoo = 50%%\n`` instead of the real
``__version__ = 19\n[misc]\n...`` shape, so the probe's
"file contains no section headers" failure went uncaught and
showed up as Config=Corrupt on a healthy install.

The fix is structural: we keep real captured snapshots in
``tests/unit/fixtures/configs/`` (sanitised — every secret is
replaced with a ``REDACTED_*`` placeholder) and walk them
through the probe. Healthy fixtures must report ``ok``; the
``corrupt/`` siblings must report ``corrupt`` (or ``invalid``
for semantic validators) so the probe can't be "fixed" into
silently accepting truly broken files.

If you find a config-format pattern the probe rejects in the
field, capture the real file (per ``fixtures/configs/README.md``),
add it here, and write a test named for the failure shape — not
for a generic "test_xml_parse"."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.api.services.config_integrity import (  # noqa: E402
    ConfigIntegrityService,
)
from media_stack.core.service_registry.registry import ServiceDef  # noqa: E402


# Fixtures live at tests/unit/fixtures/configs/ — sibling of the
# test directory tree, not under tests/unit/core/. Resolve from
# this file (tests/unit/core/test_real_config_fixtures.py) up two
# levels to tests/unit/, then into fixtures/configs/.
_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "configs"


def _svc(sid: str, *, cfg: str, fmt: str) -> ServiceDef:
    return ServiceDef(
        id=sid, name=sid.title(),
        api_key_config=cfg, api_key_format=fmt,
    )


class HealthyFixtureTests(unittest.TestCase):
    """Each test here loads a *real captured* config file from a
    running container and asserts the probe reports ``ok``. If a
    refactor breaks one of these, the dashboard will start lying
    about service health — fix the probe, not the fixture.

    Each test also sanity-checks that the fixture is readable so
    a permission/encoding regression on the fixture itself
    surfaces clearly."""

    def _check_ok(self, sid: str, rel_path: str, fmt: str) -> None:
        full_path = _FIXTURES / rel_path
        self.assertTrue(
            full_path.is_file(),
            f"Fixture missing: {full_path}. Capture from a running "
            f"container per fixtures/configs/README.md.",
        )
        svc = ConfigIntegrityService(
            config_root=_FIXTURES,
            services=[_svc(sid, cfg=rel_path, fmt=fmt)],
        )
        result = svc.check_service(sid)
        self.assertEqual(
            result.status, "ok",
            f"Real {sid} config flagged {result.status!r}: "
            f"{result.reason}. The probe is wrong, not the file.",
        )

    def test_sabnzbd_with_version_preamble(self) -> None:
        """Pins the 2026-04-20 dashboard bug: SABnzbd writes
        ``__version__ = 19`` BEFORE any ``[section]``. configparser
        rejects this; the probe must accept it."""
        self._check_ok("sabnzbd", "sabnzbd/sabnzbd.ini", "ini")

    def test_prowlarr_real_config(self) -> None:
        self._check_ok("prowlarr", "prowlarr/config.xml", "xml")

    def test_sonarr_real_config(self) -> None:
        self._check_ok("sonarr", "sonarr/config.xml", "xml")

    def test_bazarr_real_config(self) -> None:
        self._check_ok("bazarr", "bazarr/config/config.yaml", "yaml")

    def test_jellyseerr_real_settings(self) -> None:
        self._check_ok("jellyseerr", "jellyseerr/settings.json", "json")

    def test_tautulli_real_config(self) -> None:
        self._check_ok("tautulli", "tautulli/config.ini", "ini")

    def test_authelia_real_config(self) -> None:
        """Authelia goes through the integrity probe via the
        ``_INFRA_CONFIGS`` table (not the per-service registry).
        Use the same probe machinery directly to validate."""
        full_path = _FIXTURES / "authelia" / "configuration.yml"
        self.assertTrue(full_path.is_file())
        svc = ConfigIntegrityService(
            config_root=_FIXTURES, services=[],
        )
        # check_all() walks the _INFRA_CONFIGS too.
        result = svc.check_all().get("authelia", {})
        self.assertEqual(
            result.get("status"), "ok",
            f"Healthy Authelia config flagged "
            f"{result.get('status')!r}: {result.get('reason')}",
        )


class CorruptFixtureTests(unittest.TestCase):
    """Each ``corrupt/<name>.<ext>`` fixture must produce
    ``status='corrupt'`` (parse failure) or ``status='invalid'``
    (semantic-validator failure). Stops the probe from being
    "fixed" into silently accepting genuinely broken files."""

    def _check_bad(self, sid: str, fixture: str, fmt: str,
                   expected: tuple[str, ...] = ("corrupt", "invalid")) -> None:
        # Re-root the probe at the corrupt/ dir so the integrity
        # service can find the fixture by relative path.
        corrupt_root = _FIXTURES / "corrupt"
        full_path = corrupt_root / fixture
        self.assertTrue(full_path.is_file(),
                        f"corrupt fixture missing: {full_path}")
        svc = ConfigIntegrityService(
            config_root=corrupt_root,
            services=[_svc(sid, cfg=fixture, fmt=fmt)],
        )
        result = svc.check_service(sid)
        self.assertIn(
            result.status, expected,
            f"Probe accepted a deliberately-broken {fixture!r} "
            f"as {result.status!r}. The probe was loosened too far.",
        )

    def test_prowlarr_xml_trailing_junk_pinned(self) -> None:
        """Pins the exact 2026-04-20 production failure: trailing
        ``</Config>sm>\\n</Config>`` after the real closing tag."""
        self._check_bad("prowlarr", "prowlarr_trailing_junk.xml", "xml")

    def test_sabnzbd_unclosed_section_still_corrupt(self) -> None:
        """The synthetic ``[__top__]`` workaround for the preamble
        case must NOT also rescue a genuinely broken
        ``[unclosed_section`` line."""
        self._check_bad("sabnzbd", "sabnzbd_unclosed_section.ini", "ini")

    def test_jellyseerr_truncated_json_corrupt(self) -> None:
        self._check_bad("jellyseerr", "jellyseerr_truncated.json", "json")

    def test_bazarr_unclosed_quote_corrupt(self) -> None:
        self._check_bad("bazarr", "bazarr_unclosed_quote.yaml", "yaml")

    def test_authelia_bare_cookie_domain_invalid(self) -> None:
        """Authelia 4.38 rejects ``domain: local`` (single-label).
        The semantic validator must catch this — pure YAML parses
        fine, so ``status`` should be ``invalid`` (not ``corrupt``).
        Pinning the distinction matters: ``invalid`` triggers
        auto-heal restore from snapshot; ``corrupt`` does too but
        the dashboard label needs to be honest about what kind of
        broken we're looking at."""
        # Authelia validation runs via _INFRA_CONFIGS regardless
        # of the registry; place the fixture at the path the
        # infra config expects.
        import shutil, tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "authelia").mkdir()
            shutil.copy2(
                _FIXTURES / "corrupt" / "authelia_bare_cookie_domain.yml",
                tmp_path / "authelia" / "configuration.yml",
            )
            svc = ConfigIntegrityService(
                config_root=tmp_path, services=[],
            )
            result = svc.check_all().get("authelia", {})
            self.assertEqual(
                result.get("status"), "invalid",
                f"Authelia bare cookie domain not flagged invalid: "
                f"{result.get('status')!r} ({result.get('reason')})",
            )


class FixtureHygieneTests(unittest.TestCase):
    """Belt-and-suspenders: catch a contributor pasting an actual
    secret into a fixture. Every fixture under ``configs/`` must
    use ``REDACTED_*`` placeholders for credentials."""

    def test_no_long_hex_strings_outside_redacted_markers(self) -> None:
        """A 20+ char run of hex digits is almost certainly a real
        API key. The only exception is when it appears inside a
        ``REDACTED_<KIND>`` marker — that's already by definition
        a placeholder."""
        import re
        long_hex = re.compile(r"\b[a-f0-9]{20,}\b")
        offenders: list[str] = []
        for path in _FIXTURES.rglob("*"):
            if not path.is_file() or path.name == "README.md":
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for match in long_hex.finditer(text):
                if "REDACTED" in text[
                    max(0, match.start() - 20): match.end() + 20
                ]:
                    continue
                offenders.append(
                    f"{path.relative_to(_FIXTURES)}: "
                    f"{match.group()[:8]}... "
                    "(looks like a real secret — replace with REDACTED_*)"
                )
        self.assertFalse(
            offenders, "Possible real secrets in fixtures:\n  - "
            + "\n  - ".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()
