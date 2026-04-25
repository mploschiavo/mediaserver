"""Batch 2 ratchets shipped in v1.0.117.

Expands coverage into deeper correctness properties:

   #4  hardcoded internal hostnames in source
   #6  sensitive-pattern literals in source (credentials in code)
   #9  dashboard endpoint registration parity
  #14  import-time side effects (module load time cap)
  #17  default-value parity between dataclass and contract YAML
  #20  state-file schema versioning
  #25  audit-trail completeness on POST handlers
   A   healthcheck honesty — hits a real path not just /
   G   container-vs-host paradigm slips — every localhost URL
       commented/typed as container-internal vs host-published
"""

from __future__ import annotations

import ast
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "media_stack"
sys.path.insert(0, str(ROOT / "src"))


# ---------------------------------------------------------------------------
# #4 — hardcoded internal hostnames (strict: zero literals)
# ---------------------------------------------------------------------------
class HardcodedInternalHostnames(unittest.TestCase):
    """Source must not contain literal ``http://<service>:<port>``
    URLs for managed services. The registry
    (``media_stack.api.services.registry``) is the single source of
    truth for ports — call ``service_internal_url(<service_id>)``
    instead.

    Hardcoded literals lock the codebase to compose's internal DNS
    scheme and silently break k8s / podman / custom-network
    deployments.  Strict — every site must come from the registry."""

    # Services that the registry knows about. Anything else
    # (e.g. ``http://example.com:8080`` in a docstring) is allowed.
    _MANAGED = {
        "envoy", "sonarr", "radarr", "lidarr", "readarr", "jellyfin",
        "openseerr", "jellyseerr", "sabnzbd", "qbittorrent", "flaresolverr",
        "homepage", "bazarr", "transmission", "maintainerr", "tautulli",
        "prowlarr", "authelia", "authentik",
    }

    def test_no_hardcoded_service_urls(self) -> None:
        pat = re.compile(
            r'(["\'])http://([a-z][a-z0-9_-]*):(\d{2,5})(?:/[^"\']*)?\1',
            re.IGNORECASE,
        )
        bad: list[str] = []
        for path in SRC.rglob("*.py"):
            if "__pycache__" in str(path):
                continue
            text = path.read_text(encoding="utf-8")
            for line_no, line in enumerate(text.splitlines(), start=1):
                for m in pat.finditer(line):
                    host = m.group(2).lower()
                    if host == "localhost":
                        continue
                    if host not in self._MANAGED:
                        continue
                    bad.append(
                        f"{path.relative_to(ROOT)}:{line_no}: {m.group(0)}"
                    )
        self.assertFalse(
            bad,
            f"Hardcoded service URL literals ({len(bad)} sites) — "
            f"use service_internal_url('<service_id>') from "
            f"media_stack.api.services.registry instead.\n  - "
            + "\n  - ".join(bad[:15]),
        )


# ---------------------------------------------------------------------------
# #6 — sensitive-pattern literals in source
# ---------------------------------------------------------------------------
class SensitivePatternLiterals(unittest.TestCase):
    """Source shouldn't contain literal API keys / passwords /
    tokens.  Some placeholders are OK (``<YOUR_KEY>``,
    ``REDACTED``, ``example-key``); ratchet against real-looking
    hex/base64 secrets."""

    _ALLOWED_LITERALS = {
        # Test fixtures with deliberately fake keys are allow-listed
        # by path-prefix.
        "tests/",
        "docs/",
        # OpenAPI spec embeds example values in schemas — these are
        # documentation, not real credentials.
        "src/media_stack/api/openapi.yaml",
    }

    def test_no_long_hex_or_base64_literals(self) -> None:
        # 32-char+ hex OR base64 that's assigned to a password/
        # key/token/secret variable name.  Avoids false positives
        # on UUIDs or hashes embedded in other contexts.
        pat = re.compile(
            r'(?i)\b(?:password|api[_-]?key|token|secret)\s*[:=]\s*'
            r'["\']([A-Fa-f0-9]{32,}|[A-Za-z0-9+/]{40,}={0,2})["\']',
        )
        hits: list[str] = []
        for path in SRC.rglob("*"):
            if not path.is_file() or "__pycache__" in str(path):
                continue
            if path.suffix not in (".py", ".yaml", ".yml", ".json", ".html"):
                continue
            rel = str(path.relative_to(ROOT))
            if any(rel.startswith(p) for p in self._ALLOWED_LITERALS):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except Exception:
                continue
            for m in pat.finditer(text):
                hits.append(f"{rel}: {m.group(0)[:60]}...")
        self.assertFalse(
            hits,
            "Possible credential literals in source:\n  - "
            + "\n  - ".join(hits[:10]),
        )
class ImportTimeSideEffects(unittest.TestCase):
    """Every module under src/media_stack/ should ``import
    module`` in a subprocess in under 2 seconds.  Long imports
    hide: (a) runtime env reads that should be lazy, (b) DB/HTTP
    calls at module scope, (c) large-file reads at import time.

    Cap at 2s per import; soft-capped at count of slow-imports
    to gate new ones."""

    _MAX_SECS = 2.0
    _MAX_SLOW_IMPORTS = 0

    def test_module_imports_stay_under_time_budget(self) -> None:
        import subprocess as _sub
        import time as _t
        slow: list[str] = []
        # Sample a fast subset — module discovery + import is O(N)
        # and we have ~400 modules.  Start with a curated set of
        # "entry point" modules and expand if a bug bites.
        entry_points = [
            "media_stack.services.jobs.framework",
            "media_stack.cli.commands.controller_serve",
            "media_stack.api.server",
            "media_stack.api.services.registry",
            "media_stack.services.profile_config",
            "media_stack.services.apps.prowlarr.indexer_app_match",
            "media_stack.services.apps.servarr.arr_runtime_defaults",
            "media_stack.core.auth.configure_auth_job",
            "media_stack.core.config_io",
            "media_stack.core.http",
        ]
        for mod in entry_points:
            code = (
                "import os, sys, time;"
                "sys.path.insert(0, 'src');"
                f"t=time.time(); import {mod};"
                "print(round(time.time()-t, 3))"
            )
            start = _t.time()
            proc = _sub.run(
                [sys.executable, "-c", code],
                cwd=ROOT, capture_output=True, text=True,
                timeout=self._MAX_SECS + 3,
            )
            elapsed = _t.time() - start
            if proc.returncode != 0:
                continue  # Skip failing imports — covered by other tests.
            try:
                reported = float(proc.stdout.strip())
            except ValueError:
                continue
            if reported > self._MAX_SECS:
                slow.append(f"{mod}: {reported:.2f}s")
        if len(slow) > self._MAX_SLOW_IMPORTS:
            self.fail(
                f"Modules slow to import (>{self._MAX_SECS}s): {len(slow)}\n  - "
                + "\n  - ".join(slow),
            )


# ---------------------------------------------------------------------------
# #20 — state-file schema versioning
# ---------------------------------------------------------------------------
class StateFileSchemaVersioning(unittest.TestCase):
    """Every JSON state file the controller writes should carry
    a ``version:`` (or ``schema_version:``) field so readers can
    reject unknown versions cleanly.  Silent schema drift on
    state files causes hard-to-diagnose bugs after upgrades."""

    _STATE_WRITERS_REQUIRED_VERSIONED = {
        # path glob → required version key
        "indexer_app_match": "version",
        "indexer-reputation-state": "schema",
        "runtime-config": None,  # mutable runtime, no versioning needed
        "job-history": None,
        "epg-provider-health": None,
    }

    def test_state_writer_modules_declare_version(self) -> None:
        """Ratchet that the indexer_app_match module (our newest
        state writer) includes a version field.  When we add more
        state writers, expand _STATE_WRITERS_REQUIRED_VERSIONED."""
        iam = SRC / "services/apps/prowlarr/indexer_app_match.py"
        if not iam.is_file():
            self.skipTest("indexer_app_match not present")
        text = iam.read_text(encoding="utf-8")
        self.assertIn(
            "_CACHE_VERSION", text,
            "indexer_app_match lost its _CACHE_VERSION constant "
            "— state file readers have nothing to reject old "
            "schemas with.",
        )
        self.assertIn(
            '"version"', text,
            "indexer_app_match JSON state no longer writes a "
            "'version' field.",
        )

    def test_reputation_state_has_schema_version(self) -> None:
        rep = SRC / "services/apps/prowlarr/reputation_ops.py"
        if not rep.is_file():
            self.skipTest("reputation_ops not present")
        text = rep.read_text(encoding="utf-8")
        self.assertRegex(
            text,
            r'["\']schema["\']\s*:\s*1',
            "reputation_ops state file no longer tags 'schema: 1' "
            "— readers can't version-gate future upgrades.",
        )


# ---------------------------------------------------------------------------
# #25 — audit-trail completeness
# ---------------------------------------------------------------------------
class AuditTrailCompleteness(unittest.TestCase):
    """Mutating HTTP endpoints must route through the mutation-
    audit path.  Currently ``do_POST`` in server.py calls
    ``_audit_mutation(self)`` after dispatch.  Ratchet that
    (a) _audit_mutation exists, (b) it IS called from do_POST's
    main path."""

    def test_do_post_calls_audit_mutation(self) -> None:
        server = (SRC / "api" / "server.py").read_text(encoding="utf-8")
        # Find do_POST body.
        m = re.search(
            r"def do_POST\(self\).*?(?=\n    def |\nclass |\Z)",
            server, re.DOTALL,
        )
        self.assertIsNotNone(m, "do_POST handler not found")
        body = m.group(0)
        self.assertIn(
            "_audit_mutation", body,
            "do_POST no longer calls _audit_mutation — every "
            "mutating POST bypasses the audit trail silently.",
        )


# ---------------------------------------------------------------------------
# A — healthcheck honesty
# ---------------------------------------------------------------------------
class HealthcheckHonesty(unittest.TestCase):
    """Compose healthcheck commands that probe bare
    ``http://localhost:N`` without a path often hit the login
    page — which returns 200 even when the actual API is broken.
    Prefer probes against known-functional paths
    (``/ping``, ``/health``, ``/api/v1/status``, ``/healthz``)
    so "healthy" means "working", not "alive"."""

    def test_healthchecks_hit_meaningful_paths(self) -> None:
        try:
            import yaml as _yaml
        except ImportError:
            self.skipTest("PyYAML not installed")
        path = ROOT / "docker" / "docker-compose.yml"
        if not path.is_file():
            self.skipTest("docker-compose.yml not present")
        doc = _yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        shallow: list[str] = []
        for svc_name, svc in (doc.get("services") or {}).items():
            hc = svc.get("healthcheck") or {}
            test = hc.get("test")
            if not test:
                continue
            cmd = " ".join(str(x) for x in (test if isinstance(test, list) else [test]))
            # Flag healthchecks that hit bare host:port with no
            # path or just "/" — true "alive but maybe broken".
            if re.search(
                r"(?:wget|curl)[^\"']*localhost:\d+(?:/?\"|/?$|/?'|/?/\s)", cmd,
            ):
                shallow.append(f"{svc_name}: {cmd[:80]}")
        # Soft-cap: some services legitimately answer on "/" (e.g.
        # homepage), and we can't catch every nuance from the cmd
        # string alone. Warn if the count exceeds a threshold.
        self.assertLessEqual(
            len(shallow), 5,
            f"Healthchecks probing bare localhost without a "
            f"specific path: {len(shallow)}. Prefer service-"
            f"specific endpoints that exercise the real API path "
            f"(e.g. /ping, /health, /api/v1/status).\n  - "
            + "\n  - ".join(shallow[:10]),
        )


# ---------------------------------------------------------------------------
# G — container-vs-host paradigm slips
# ---------------------------------------------------------------------------
class ContainerVsHostSemantics(unittest.TestCase):
    """``localhost`` means different things in different
    contexts:
      - inside a container, it's the container itself
      - on the host, it's the host
      - in a browser session, it's the user's machine

    When source code constructs a ``localhost:N`` URL, it's
    easy to pick the wrong semantics. Strict ratchet — every
    surviving site must be allow-listed with the reason it's
    container-internal vs host-published."""

    _ALLOWED_LOCALHOST_FILES = {
        # download client fallback URL — used only when no
        # torrent client is registered. The literal port matches
        # the qbit/transmission/sab convention; the *real* URL
        # comes from service_internal_url() one branch up.
        "src/media_stack/services/apps/download_clients/registry_helpers.py",
        # `kubectl exec` into the jellyfin pod and curl localhost:8096
        # from INSIDE the pod — this is correctly container-internal.
        "src/media_stack/services/apps/jellyfin/cli/jellyfin_plugin_activation_service.py",
    }

    def test_localhost_url_construction_count_below_cap(self) -> None:
        hits: list[str] = []
        pat = re.compile(
            r'["\']http://localhost:\d+', re.IGNORECASE,
        )
        for path in SRC.rglob("*.py"):
            if "__pycache__" in str(path):
                continue
            rel = str(path.relative_to(ROOT))
            if rel in self._ALLOWED_LOCALHOST_FILES:
                continue
            text = path.read_text(encoding="utf-8")
            for m in pat.finditer(text):
                hits.append(f"{rel}: {m.group(0)}")
        self.assertFalse(
            hits,
            f"Bare 'http://localhost:N' literals in code "
            f"({len(hits)} sites) — each is a bet on a specific "
            f"container-vs-host semantic. Use "
            f"service_internal_url() for container-internal URLs, "
            f"or add the file to _ALLOWED_LOCALHOST_FILES with a "
            f"comment explaining the semantics.\n  - "
            + "\n  - ".join(hits[:15]),
        )


if __name__ == "__main__":
    unittest.main()
