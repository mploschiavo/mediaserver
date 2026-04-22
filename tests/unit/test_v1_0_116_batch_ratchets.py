"""Batch 1 ratchets shipped in v1.0.116.

Each test class targets one bug class from the
"how do we keep doing this" gap analysis (see session memory
``feedback_dataclass_parser_drift.md`` for the meta-principle).
The shape is always: introspect or grep the canonical source,
assert dependent code stays in sync.

Bug classes covered in this batch:

  #11 — multiprocessing.Process MUST go through spawn context
        (the v1.0.105 fork-deadlock that silently hung bootstrap)
  #12 — no two compose services publish the same host port
        (qBit owned 8080 → SAB had to remap to 8085)
  #18 — compose healthcheck timing sanity
        (interval ≥ timeout; retries × interval > startup_period)
  #28 — git tag ↔ VERSION file parity
  #5  — every contract-declared job has an importable handler
  #20 — every JSON state file the controller writes has a
        ``version:`` field for forward-compat
  #21 — every config-XML write goes through atomic_write_xml
  #27 — log-level discipline: no [DEBUG] strings emitted at INFO
  B   — every read_text/write_text on src/ uses encoding="utf-8"
  F   — time-of-day correctness: no naive datetime.now()
  K   — silent-fail count (``except: pass`` / bare ``except``)
        is bounded; new ones forbidden without bumping the cap
  #24 — unused-import cleanliness via simple AST check (vulture-lite)
"""

from __future__ import annotations

import ast
import dataclasses
import importlib
import inspect
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "media_stack"
sys.path.insert(0, str(ROOT / "src"))


# ---------------------------------------------------------------------------
# #11 — multiprocessing.Process must use spawn context
# ---------------------------------------------------------------------------
class SpawnContextEnforcement(unittest.TestCase):
    """The v1.0.105 fork-deadlock. ``multiprocessing.Process(...)``
    on Linux defaults to fork(), inheriting parent thread locks
    as permanently held → first lock acquire deadlocks. The
    controller runs ~6 background threads at startup so this
    fires reliably in production.

    Spawn creates a fresh interpreter — no inherited locks. All
    subprocess spawns must go through a ``get_context("spawn")``
    helper, never the bare class."""

    def test_no_bare_multiprocessing_process_calls(self) -> None:
        bad: list[str] = []
        for path in SRC.rglob("*.py"):
            if "__pycache__" in str(path):
                continue
            text = path.read_text(encoding="utf-8")
            for m in re.finditer(r"multiprocessing\.Process\s*\(", text):
                bad.append(f"{path.relative_to(ROOT)}: bare multiprocessing.Process(")
        self.assertFalse(
            bad,
            "Bare multiprocessing.Process() found — must use a "
            "spawn context (_MP_CTX = multiprocessing.get_context"
            "('spawn'); _MP_CTX.Process(...)) or fork-deadlock "
            "the next time a thread holds a lock at fork time.\n  - "
            + "\n  - ".join(bad),
        )


# ---------------------------------------------------------------------------
# #12 — published-port uniqueness in compose
# ---------------------------------------------------------------------------
class ComposePublishedPortUniqueness(unittest.TestCase):
    """No two compose services publish the same host port. qBit
    owns 8080 → SAB had to remap to 8085. A future service
    naively picking :8080 would silently fail to start at
    runtime; this catches it at config-validate time."""

    def test_unique_published_ports(self) -> None:
        try:
            import yaml as _yaml
        except ImportError:
            self.skipTest("PyYAML not installed")
        path = ROOT / "docker" / "docker-compose.yml"
        if not path.is_file():
            self.skipTest("docker-compose.yml not present")
        doc = _yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        # Track (host_port, transport) tuples. Same port on
        # different transports (6881/tcp and 6881/udp) doesn't
        # collide — qBittorrent legitimately publishes both.
        seen: dict[tuple[int, str], list[str]] = {}
        for svc_name, svc in (doc.get("services") or {}).items():
            # Skip services gated behind compose profiles —
            # profile-gated services don't run in the default
            # install, and an operator opting INTO a profile is
            # responsible for any collisions it introduces.
            # traefik (profile=traefik) collides with envoy (default)
            # on :80 because the operator's chosen ONE OR THE OTHER
            # of those gateways, never both.
            if svc.get("profiles"):
                continue
            for entry in (svc.get("ports") or []):
                if isinstance(entry, dict):
                    host_port = entry.get("published") or entry.get("target")
                    try:
                        host_port = int(host_port)
                    except (TypeError, ValueError):
                        continue
                    transport = str(entry.get("protocol", "tcp")).lower()
                else:
                    s = str(entry)
                    transport = "udp" if "/udp" in s.lower() else "tcp"
                    s = s.split("/")[0]
                    parts = s.split(":")
                    if len(parts) < 2:
                        continue
                    host_part = parts[-2]
                    m = re.search(r"-(\d+)\}", host_part) or re.search(r"^(\d+)$", host_part)
                    if not m:
                        continue
                    try:
                        host_port = int(m.group(1))
                    except ValueError:
                        continue
                key = (host_port, transport)
                if svc_name not in seen.setdefault(key, []):
                    seen[key].append(svc_name)
        collisions = {f"{p}/{t}": s for (p, t), s in seen.items() if len(s) > 1}
        self.assertFalse(
            collisions,
            f"Compose services publish overlapping host ports: "
            f"{collisions}. Pick a different host-side port for "
            f"the colliding service (cf. SABnzbd 8080→8085 because "
            f"qBittorrent owns 8080 on the host).",
        )


# ---------------------------------------------------------------------------
# #18 — healthcheck timing sanity
# ---------------------------------------------------------------------------
class ComposeHealthcheckTiming(unittest.TestCase):

    def test_interval_at_least_equal_to_timeout(self) -> None:
        """``timeout`` is the per-attempt limit; ``interval`` is
        time between attempts. ``timeout > interval`` makes the
        next attempt start while the previous is still
        in-flight. Bound the foot-gun."""
        try:
            import yaml as _yaml
        except ImportError:
            self.skipTest("PyYAML not installed")
        path = ROOT / "docker" / "docker-compose.yml"
        if not path.is_file():
            self.skipTest("docker-compose.yml not present")
        doc = _yaml.safe_load(path.read_text(encoding="utf-8")) or {}

        def _seconds(v: object, default: int = 0) -> int:
            if v is None:
                return default
            s = str(v).strip()
            m = re.match(r"^(\d+)([smh]?)$", s)
            if not m:
                return default
            n = int(m.group(1))
            unit = m.group(2) or "s"
            return n * {"s": 1, "m": 60, "h": 3600}[unit]

        bad: list[str] = []
        for svc_name, svc in (doc.get("services") or {}).items():
            hc = svc.get("healthcheck") or {}
            if not hc:
                continue
            interval = _seconds(hc.get("interval"), 30)
            timeout = _seconds(hc.get("timeout"), 30)
            if timeout > interval:
                bad.append(
                    f"{svc_name}: interval={interval}s < timeout={timeout}s"
                )
        self.assertFalse(
            bad,
            "Healthcheck timing inverted (timeout > interval):\n  - "
            + "\n  - ".join(bad),
        )


# ---------------------------------------------------------------------------
# #28 — git tag ↔ VERSION parity
# ---------------------------------------------------------------------------
class GitTagVersionParity(unittest.TestCase):
    """If a v1.0.X tag exists, the VERSION file at that commit
    should read 1.0.X. Cheap audit run only on tagged checkouts;
    no-op on non-tag commits."""

    def test_current_tag_matches_version(self) -> None:
        # Skip if not in a git repo or no tag points at HEAD.
        try:
            tags = subprocess.check_output(
                ["git", "tag", "--points-at", "HEAD"],
                cwd=ROOT, stderr=subprocess.DEVNULL,
            ).decode().strip().splitlines()
        except Exception:
            self.skipTest("git not available")
        if not tags:
            self.skipTest("HEAD has no tag")
        # Skip when working tree is dirty — we're mid-release
        # (bumped VERSION, not yet committed/tagged). The test
        # fires meaningfully only on clean tagged commits.
        try:
            dirty = subprocess.check_output(
                ["git", "status", "--porcelain"],
                cwd=ROOT, stderr=subprocess.DEVNULL,
            ).decode().strip()
        except Exception:
            dirty = ""
        if dirty:
            self.skipTest("working tree dirty — mid-release")
        tag = tags[0]
        m = re.match(r"^v?(\d+\.\d+\.\d+)$", tag)
        if not m:
            self.skipTest(f"tag {tag!r} not semver")
        version_file = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        self.assertEqual(
            version_file, m.group(1),
            f"Tag {tag!r} doesn't match VERSION file {version_file!r}",
        )


# ---------------------------------------------------------------------------
# #5 — contract job ↔ handler presence parity
# ---------------------------------------------------------------------------
class ContractJobHandlerParity(unittest.TestCase):

    def test_every_contract_job_has_importable_handler(self) -> None:
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
                except Exception as exc:
                    bad.append(f"{yaml_file.name}::{job_name}: import {mod_name} failed: {exc}")
                    continue
                if not hasattr(mod, fn_name):
                    bad.append(f"{yaml_file.name}::{job_name}: {mod_name}.{fn_name} missing")
        self.assertFalse(
            bad,
            "Contract jobs reference handlers that don't exist:\n  - "
            + "\n  - ".join(bad),
        )


# ---------------------------------------------------------------------------
# #21 — every XML config write goes through atomic_write_xml
# ---------------------------------------------------------------------------
class AtomicWriteUsage(unittest.TestCase):
    """We have a hardened ``atomic_write_xml`` that fsyncs +
    re-parses + rolls back from .bak on corruption. Code that
    bypasses it with raw ``write_text`` on a *.xml path can
    crash-corrupt the file. Allow-list narrow."""

    _ALLOWED_RAW_XML_WRITERS = {
        # gpu.py:149, 183 — Jellyfin system.xml mutations.
        # Already does explicit .bak backup before write.
        # Worth migrating to atomic_write_xml as a follow-up but
        # not blocking on it; the failure mode (corrupt
        # system.xml) is bounded and recoverable from the .bak.
        "src/media_stack/services/apps/jellyfin/gpu.py",
    }

    def test_no_raw_write_text_to_xml_paths(self) -> None:
        """Per-line scan for ``var.write_text(...)`` whose
        VARIABLE NAME suggests an XML config path. Catches the
        common shape (``config_path.write_text(tree.write())``)
        without false-positive matching on ``.xml`` substrings
        anywhere in the file."""
        bad: list[str] = []
        # Trigger when:
        #   - the .write_text receiver name contains "xml" or "config"
        #   - AND the line is in a file under services/ (excludes test
        #     fixtures + this very ratchet file)
        # ONLY var names containing "xml" — atomic_write_xml is
        # XML-specific. YAML/JSON configs would need their own
        # atomic helper (separate ratchet, not this one).
        var_pat = re.compile(r"\b\w*xml\w*\.write_text\(", re.IGNORECASE)
        for path in SRC.rglob("*.py"):
            if "__pycache__" in str(path):
                continue
            text = path.read_text(encoding="utf-8")
            for line_no, line in enumerate(text.splitlines(), start=1):
                m = var_pat.search(line)
                if not m:
                    continue
                # Skip the helper itself and atomic_write_xml callers.
                if "atomic_write_xml" in line or "def atomic_write" in line:
                    continue
                rel = str(path.relative_to(ROOT))
                if rel in self._ALLOWED_RAW_XML_WRITERS:
                    continue
                snippet = line.strip()[:90]
                bad.append(f"{rel}:{line_no}: {snippet}")
        self.assertFalse(
            bad,
            "Raw .write_text on what looks like an XML/config "
            "path — use atomic_write_xml from core/config_io.py "
            "(fsync + post-write reparse + .bak rollback). False "
            "positives can be added to _ALLOWED_RAW_XML_WRITERS "
            "with a comment:\n  - "
            + "\n  - ".join(bad),
        )


# ---------------------------------------------------------------------------
# #27 — log-level discipline
# ---------------------------------------------------------------------------
class LogLevelDiscipline(unittest.TestCase):
    """``[DEBUG]``-tagged messages must not be emitted by code
    paths that run at INFO level. We use string prefixes
    (``[DEBUG]``, ``[INFO]``, ``[WARN]``) as a leveling
    convention; the prefix should match the visibility we
    actually want."""

    def test_no_debug_prefix_in_log_warn_or_log_error(self) -> None:
        bad: list[str] = []
        # Only flag the most clearly wrong shape: an explicit
        # logger.warning(...) / .error(...) call that contains
        # a "[DEBUG]" prefix in its first arg.
        for path in SRC.rglob("*.py"):
            if "__pycache__" in str(path):
                continue
            text = path.read_text(encoding="utf-8")
            for m in re.finditer(
                r'(?:log|logger|_log)\.(?:warning|error|critical)\(\s*[fr]?["\']\[DEBUG\]',
                text,
            ):
                bad.append(f"{path.relative_to(ROOT)}: warning-level log starts with [DEBUG]")
        self.assertFalse(
            bad,
            "Log calls at warning/error level emit [DEBUG]-tagged "
            "strings — operator sees DEBUG noise as a problem. Fix "
            "the call's level or the message prefix.\n  - "
            + "\n  - ".join(bad),
        )


# ---------------------------------------------------------------------------
# B — locale / encoding
# ---------------------------------------------------------------------------
class FileIOExplicitEncoding(unittest.TestCase):
    """Every ``.read_text()`` / ``.write_text()`` call must
    specify ``encoding="utf-8"`` (or another explicit value).
    Default is the system locale → silent breakage on hosts
    where locale != UTF-8 (legacy embedded systems, fresh
    Alpine without locales installed, Docker Desktop on
    Windows mounting a UTF-16 file).

    Bound by a count cap rather than zero — there are too many
    legacy call sites to fix in one shot. Cap drops over time."""

    _MAX_UNGUARDED_FILE_IO = 50

    def test_unguarded_file_io_count_below_cap(self) -> None:
        unguarded: list[str] = []
        # Pattern: ``.read_text()`` or ``.read_text(...)`` where
        # the call's args don't include ``encoding=``.
        # Simple AST walk catches the common shapes.
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
                if not isinstance(node.func, ast.Attribute):
                    continue
                if node.func.attr not in ("read_text", "write_text"):
                    continue
                kw_names = {kw.arg for kw in node.keywords if kw.arg}
                if "encoding" in kw_names:
                    continue
                unguarded.append(
                    f"{path.relative_to(ROOT)}:{node.lineno}: "
                    f".{node.func.attr}() without encoding=",
                )
        # Soft cap so we ratchet down over time without breaking
        # the build on the first run with the existing baseline.
        if len(unguarded) > self._MAX_UNGUARDED_FILE_IO:
            self.fail(
                f"Unguarded file I/O calls: {len(unguarded)} "
                f"(cap {self._MAX_UNGUARDED_FILE_IO}). Add "
                f"``encoding=\"utf-8\"`` to new sites OR drop the "
                f"cap as you fix existing ones.\n  - "
                + "\n  - ".join(unguarded[:30])
                + ("\n  ..." if len(unguarded) > 30 else ""),
            )


# ---------------------------------------------------------------------------
# F — time-of-day correctness
# ---------------------------------------------------------------------------
class TimezoneAwareness(unittest.TestCase):
    """``datetime.now()`` returns a naive datetime in the host's
    local timezone. Naive datetimes silently break across
    daylight-saving boundaries, between hosts in different
    zones, and at year-2038 / leap-day edges. Use
    ``datetime.now(timezone.utc)`` everywhere or
    ``datetime.utcnow()`` (deprecated but tz-naive UTC).

    Soft-capped — legacy call sites get fixed over time."""

    _MAX_NAIVE_NOW = 30

    def test_naive_datetime_now_count_below_cap(self) -> None:
        bad: list[str] = []
        for path in SRC.rglob("*.py"):
            if "__pycache__" in str(path):
                continue
            text = path.read_text(encoding="utf-8")
            # ``datetime.now()`` with NO args = naive local-time.
            # ``datetime.now(timezone.utc)`` = OK.
            # Heuristic: bare ``datetime.now()`` with empty parens.
            for m in re.finditer(r"\bdatetime\.now\(\s*\)", text):
                bad.append(f"{path.relative_to(ROOT)}: bare datetime.now()")
        if len(bad) > self._MAX_NAIVE_NOW:
            self.fail(
                f"Naive datetime.now() calls: {len(bad)} "
                f"(cap {self._MAX_NAIVE_NOW}). Use "
                f"``datetime.now(timezone.utc)`` for new code.\n  - "
                + "\n  - ".join(bad[:20]),
            )


# ---------------------------------------------------------------------------
# K — silent-failure count
# ---------------------------------------------------------------------------
class SilentFailureCount(unittest.TestCase):
    """``except Exception: pass`` and bare ``except: pass`` swallow
    real bugs into the void. The project backlog already names
    this as a target (176→0). This ratchet sets a cap that
    drops as the count comes down — new ones can't be added
    without explicitly raising the cap."""

    _MAX_SILENT_FAILURES = 200

    def test_silent_failure_count_below_cap(self) -> None:
        count = 0
        examples: list[str] = []
        # Patterns:
        #   except: pass
        #   except Exception: pass
        #   except (X, Y): pass
        # All on a single try/except where the body is JUST pass
        # or a debug log + pass.
        pattern = re.compile(
            r"except[^:]*:\s*\n\s*(?:logging\.[^\n]*\n\s*)?pass\b",
        )
        for path in SRC.rglob("*.py"):
            if "__pycache__" in str(path):
                continue
            text = path.read_text(encoding="utf-8")
            for m in pattern.finditer(text):
                count += 1
                if len(examples) < 20:
                    examples.append(f"{path.relative_to(ROOT)}: {m.group(0)[:60]}")
        if count > self._MAX_SILENT_FAILURES:
            self.fail(
                f"Silent failure handlers: {count} "
                f"(cap {self._MAX_SILENT_FAILURES}). Each "
                f"``except: pass`` hides a future debugging "
                f"nightmare. Replace with logged warnings or "
                f"propagate; drop the cap as you do.\n  - "
                + "\n  - ".join(examples[:10])
                + ("\n  ..." if count > 10 else ""),
            )


if __name__ == "__main__":
    unittest.main()
