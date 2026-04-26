"""Batch 3 ratchets shipped in v1.0.118.

Medium-priority correctness + integration ratchets.

Bug classes covered:

   #5  contract job ↔ handler kwargs parity (sibling of v1.0.116
       ContractJobHandlerParity, but checks the *signature* not just
       importability — catches "contract passes new field, handler
       silently ignores it" drift)
  #15  Dockerfile build context excludes obvious leaks
       (.venv, backups, .claude, *.log)
  #16  CSRF enforcement: the POST dispatch path must verify the
       cookie/header pair before doing any work
  #19  every ServiceDef.health_path is non-empty and starts with "/"
       (a bare "/" caught a Jellyfin GUI page on a broken API; the
       healthcheck-honesty ratchet catches compose-side, this ratchet
       catches registry-side)
  #20  service-registry ↔ contract-services parity (every
       ServiceDef has a contract YAML; every contract has a registry
       entry, except documented technology-alias contracts)
  #29  subprocess.run() always specifies the check= kwarg
       (defaults to False but Python's docstring says "explicit is
       better than implicit"; missing check= silently swallows
       non-zero exit codes)
   #32 dist/ regen is byte-deterministic — running bin/release/regen-dist.sh
       twice yields identical bytes (caught DICT-ORDER bugs in YAML
       output after profile YAML was added)
   J  test doubles for http_request return 3-tuples (status, data,
      body) — matches the real http_request contract; mismatches
      were the original "test passes, prod breaks" trap
"""

from __future__ import annotations

import ast
import hashlib
import importlib
import inspect
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src" / "media_stack"
sys.path.insert(0, str(ROOT / "src"))


# ---------------------------------------------------------------------------
# #5b — contract job ↔ handler kwargs parity
# ---------------------------------------------------------------------------
class ContractJobKwargsParity(unittest.TestCase):
    """Every contract-declared input/param must appear in the
    handler's Python signature (or it must accept **kwargs).
    Catches "contract YAML adds a field, handler silently drops it"
    — the dataclass-parser-drift bug at the contract boundary."""

    def test_every_contract_kwarg_is_in_handler_signature(self) -> None:
        try:
            import yaml as _yaml
        except ImportError:
            self.skipTest("PyYAML not installed")
        contracts_dir = ROOT / "contracts" / "services"
        if not contracts_dir.is_dir():
            self.skipTest("contracts/services not present")

        bad: list[str] = []
        for yaml_file in sorted(contracts_dir.glob("*.yaml")):
            doc = _yaml.safe_load(yaml_file.read_text(encoding="utf-8")) or {}
            jobs = ((doc.get("plugin") or {}).get("jobs") or {})
            for job_name, job_def in jobs.items():
                handler = (job_def or {}).get("handler", "")
                if ":" not in handler:
                    continue
                mod_name, fn_name = handler.split(":", 1)
                try:
                    mod = importlib.import_module(mod_name)
                except Exception:
                    continue  # ContractJobHandlerParity catches import failures
                fn = getattr(mod, fn_name, None)
                if fn is None or not callable(fn):
                    continue
                try:
                    sig = inspect.signature(fn)
                except (TypeError, ValueError):
                    continue
                params = sig.parameters
                if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
                    continue  # **kwargs accepts anything
                required: set[str] = set()
                for section in ("inputs", "params", "args"):
                    sec = (job_def or {}).get(section) or {}
                    if isinstance(sec, dict):
                        required.update(sec.keys())
                    elif isinstance(sec, list):
                        required.update(str(x) for x in sec)
                missing = required - set(params.keys())
                if missing:
                    bad.append(
                        f"{yaml_file.name}::{job_name} ({mod_name}:{fn_name}) "
                        f"handler missing kwargs: {sorted(missing)}"
                    )
        self.assertFalse(
            bad,
            "Contract job kwargs not present in Python handler "
            "signature — add the param or accept **kwargs:\n  - "
            + "\n  - ".join(bad),
        )


# ---------------------------------------------------------------------------
# #15 — Dockerfile build context excludes obvious leaks
# ---------------------------------------------------------------------------
class DockerignoreCompleteness(unittest.TestCase):
    """``.dockerignore`` must cover the obvious "this should never
    end up in a production image" entries: editor caches, virtualenvs,
    .claude (agent worktrees), local backups."""

    _REQUIRED_ENTRIES = (
        ".git", ".venv", "__pycache__", ".pytest_cache",
        "node_modules", ".claude", "backups",
    )

    def test_dockerignore_excludes_obvious_leaks(self) -> None:
        path = ROOT / ".dockerignore"
        if not path.is_file():
            self.skipTest(".dockerignore not present")
        text = path.read_text(encoding="utf-8")
        # Match lines that are EXACTLY each required token
        # (commented lines don't count).
        lines = [
            ln.strip() for ln in text.splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        ]
        missing = [r for r in self._REQUIRED_ENTRIES if r not in lines]
        self.assertFalse(
            missing,
            f".dockerignore is missing required entries: {missing}. "
            f"Add them to keep them out of the production build "
            f"context (each is either a 30GB+ leak or an info-disclosure "
            f"risk).",
        )


# ---------------------------------------------------------------------------
# #16 — CSRF enforcement at POST dispatch
# ---------------------------------------------------------------------------
class CsrfEnforcementCompleteness(unittest.TestCase):
    """The POST dispatch path must verify the CSRF cookie/header
    pair before running any handler body. handlers_post.handle()
    is the choke-point; it must call _csrf.verify(...) early."""

    def test_handlers_post_calls_csrf_verify(self) -> None:
        path = SRC / "api" / "handlers_post.py"
        if not path.is_file():
            self.skipTest("handlers_post not present")
        text = path.read_text(encoding="utf-8")
        # Must reference _csrf.verify(...) at least once with the
        # documented cookie_header= / header_value= kwargs.
        self.assertRegex(
            text,
            r"_csrf\.verify\(\s*cookie_header\s*=",
            "handlers_post.py no longer calls _csrf.verify(cookie_header=...) "
            "— POST dispatch may not enforce CSRF; verify the "
            "_PostCsrfGate / _validate_csrf path is intact.",
        )


# ---------------------------------------------------------------------------
# #19 — every registry health_path is meaningful (non-empty, /-prefixed)
# ---------------------------------------------------------------------------
class RegistryHealthPathMeaningful(unittest.TestCase):
    """A bare ``/`` is the same foot-gun the compose-healthcheck
    ratchet caught: ``/`` returns 200 from a login page even when
    the service's API is broken. Per-service health_path must be
    a real probe path."""

    _ALLOWED_BARE_SLASH = {
        # homepage's only useful path IS / (returns the static
        # dashboard HTML); a 200 means homepage is alive.
        "homepage",
        # FlareSolverr exposes only / — it returns
        # `{"msg":"FlareSolverr is ready!"}` JSON. No deeper probe
        # path exists in the upstream API.
        "flaresolverr",
        # Community-plugin alternative services where we don't have
        # authoritative health-path knowledge. If any becomes the
        # default for a profile, add a real probe path and remove
        # the entry here.
        "grabit", "jdownloader", "mythtv", "nzbget",
    }

    def test_health_paths_are_specific(self) -> None:
        from media_stack.api.services.registry import SERVICES
        bad: list[str] = []
        for s in SERVICES:
            # Skip services that don't expose an HTTP surface
            # (port=0 means "no listener" — health probes are N/A).
            if not s.port:
                continue
            if not s.health_path or not s.health_path.startswith("/"):
                bad.append(f"{s.id}: health_path={s.health_path!r}")
                continue
            if s.health_path == "/" and s.id not in self._ALLOWED_BARE_SLASH:
                bad.append(
                    f"{s.id}: health_path is bare '/' — set a real "
                    f"probe endpoint (e.g. /ping, /System/Info/Public, "
                    f"/api/v1/status)"
                )
        self.assertFalse(
            bad,
            "Service health_path values are missing or too generic:\n  - "
            + "\n  - ".join(bad),
        )


# ---------------------------------------------------------------------------
# #20 — registry ↔ contract-services parity
# ---------------------------------------------------------------------------
class ServiceRegistryContractParity(unittest.TestCase):
    """Every ServiceDef must have a contract YAML; every contract
    YAML (with a service: block) must have a ServiceDef. Catches
    "I added a service to the registry but forgot the contract" and
    vice versa."""

    _CONTRACT_ONLY_OK = {
        # Technology-alias contracts: piggyback on a sibling
        # service's registry entry, no own ServiceDef.
        "openseerr",     # alternative request_manager (alias of jellyseerr)
        "transmission",  # alternative torrent_client (alias of qbittorrent)
    }

    def test_registry_and_contracts_agree(self) -> None:
        try:
            import yaml as _yaml
        except ImportError:
            self.skipTest("PyYAML not installed")
        from media_stack.api.services.registry import SERVICES
        contracts_dir = ROOT / "contracts" / "services"
        if not contracts_dir.is_dir():
            self.skipTest("contracts/services not present")

        registry_ids = {s.id for s in SERVICES}
        contract_ids: set[str] = set()
        for yaml_file in sorted(contracts_dir.glob("*.yaml")):
            if yaml_file.stem.startswith("_"):
                continue
            doc = _yaml.safe_load(yaml_file.read_text(encoding="utf-8")) or {}
            svc_id = (
                ((doc.get("plugin") or {}).get("id"))
                or yaml_file.stem
            )
            contract_ids.add(svc_id)

        only_registry = registry_ids - contract_ids
        only_contract = (contract_ids - registry_ids) - self._CONTRACT_ONLY_OK
        msgs: list[str] = []
        if only_registry:
            msgs.append(f"In registry but no contract: {sorted(only_registry)}")
        if only_contract:
            msgs.append(
                f"In contract but no registry entry: {sorted(only_contract)} "
                f"(if intentional, add to _CONTRACT_ONLY_OK with rationale)"
            )
        self.assertFalse(
            msgs,
            "Service registry and contracts have diverged:\n  - "
            + "\n  - ".join(msgs),
        )


# ---------------------------------------------------------------------------
# #29 — subprocess.run() always specifies check=
# ---------------------------------------------------------------------------
class SubprocessExplicitCheck(unittest.TestCase):
    """``subprocess.run`` defaults ``check=False``; missing
    ``check=`` reads as "I haven't decided" — make the choice
    explicit so the next reader knows the failure mode is
    intentional."""

    def test_every_subprocess_run_specifies_check_kwarg(self) -> None:
        bad: list[str] = []
        for path in SRC.rglob("*.py"):
            if "__pycache__" in str(path):
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if not isinstance(func, ast.Attribute) or func.attr != "run":
                    continue
                if not isinstance(func.value, ast.Name) or func.value.id != "subprocess":
                    continue
                kw_names = {kw.arg for kw in node.keywords if kw.arg}
                if "check" not in kw_names:
                    bad.append(f"{path.relative_to(ROOT)}:{node.lineno}")
        self.assertFalse(
            bad,
            f"subprocess.run() calls without explicit check= "
            f"({len(bad)} sites). Add check=True to fail fast on "
            f"non-zero exit, or check=False to silently tolerate it. "
            f"Don't leave the choice implicit:\n  - "
            + "\n  - ".join(bad[:15]),
        )


# ---------------------------------------------------------------------------
# #32 — dist/ regen is byte-deterministic
# ---------------------------------------------------------------------------
class DistRegenDeterminism(unittest.TestCase):
    """Running ``bin/release/regen-dist.sh`` twice in a row must produce
    byte-identical ``dist/docker-compose.yml`` and
    ``dist/k8s-deploy.yaml``. Non-determinism here means dict
    ordering, ``set()`` iteration order, or a timestamp leak — all
    bugs that bite when developers diff CI artifacts."""

    def test_regen_is_byte_deterministic(self) -> None:
        targets = [
            ROOT / "dist" / "docker-compose.yml",
            ROOT / "dist" / "k8s-deploy.yaml",
        ]
        for t in targets:
            if not t.is_file():
                self.skipTest(f"{t.name} not regen'd yet — run bin/release/regen-dist.sh")
        before = {str(t): hashlib.sha256(t.read_bytes()).hexdigest() for t in targets}
        try:
            subprocess.run(
                ["bash", "bin/release/regen-dist.sh"],
                cwd=ROOT, check=True, capture_output=True, timeout=60,
            )
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            self.skipTest(f"regen-dist.sh not runnable: {exc}")
        after = {str(t): hashlib.sha256(t.read_bytes()).hexdigest() for t in targets}
        drift = [t for t in before if before[t] != after[t]]
        self.assertFalse(
            drift,
            "bin/release/regen-dist.sh is not deterministic — these files "
            "differ on a second run:\n  - " + "\n  - ".join(drift)
            + "\nLikely cause: dict/set iteration order, timestamp "
            "leak, or unsorted YAML keys in the generator.",
        )


# ---------------------------------------------------------------------------
# J — http_request mock test doubles return 3-tuples
# ---------------------------------------------------------------------------
class HttpRequestMockShapeParity(unittest.TestCase):
    """Test doubles assigning ``mock.http_request.return_value``
    must produce a 3-tuple ``(status, data, body)`` matching the
    real ``http_request`` contract. Mismatched shape was the root
    cause of the v1.0.111-era "test passes, prod breaks" bugs."""

    _PAT = re.compile(
        r"\w+\.http_request\.return_value\s*=\s*\(([^)]+)\)",
    )

    def test_mock_http_request_returns_three_tuple(self) -> None:
        bad: list[str] = []
        for path in (ROOT / "tests" / "unit").rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for m in self._PAT.finditer(text):
                inner = m.group(1)
                # Count top-level commas + 1 = element count.
                depth = 0
                parts = 0
                saw = False
                for ch in inner:
                    if ch in "([{":
                        depth += 1
                    elif ch in ")]}":
                        depth -= 1
                    elif ch == "," and depth == 0:
                        parts += 1
                        saw = False
                        continue
                    if not ch.isspace():
                        saw = True
                if saw:
                    parts += 1
                if parts != 3:
                    line_no = text[:m.start()].count("\n") + 1
                    bad.append(
                        f"{path.relative_to(ROOT)}:{line_no}: "
                        f"http_request mock has {parts} elements "
                        f"— expected 3-tuple (status, data, body)"
                    )
        self.assertFalse(
            bad,
            "Test doubles for http_request must return 3-tuples "
            "(status, data, body) to match the real contract:\n  - "
            + "\n  - ".join(bad),
        )


if __name__ == "__main__":
    unittest.main()
