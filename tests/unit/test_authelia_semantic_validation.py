"""Authelia semantic-validation tests.

Drove the addition of these tests: 2026-04-20 SSO outage. Authelia's
``configuration.yml`` parsed cleanly as YAML but the cookie domain
was the bare ``"local"``, which Authelia 4.38 rejects with
``"is not a valid cookie domain: must have at least a single
period or be an ip address"``. The container crashlooped silently;
the integrity probe (which only checked YAML well-formedness)
reported ``ok``.

These tests pin three layers of defense:

1. **Validator** — given the parsed config dict, return errors for
   the exact production failure shape and a few related ones.
2. **Integrity probe** — when the validator returns errors, the
   probe must report ``status="invalid"`` (not ``ok``) so the
   dashboard surfaces it and the auto-heal job acts on it.
3. **Crashloop classifier** — the Authelia error log line must map
   to ``cause="authelia_cookie_domain_invalid"`` with
   ``healable=True`` so auto-heal restores from snapshot."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.api.services.config_validators import (  # noqa: E402
    validate_authelia_config,
)
from media_stack.api.services.config_integrity import (  # noqa: E402
    ConfigIntegrityService,
)
from media_stack.api.services.crashloop import (  # noqa: E402
    CrashloopClassifier,
)
from media_stack.api.services.workload_inspector import (  # noqa: E402
    WorkloadState,
)


# Real Authelia 4.38 fatal log line captured 2026-04-20 from the
# crashlooping container.
_REAL_FATAL_LOG = (
    "time=\"2026-04-20T21:25:56Z\" level=error "
    "msg=\"Configuration: session: domain config #1 (domain 'local'): "
    "option 'domain' is not a valid cookie domain: "
    "must have at least a single period or be an ip address\"\n"
    "time=\"2026-04-20T21:25:56Z\" level=fatal "
    "msg=\"Can't continue due to the errors loading the configuration\"\n"
)


# ----------------------------------------------------------------------
# Validator
# ----------------------------------------------------------------------


class AutheliaValidatorTests(unittest.TestCase):

    def test_real_2026_04_20_shape_is_rejected(self) -> None:
        """The exact config that production crashlooped with.
        Pin every rule that fired so a future relaxation of the
        validator gets caught."""
        cfg = {
            "session": {
                "secret": "x" * 64,
                "cookies": [{
                    "domain": "local",
                    "authelia_url": "https://auth.local",
                    "default_redirection_url": "https://apps.media-stack.local",
                }],
            },
            "access_control": {
                "rules": [
                    {"domain": ["*..local"], "policy": "one_factor"},
                ],
            },
        }
        errors = validate_authelia_config(cfg)
        rules = {e.rule for e in errors}
        # Bare cookie domain — the headline failure.
        self.assertIn("authelia_cookie_domain_single_label", rules)
        # The "*..local" typo from the generator.
        self.assertIn("authelia_access_control_double_dot", rules)
        # ``authelia_url`` and ``default_redirection_url`` are
        # technically under the bare ``"local"`` scope (string
        # suffix matches), so the URL-scope rule does not fire
        # here — the single-label rule catches the same root
        # cause.

    def test_correct_compose_shape_passes(self) -> None:
        """The shape after the resolver fix: cookie_domain matches
        the eTLD+1 of every URL that mentions it."""
        cfg = {
            "session": {
                "secret": "x" * 64,
                "cookies": [{
                    "domain": "media-stack.local",
                    "authelia_url": "https://auth.media-stack.local",
                    "default_redirection_url": "https://apps.media-stack.local",
                }],
            },
            "access_control": {
                "rules": [
                    {"domain": ["*.media-stack.local"], "policy": "one_factor"},
                ],
            },
        }
        self.assertEqual(validate_authelia_config(cfg), [])

    def test_flat_k8s_shape_passes(self) -> None:
        """Cookie domain matches the bare base; portal sits at
        ``auth.<base>``. This is the K8s-flat layout."""
        cfg = {
            "session": {
                "cookies": [{
                    "domain": "iomio.io",
                    "authelia_url": "https://auth.iomio.io",
                    "default_redirection_url": "https://m.iomio.io",
                }],
            },
        }
        self.assertEqual(validate_authelia_config(cfg), [])

    def test_ip_cookie_domain_is_allowed(self) -> None:
        """Authelia accepts IP-form cookie domains — make sure the
        single-label rule doesn't false-positive on them."""
        cfg = {
            "session": {
                "cookies": [{
                    "domain": "192.168.1.50",
                    "authelia_url": "https://192.168.1.50",
                    "default_redirection_url": "https://192.168.1.50",
                }],
            },
        }
        self.assertEqual(validate_authelia_config(cfg), [])

    def test_empty_cookie_domain_is_rejected(self) -> None:
        cfg = {"session": {"cookies": [{"domain": ""}]}}
        rules = {e.rule for e in validate_authelia_config(cfg)}
        self.assertIn("authelia_cookie_domain_empty", rules)

    def test_leading_dot_is_rejected(self) -> None:
        cfg = {"session": {"cookies": [{"domain": ".media-stack.local"}]}}
        rules = {e.rule for e in validate_authelia_config(cfg)}
        self.assertIn("authelia_cookie_domain_dot_edge", rules)

    def test_url_under_cookie_scope_strict(self) -> None:
        """The host must equal the domain or be a true subdomain.
        ``apps.media-stack.local`` is fine for cookie ``media-stack.local``;
        ``apps.media-stack.locale`` is NOT (string-suffix bug)."""
        cfg = {
            "session": {
                "cookies": [{
                    "domain": "media-stack.local",
                    "authelia_url": "https://auth.media-stack.locale",
                }],
            },
        }
        rules = {e.rule for e in validate_authelia_config(cfg)}
        self.assertIn("authelia_url_outside_cookie_scope", rules)


# ----------------------------------------------------------------------
# Integrity probe runs the validator
# ----------------------------------------------------------------------


_BAD_AUTHELIA_YAML = (
    b"session:\n"
    b"  cookies:\n"
    b"  - domain: local\n"
    b"    authelia_url: https://auth.local\n"
    b"    default_redirection_url: https://apps.media-stack.local\n"
    b"access_control:\n"
    b"  rules:\n"
    b"  - domain: ['*..local']\n"
    b"    policy: one_factor\n"
)

_GOOD_AUTHELIA_YAML = (
    b"session:\n"
    b"  cookies:\n"
    b"  - domain: media-stack.local\n"
    b"    authelia_url: https://auth.media-stack.local\n"
    b"    default_redirection_url: https://apps.media-stack.local\n"
)


class IntegrityProbeAutheliaTests(unittest.TestCase):

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "authelia").mkdir()
        self.cfg = self.root / "authelia" / "configuration.yml"

    def test_invalid_authelia_config_reports_status_invalid(self) -> None:
        """The 2026-04-20 shape: parses fine, but Authelia rejects
        it. Probe must distinguish from ``ok`` so the dashboard
        and auto-heal can act."""
        self.cfg.write_bytes(_BAD_AUTHELIA_YAML)
        svc = ConfigIntegrityService(
            config_root=self.root, services=[],
        )
        result = svc.check_service("authelia")
        # Authelia is added via _INFRA_CONFIGS so check_all sees
        # it; check_service hits via probe_path indirectly...
        # Use check_all to be sure we exercise the real path.
        all_results = svc.check_all()
        self.assertIn("authelia", all_results)
        self.assertEqual(
            all_results["authelia"]["status"], "invalid",
            "Probe must distinguish 'parses but app rejects' from 'ok'.",
        )
        self.assertIn(
            "single_label", all_results["authelia"]["reason"],
        )

    def test_valid_authelia_config_reports_ok(self) -> None:
        self.cfg.write_bytes(_GOOD_AUTHELIA_YAML)
        svc = ConfigIntegrityService(
            config_root=self.root, services=[],
        )
        result = svc.check_all()["authelia"]
        self.assertEqual(result["status"], "ok")

    def test_corrupt_yaml_still_reports_corrupt_not_invalid(self) -> None:
        """If the YAML doesn't even parse, status is ``corrupt``,
        not ``invalid``. The validator never runs."""
        self.cfg.write_bytes(b"session:\n  cookies: [{domain: 'unclosed\n")
        svc = ConfigIntegrityService(
            config_root=self.root, services=[],
        )
        result = svc.check_all()["authelia"]
        self.assertEqual(result["status"], "corrupt")


# ----------------------------------------------------------------------
# Crashloop classifier maps the Authelia fatal log to a healable cause
# ----------------------------------------------------------------------


class _StubInspector:
    def __init__(self, log: str) -> None:
        self._log = log

    def list_workloads(self, sids):
        return {
            sid: WorkloadState(
                service_id=sid, running=False, restart_count=8,
                last_terminated_reason="Error",
                last_terminated_exit_code=1,
            )
            for sid in sids
        }

    def previous_logs(self, sid, *, tail_lines=200):
        return self._log


class _Svc:
    def __init__(self, sid: str) -> None:
        self.id = sid
        self.name = sid.title()


class CrashloopAutheliaSignatureTests(unittest.TestCase):

    def test_real_2026_04_20_log_is_classified(self) -> None:
        classifier = CrashloopClassifier(
            inspector=_StubInspector(_REAL_FATAL_LOG),
            services=[_Svc("authelia")],
        )
        result = classifier.check_service("authelia")
        self.assertEqual(
            result["cause"], "authelia_cookie_domain_invalid",
            "The exact production fatal log line must classify as "
            "the cookie-domain-invalid cause.",
        )
        self.assertTrue(
            result["healable"],
            "Auto-heal must be able to act on this — the snapshot "
            "store will have a healthy cookie-domain version.",
        )

    def test_storage_key_rotation_signature(self) -> None:
        log = (
            "level=fatal msg=\"the configured encryption key does "
            "not appear to be valid for this database\"\n"
        )
        classifier = CrashloopClassifier(
            inspector=_StubInspector(log),
            services=[_Svc("authelia")],
        )
        result = classifier.check_service("authelia")
        self.assertEqual(result["cause"], "authelia_storage_key_rotated")
        self.assertFalse(
            result["healable"],
            "Auto-heal can't recover from a rotated encryption "
            "key — the SQLite rows are unrecoverable. Mark not "
            "healable so the user gets a manual-fix banner.",
        )


if __name__ == "__main__":
    unittest.main()
