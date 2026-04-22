"""Batch 6 ratchets shipped in v1.0.122.

Bugs found in the live deploy that the existing 5-batch suite
didn't catch — adding ratchets for each class so they can never
regress without lighting up CI.

Bug classes covered:

  L1   ``urllib.request.urlopen`` POST/PUT/DELETE without manual
       redirect handling. urllib silently drops the body on a 307,
       which is the failure mode that caused tag-creation to no-op
       in v1.0.110 → v1.0.120 (qBit wasn't downloading because
       Prowlarr's ``sync-sonarr`` tag never existed). Any new
       servarr-style HTTP call MUST go through
       ``_make_servarr_http_request()`` or carry a comment
       explaining how it survives URL-base redirects.
  L2   compose ``CONFIG_ROOT`` (and MEDIA_ROOT, DATA_ROOT) default
       must use ``../config`` (parent-of-compose-file) rather than
       ``./config`` (compose-file-dir). ``./config`` resolves
       differently when the same compose file is loaded from
       different directories — the bug that put the controller's
       ``/srv-config`` mount at one host path while the apps'
       ``/config`` mount went to a different one, so the controller
       couldn't read API keys from disk and ``discover-api-keys``
       silently failed.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "media_stack"
sys.path.insert(0, str(ROOT / "src"))


# ---------------------------------------------------------------------------
# L1 — urllib POST/PUT/DELETE must handle 307 redirects
# ---------------------------------------------------------------------------
class UrllibPostHandlesRedirects(unittest.TestCase):
    """Any source-file urllib usage that does POST / PUT / DELETE
    must route through a wrapper that survives URL-base 307
    redirects. The reference implementation is
    ``_make_servarr_http_request()`` in
    ``services/apps/core/job_adapters.py``."""

    _ALLOWED_FILES = {
        # The reference helper itself.
        "src/media_stack/services/apps/core/job_adapters.py",

        # qBittorrent — no URL base prefix. POSTs to its WebUI
        # endpoints (/api/v2/auth/login, etc.) don't redirect.
        "src/media_stack/services/apps/qbittorrent/admin_ops.py",

        # Outbound to user-supplied URLs. Caller is responsible
        # for the destination's redirect behavior; we don't control
        # it. (Telemetry endpoint, alert webhook, generic webhook
        # broadcast.)
        "src/media_stack/services/telemetry_client.py",
        "src/media_stack/cli/workflows/controller_notification_service.py",
        "src/media_stack/api/webhooks.py",
        "src/media_stack/api/server.py",  # webhook broadcast loop

        # Jellyfin endpoints. Jellyfin doesn't enforce a URL-base
        # prefix unless ``NetworkConfiguration.BaseUrl`` is set on
        # the server side, which we never set. POSTs to /Users/...,
        # /Library/Refresh, /Auth/Keys go through unredirected.
        "src/media_stack/services/apps/jellyfin/admin_ops.py",
        "src/media_stack/api/services/health.py",
        "src/media_stack/api/handlers_post.py",
        "src/media_stack/api/services/admin.py",

        # content.py — TODO migrate to _make_servarr_http_request.
        # These call *arr DELETE on indexer/import-list paths and
        # ARE in the same bug class as the v1.0.121 fix. Allow-listed
        # for the v1.0.122 ratchet introduction; tracked for fix.
        # Keeping the entry in this set is itself a TODO marker.
        "src/media_stack/api/services/content.py",
    }

    def test_no_unguarded_urllib_post(self) -> None:
        # Find urllib.request.Request(...) calls with method="POST"|"PUT"|"DELETE"
        # in src/ outside the allow-list.
        bad: list[str] = []
        pat = re.compile(
            r"""urllib\.request\.Request\([^)]*method\s*=\s*['"](?:POST|PUT|DELETE|PATCH)['"]""",
            re.DOTALL,
        )
        for path in SRC.rglob("*.py"):
            if "__pycache__" in str(path):
                continue
            rel = str(path.relative_to(ROOT))
            if rel in self._ALLOWED_FILES:
                continue
            text = path.read_text(encoding="utf-8")
            for m in pat.finditer(text):
                line_no = text[:m.start()].count("\n") + 1
                bad.append(f"{rel}:{line_no}")
        self.assertFalse(
            bad,
            f"urllib.request.Request(method='POST'|...) outside the "
            f"allow-list. urllib drops POST body on 307 — route "
            f"through _make_servarr_http_request() in "
            f"job_adapters.py or document why this call is safe.\n"
            f"  - " + "\n  - ".join(bad),
        )


# ---------------------------------------------------------------------------
# L2 — compose root paths default to ../<dir>, not ./<dir>
# ---------------------------------------------------------------------------
class ComposeRelativePathConsistency(unittest.TestCase):
    """``${CONFIG_ROOT:-./config}`` resolves relative to the
    compose file's directory. When the same compose file is loaded
    via two different paths (or two compose files in sibling
    dirs), ``./config`` lands in different host directories. The
    fix: default to ``../config`` so both ``docker/`` and
    ``dist/`` files resolve to ``media-automation-stack/config/``
    consistently."""

    _ROOT_VARS = ("CONFIG_ROOT", "MEDIA_ROOT", "DATA_ROOT")

    def test_compose_files_use_parent_relative_paths(self) -> None:
        bad: list[str] = []
        for name in ("docker/docker-compose.yml", "dist/docker-compose.yml"):
            path = ROOT / name
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            for var in self._ROOT_VARS:
                # Match `${VAR:-./<path>}` — the bad pattern.
                pat = re.compile(
                    rf"\$\{{{var}:-\./[A-Za-z0-9_./-]+\}}"
                )
                for m in pat.finditer(text):
                    line_no = text[:m.start()].count("\n") + 1
                    bad.append(f"{name}:{line_no}: {m.group(0)}")
        self.assertFalse(
            bad,
            f"Compose ROOT vars use './<dir>' which resolves "
            f"relative to the compose-file directory and diverges "
            f"between docker/ and dist/. Use '../<dir>' so both "
            f"resolve to the same parent dir:\n  - "
            + "\n  - ".join(bad[:15]),
        )


# ---------------------------------------------------------------------------
# L3 — Prowlarr search categories use repeated-param query, not CSV
# ---------------------------------------------------------------------------
class ProwlarrSearchCategoriesRepeatedParam(unittest.TestCase):
    """Prowlarr's ``/api/v1/search`` endpoint requires
    ``categories`` to be supplied as REPEATED query params
    (``categories=2000&categories=2010&...``). A comma-separated
    list returns HTTP 400 with::

        "value '2000,2010,2020' is not a valid categories value"

    The ``_probe_indexer_for_app`` function in
    ``services/apps/prowlarr/indexer_app_match.py`` mis-encoded
    this as CSV from v1.0.105 → v1.0.121, so every indexer probe
    returned 400 → False, every indexer was classified ``apps=[]``,
    cache poisoned for the project's whole indexer-tagging history,
    and Sonarr/Radarr ended up with zero indexers despite Prowlarr
    having dozens. Fixed v1.0.122."""

    def test_no_csv_categories_param_in_prowlarr_search(self) -> None:
        path = SRC / "services" / "apps" / "prowlarr" / "indexer_app_match.py"
        if not path.is_file():
            self.skipTest("indexer_app_match.py not present")
        text = path.read_text(encoding="utf-8")
        # The reference fix uses `&".join(f"categories={c}" for c in cats)`
        # The bad pattern is `",".join(str(c) for c in cats)` immediately
        # near a `categories=` substring.
        self.assertNotRegex(
            text,
            r'cat_param\s*=\s*",".*?join.*?categories=\{cat_param\}',
            "indexer_app_match._probe_indexer_for_app builds "
            "`categories=` as a CSV again — Prowlarr will return "
            "HTTP 400 and every indexer probe will silently fail. "
            "Use repeated query params: "
            '"&".join(f"categories={c}" for c in cats)',
        )
        # And the positive assertion: the fix shape is present.
        self.assertIn(
            'f"categories={c}" for c in cats',
            text,
            "indexer_app_match._probe_indexer_for_app no longer "
            "uses repeated-param categories — Prowlarr's "
            "/api/v1/search requires the repeated form.",
        )


# ---------------------------------------------------------------------------
# L4 — onclick handlers in dashboard.html call defined JS functions
#       (with the right shape if they're known navigation calls)
# ---------------------------------------------------------------------------
class DashboardOnclickHandlersResolve(unittest.TestCase):
    """Every ``onclick="someFunc(...)"`` in dashboard.html must
    reference a function that's actually defined in the same file.
    The "Config Drift / N issue →" link silently no-op'd because
    its onclick called ``goToDriftTab()`` which then called
    ``showSubTab('cfg-drift')`` without the second ``btn`` arg —
    ``showSubTab`` immediately did ``btn.closest()`` and threw,
    leaving the user staring at an unresponsive link.

    This catches:
    1. ``onclick="undefinedFn(...)"`` — typo or rename drift.
    2. ``onclick="showSubTab('id')"`` (single-arg) — the exact
       bug shape we just hit. ``showSubTab`` requires two args
       (id, btn) unless called from inline ``onclick`` where
       ``this`` is the button.
    """

    # JS keywords / control-flow that look like function calls
    # but aren't.
    _JS_KEYWORDS = {
        "if", "else", "for", "while", "switch", "return", "throw",
        "new", "typeof", "instanceof", "delete", "void", "in", "of",
        "do", "try", "catch", "finally", "yield", "await", "async",
        "function", "class", "extends", "super", "import", "export",
        "default", "case", "break", "continue", "with", "var", "let",
        "const",
    }

    def test_onclick_handlers_reference_defined_functions(self) -> None:
        dash = (SRC / "api" / "dashboard.html").read_text(encoding="utf-8")
        # Extract function names defined in <script> blocks.
        defined = set(re.findall(r"\bfunction\s+([A-Za-z_$][\w$]*)\s*\(", dash))
        defined |= set(re.findall(
            r"\b([A-Za-z_$][\w$]*)\s*=\s*(?:async\s+)?function\b", dash,
        ))
        # const/let/var X = (...) => ...   OR   X = function ...
        defined |= set(re.findall(
            r"^\s*(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=",
            dash, re.MULTILINE,
        ))
        # Inline arrow assignment without var/let/const
        defined |= set(re.findall(
            r"\b([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>",
            dash,
        ))
        # Browser/DOM globals + project-wide always-defined helpers.
        defined |= {
            "window", "document", "console", "setTimeout", "setInterval",
            "clearInterval", "clearTimeout", "alert", "confirm", "prompt",
            "fetch", "Promise", "Object", "Array", "String",
            "Number", "Date", "Math", "RegExp", "encodeURIComponent",
            "decodeURIComponent", "parseInt", "parseFloat", "isNaN",
            "event", "this", "msUI", "JSON",
        }
        defined |= self._JS_KEYWORDS

        # Find every onclick="X(..." and pull X.
        bad: list[str] = []
        for m in re.finditer(
            r'''onclick\s*=\s*["']([A-Za-z_$][\w$.]*)\(''', dash,
        ):
            fn = m.group(1).split(".")[0]
            if fn in defined:
                continue
            line_no = dash[:m.start()].count("\n") + 1
            bad.append(f"line {line_no}: {fn}(...)")
        self.assertFalse(
            bad,
            f"Dashboard onclick handlers reference undefined "
            f"functions ({len(bad)}):\n  - "
            + "\n  - ".join(bad[:10]),
        )

    def test_show_sub_tab_calls_pass_button_argument(self) -> None:
        """``showSubTab(id, btn)`` requires the button so it can
        find its parent .tab-content. Single-arg calls from
        cross-tab navigation (not inline ``onclick``) silently
        threw before. Allow inline-onclick form where ``btn=this``
        is implicit and pre-resolved at parse time."""
        dash = (SRC / "api" / "dashboard.html").read_text(encoding="utf-8")
        # ``onclick="showSubTab(...)"`` is fine — `this` is
        # available. Plain ``showSubTab('id')`` from a function
        # body is the bug.
        bad: list[str] = []
        for m in re.finditer(
            r"(?<!onclick=[\"'])showSubTab\(\s*['\"][^'\"]+['\"]\s*\)",
            dash,
        ):
            line_no = dash[:m.start()].count("\n") + 1
            # Skip if this match IS inside an onclick attr (regex
            # lookbehind only checks the immediately-preceding char).
            seg = dash[max(0, m.start()-200):m.start()]
            if 'onclick="' in seg.split('"')[-1] or "onclick='" in seg.split("'")[-1]:
                continue
            bad.append(f"line {line_no}: {m.group(0).strip()}")
        self.assertFalse(
            bad,
            f"showSubTab() called without a btn arg "
            f"({len(bad)} sites). It needs a real button to find "
            f"the parent .tab-content. Pass the button explicitly:\n  - "
            + "\n  - ".join(bad[:10]),
        )


# ---------------------------------------------------------------------------
# L5 — probe-cache must not store a "no match" entry on probe failure
# ---------------------------------------------------------------------------
class ProbeCacheRejectsFailureAsResult(unittest.TestCase):
    """``indexer_app_match._resolve_per_indexer_apps`` writes to
    the indexer-app-match cache after probing each indexer × app.
    If the probe call ITSELF fails (HTTP 401, 400, network error),
    the function stored ``apps=[]`` — which the cache then served
    back as the authoritative answer ("no app matches this
    indexer"), poisoning the cache for the entire TTL window
    (24h by default). Any cache write of ``apps=[]`` must be
    accompanied by an explicit success signal — not a silent
    fall-through from a broken probe.

    Currently soft-asserted via comment scan; the real fix is to
    refactor ``_probe_indexer_for_app`` to return a 3-state
    ``(matched, probed_ok)`` tuple. Tracked for next session."""

    def test_resolve_per_indexer_apps_warns_on_no_results(self) -> None:
        path = SRC / "services/apps/prowlarr/indexer_app_match.py"
        if not path.is_file():
            self.skipTest("indexer_app_match not present")
        text = path.read_text(encoding="utf-8")
        # Today's marker that the no-match path is at least logged.
        # The deeper fix (don't cache on probe-failure) lives in a
        # follow-up commit; this ratchet pins the LOG and prevents
        # someone from quietly removing it.
        self.assertIn(
            "no app match",
            text,
            "indexer_app_match no longer logs the per-indexer "
            "'no app match' line — operators lose the only signal "
            "that a fresh probe ran AND classified the indexer "
            "as no-match.",
        )


# ---------------------------------------------------------------------------
# L6 — auto-refreshing dashboard sections preserve user UI state
# ---------------------------------------------------------------------------
class AutoRefreshPreservesUserState(unittest.TestCase):
    """Any dashboard JS function that BOTH:

      1. assigns ``el.innerHTML = ...`` (full re-render), AND
      2. self-schedules a refresh via ``setTimeout(...) => loadX``

    MUST have a state-preservation block — without it, the
    auto-refresh wipes anything the user just expanded
    (``<details open>``), scrolled to, or focused. The Job-tree
    Execution-History "details that keep closing" bug was exactly
    this pattern.

    The marker we look for is a comment containing "Preserve" or
    "preserve" near the el.innerHTML+setTimeout combo, OR a
    ``_openDetailsKeys`` / ``_scrollY`` variable name nearby.
    Rough heuristic — false positives are easy, false negatives
    catch the bug class."""

    def test_self_refreshing_renders_preserve_state(self) -> None:
        dash = (SRC / "api" / "dashboard.html").read_text(encoding="utf-8")
        # Find every async function that does both el.innerHTML= AND
        # setTimeout calling itself.
        bad: list[str] = []
        # Function bodies are ugly to extract reliably from raw text;
        # use a heuristic: split on `function NAME(` boundaries and
        # check each chunk.
        chunks = re.split(r"(?=\bfunction\s+\w+\s*\()", dash)
        for chunk in chunks:
            m_name = re.match(r"\bfunction\s+(\w+)\s*\(", chunk)
            if not m_name:
                continue
            name = m_name.group(1)
            # Stop at next top-level function or async function defn
            # (already split). Limit chunk to first 4000 chars.
            body = chunk[: 4000]
            self_refresh = re.search(
                rf"setTimeout\s*\(\s*\(?\)?\s*=>\s*\{{[^}}]*\b{re.escape(name)}\s*\(",
                body,
            )
            innerhtml = "innerHTML=" in body or "innerHTML =" in body
            if not (self_refresh and innerhtml):
                continue
            preserves = bool(
                re.search(
                    r"(?i)preserve|_openDetails|_scrollY|details\[open\]",
                    body,
                )
            )
            if not preserves:
                bad.append(name)
        self.assertFalse(
            bad,
            f"Dashboard functions that auto-refresh (setTimeout "
            f"self-call) and rebuild innerHTML — but don't restore "
            f"user-expanded <details>/scroll. The next refresh wipes "
            f"the user's expand. Add a state-preservation block "
            f"(see loadJobTree() for a reference):\n  - "
            + "\n  - ".join(bad),
        )


# ---------------------------------------------------------------------------
# L7 — meta: every fix-commit either adds a ratchet or declares N/A
# ---------------------------------------------------------------------------
class FixCommitsTouchRatchets(unittest.TestCase):
    """For every recent commit whose message marks a fix
    (``fix``, ``bug``, ``regression``, ``FIXED``), at least one of:

      a) a file matching ``tests/unit/test_v*_batch*_ratchets.py``
         was modified in that commit, OR
      b) the commit message contains ``Ratchet: N/A`` (with any
         trailing reason — usually a one-off typo or message wording)

    The rule forces the AUTHOR to consciously decide whether the
    bug is a recurring CLASS or a one-off — instead of silently
    shipping fixes that the next mistake of the same shape will
    re-introduce. This is the principle the user articulated:
    *'every fix should answer the question — is this a class?'*

    Scope: last 50 commits (cheap; CI-friendly). Skipped on
    detached-HEAD or shallow clones."""

    _FIX_TOKENS = re.compile(r"\b(fix|bug|regression|FIXED)\b", re.IGNORECASE)
    _RATCHET_NA = re.compile(r"Ratchet:\s*N/?A\b", re.IGNORECASE)
    _RATCHET_FILE_HINT = re.compile(r"tests/unit/test_v[\d_]+_batch.*_ratchets\.py")

    # Subjects that are pure version-bumps / chore commits aren't
    # fixes even if their bodies say "fixes ...".
    _SKIP_SUBJECT_PREFIX = (
        "v1.0.", "Bump ", "Release ", "chore:", "docs:",
    )

    # Baseline tag — only commits AFTER this one are subject to the
    # rule. v1.0.123 was the release where this ratchet shipped, so
    # everything before it is grandfathered without re-litigating.
    _BASELINE_TAG = "v1.0.123"

    def test_recent_fix_commits_have_ratchet_or_na(self) -> None:
        import subprocess
        # Resolve baseline. If the tag doesn't exist yet, the rule
        # is dormant — nothing to enforce.
        try:
            subprocess.check_output(
                ["git", "rev-parse", self._BASELINE_TAG],
                cwd=ROOT, stderr=subprocess.DEVNULL,
            )
        except Exception:
            self.skipTest(
                f"baseline tag {self._BASELINE_TAG} not present "
                f"yet — rule starts after that tag is created"
            )
        try:
            log = subprocess.check_output(
                ["git", "log", f"{self._BASELINE_TAG}..HEAD",
                 "--name-only", "--pretty=format:%H%x00%s%x00%b%x01"],
                cwd=ROOT, stderr=subprocess.DEVNULL,
            ).decode("utf-8")
        except Exception:
            self.skipTest("git not available")
        if not log.strip():
            self.skipTest(f"no commits since {self._BASELINE_TAG}")

        bad: list[str] = []
        for entry in log.split("\x01"):
            entry = entry.strip()
            if not entry:
                continue
            parts = entry.split("\x00", 2)
            if len(parts) < 3:
                continue
            sha = parts[0].strip()
            subject = parts[1].strip()
            rest = parts[2]
            lines = rest.splitlines()
            body_lines: list[str] = []
            file_lines: list[str] = []
            in_files = False
            for ln in lines:
                # The git --name-only filename block starts after a
                # blank line. Heuristic: a non-indented line ending
                # with a known file extension.
                if (not ln.strip()) and not in_files:
                    in_files = True
                    continue
                if in_files:
                    if ln.strip():
                        file_lines.append(ln.strip())
                else:
                    body_lines.append(ln)
            full_msg = subject + "\n" + "\n".join(body_lines)
            if any(subject.startswith(p) for p in self._SKIP_SUBJECT_PREFIX):
                continue
            if not self._FIX_TOKENS.search(full_msg):
                continue
            if self._RATCHET_NA.search(full_msg):
                continue
            if any(self._RATCHET_FILE_HINT.search(f) for f in file_lines):
                continue
            bad.append(f"{sha[:8]} {subject[:80]}")
        self.assertFalse(
            bad,
            f"Fix-commits since {self._BASELINE_TAG} with no ratchet "
            f"AND no 'Ratchet: N/A' declaration ({len(bad)} commits). "
            f"Either add a regression test in "
            f"tests/unit/test_v*_batch*_ratchets.py, OR add a line "
            f"'Ratchet: N/A — <reason>' to the commit message:\n  - "
            + "\n  - ".join(bad[:10]),
        )


# ---------------------------------------------------------------------------
# L8 — Prowlarr ApplicationIndexerMapping staleness
# ---------------------------------------------------------------------------
class ProwlarrApplicationMappingReset(unittest.TestCase):
    """Prowlarr tracks what it has already pushed to each *arr in
    its ``ApplicationIndexerMapping`` DB table. In ``addOnly`` sync
    mode (the v1.0.110 Fix C default), Prowlarr SKIPS any
    indexer-to-app pair that already appears in the mapping table.

    Failure mode: if the *arr's actual DB is wiped (fresh install,
    restore-from-empty-backup, ``down -v`` without ``down -v`` on
    Prowlarr too), Prowlarr's mapping table still claims "synced"
    and refuses to re-push. The *arr ends up with 0 indexers
    despite Prowlarr's "sync successful" reports, which is exactly
    the state the v1.0.124-era deploy was stuck in.

    The controller's bootstrap runner must either:
      a) reconcile Prowlarr's mappings against each *arr's actual
         indexer list on startup (delete orphaned mapping rows), OR
      b) expose a "force resync" button / job that clears the
         mapping table and retriggers ApplicationIndexerSync.

    Marker: the controller codebase must either reference the
    ``ApplicationIndexerMapping`` table by name OR call a
    dedicated ``reset_prowlarr_app_mappings()`` helper. Starting
    with only the marker; the full reconciliation is a follow-up
    commit."""

    def test_controller_acknowledges_application_mapping_reconciliation(self) -> None:
        # Search the whole source tree for a mention that proves
        # somebody thought about this failure mode. Either:
        #   - the table name ApplicationIndexerMapping appears in a
        #     comment, handler, or job-adapter
        #   - a named helper reset_prowlarr_app_mappings exists
        marker_found = False
        for path in SRC.rglob("*.py"):
            if "__pycache__" in str(path):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if "ApplicationIndexerMapping" in text:
                marker_found = True
                break
            if "reset_prowlarr_app_mappings" in text:
                marker_found = True
                break
        self.assertTrue(
            marker_found,
            "No code path acknowledges Prowlarr's "
            "ApplicationIndexerMapping staleness failure mode. "
            "Sonarr/Radarr/Lidarr/Readarr can show 0 indexers "
            "while Prowlarr's mapping table claims 'synced' — "
            "addOnly syncLevel refuses to re-push. Add either:\n"
            "  * a comment/handler referencing "
            "``ApplicationIndexerMapping`` by name in the indexer "
            "reconciliation path, OR\n"
            "  * a ``reset_prowlarr_app_mappings()`` helper that "
            "DELETEs the table and retriggers "
            "ApplicationIndexerSync when the *arr's actual indexer "
            "count is 0.",
        )


# ---------------------------------------------------------------------------
# L9 — indexer-chain job sequencing in core.yaml
# ---------------------------------------------------------------------------
class IndexerChainJobSequencing(unittest.TestCase):
    """The download_clients phase has four jobs that MUST run in
    this order::

        discover-indexers          (priority 30)
        tag-indexers-for-apps      (priority 35)
        reset-prowlarr-app-mappings (priority 38)  <-- v1.0.125
        push-indexers              (priority 40)

    Reset MUST run AFTER tag (so we don't reset mappings while
    tagging is in flight) and BEFORE push (so the next push sees
    a clean slate when an *arr is at zero).

    If someone re-orders priorities or removes a job, this
    ratchet catches it before deploy."""

    _EXPECTED_ORDER = [
        ("discover-indexers", 30),
        ("tag-indexers-for-apps", 35),
        ("reset-prowlarr-app-mappings", 38),
        ("push-indexers", 40),
    ]

    def test_indexer_chain_jobs_priority_ordered(self) -> None:
        try:
            import yaml as _yaml
        except ImportError:
            self.skipTest("PyYAML not installed")
        path = ROOT / "contracts" / "services" / "core.yaml"
        if not path.is_file():
            self.skipTest("core.yaml not present")
        doc = _yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        jobs = ((doc.get("plugin") or {}).get("jobs") or {})
        observed: list[tuple[str, int]] = []
        for name, expected_pri in self._EXPECTED_ORDER:
            job = jobs.get(name)
            self.assertIsNotNone(
                job,
                f"core.yaml is missing the indexer-chain job "
                f"{name!r}. Reset is the v1.0.125 fix for the "
                f"Prowlarr stale-mapping bug — removing it is the "
                f"exact regression that ratchet L8 + this one "
                f"prevent.",
            )
            self.assertEqual(
                int(job.get("priority", -1)),
                expected_pri,
                f"core.yaml::{name} priority drift "
                f"(want {expected_pri}, got {job.get('priority')}). "
                f"The four indexer-chain jobs are sequenced 30 → 35 "
                f"→ 38 → 40 so reset runs AFTER tagging and BEFORE "
                f"push. Re-ordering breaks the bullet-proof chain.",
            )
            self.assertEqual(
                job.get("phase"), "download_clients",
                f"core.yaml::{name} phase must be download_clients",
            )
            observed.append((name, int(job["priority"])))
        # Strictly ascending by priority — no ties allowed.
        for i in range(1, len(observed)):
            self.assertGreater(
                observed[i][1], observed[i-1][1],
                f"core.yaml indexer-chain priorities not strictly "
                f"ascending: {observed}",
            )


# ---------------------------------------------------------------------------
# L10 — release pipeline runs the version-parity ratchet pre-build
# ---------------------------------------------------------------------------
class RegenDistInvokesVersionParity(unittest.TestCase):
    """We HAVE a ratchet for image-version drift
    (``ControllerImageVersionParity``, Batch 4) — but in v1.0.126
    the bug it would have caught shipped anyway because nobody ran
    the ratchet between bumping VERSION and ``bin/build-controller-image.sh``.
    The user's sed expected v1.0.125 → v1.0.126 but the file was
    still at v1.0.123 (skipped two intermediate version bumps), so
    the substitution was a NOOP and the deployed controller ran
    the old image.

    Ratchets only protect you if the release pipeline RUNS them.
    ``bin/regen-dist.sh`` is the natural choke-point — every
    release runs it before the build. Pin that it invokes the
    version-parity check."""

    def test_regen_dist_runs_version_parity(self) -> None:
        path = ROOT / "bin" / "regen-dist.sh"
        if not path.is_file():
            self.skipTest("regen-dist.sh not present")
        text = path.read_text(encoding="utf-8")
        self.assertIn(
            "ControllerImageVersionParity",
            text,
            "bin/regen-dist.sh must invoke the "
            "ControllerImageVersionParity ratchet before regen — "
            "otherwise version-tag drift ships into the dist "
            "bundles and into harbor with old image refs.",
        )


# ---------------------------------------------------------------------------
# L11 — *arr indexer-name comparison strips Prowlarr suffix
# ---------------------------------------------------------------------------
class ArrIndexerNameStripsProwlarrSuffix(unittest.TestCase):
    """When comparing *arr indexer names against Prowlarr's
    source-of-truth list (the v1.0.110 stale-reconciliation
    pipeline in indexer_sync_service.reconcile()), the *arr name
    MUST strip the trailing ``" (Prowlarr)"`` suffix. Prowlarr
    appends this suffix when adding an indexer to a *arr; the
    Prowlarr-side record uses the bare name. Without the strip,
    every reconcile classifies every Prowlarr-managed indexer as
    "stale" and DELETEs it from the *arr, immediately undoing the
    push. (Discovered v1.0.127 — Sonarr/Radarr would lose all
    indexers seconds after every successful push.)"""

    def test_arr_indexer_name_strips_prowlarr_suffix(self) -> None:
        sys.path.insert(0, str(SRC.parent.parent))
        from media_stack.services.apps.prowlarr.indexer_sync_service import (
            ArrIndexerSyncService,
        )
        # Direct unit test of the helper (no http_request needed).
        # ``_arr_indexer_name`` is a @staticmethod, callable on the class.
        cases = [
            # (input dict, expected stripped name)
            ({"name": "AnimeTosho (Prowlarr)"}, "AnimeTosho"),
            ({"name": "AnimeTosho (prowlarr)"}, "AnimeTosho"),  # case-insensitive
            ({"name": "AnimeTosho"}, "AnimeTosho"),  # no-op when no suffix
            ({"name": "Some Index (Prowlarr) "}, "Some Index"),
        ]
        for input_dict, expected in cases:
            got = ArrIndexerSyncService._arr_indexer_name(input_dict)
            self.assertEqual(
                got, expected,
                f"_arr_indexer_name({input_dict!r}) expected "
                f"{expected!r}, got {got!r}. Without this strip, "
                f"reconcile() will treat every Prowlarr-managed "
                f"*arr indexer as stale and DELETE it.",
            )


# ---------------------------------------------------------------------------
# L12 — FlareSolverr proxy auto-tags with sync-* so it attaches to indexers
# ---------------------------------------------------------------------------
class FlareSolverrProxyAutoTagsSyncIndexers(unittest.TestCase):
    """Prowlarr only routes an indexer through a proxy when the
    indexer's tags overlap with the proxy's tags. ``FlareSolverr``
    shipped with empty tags by default → attached to NOTHING →
    every CloudFlare-protected indexer (Knaben, The Pirate Bay,
    TorrentDownload, Uindex, etc.) returned the CF challenge HTML
    to Sonarr/Radarr, which then crashed on ``Invalid torrent file``
    and the *arr queue filled with ``downloadClientUnavailable``.

    The fix in ``proxy_ops.ensure_flaresolverr_proxy`` auto-fetches
    every ``sync-*`` tag from Prowlarr when the operator hasn't
    pinned tags via cfg, so FlareSolverr applies to every indexer
    the controller pushes to a *arr. Pin the auto-fetch behavior
    so it can't silently regress.

    Discovered v1.0.130 — final piece of the qBit-not-downloading
    chain that ran v1.0.121 → v1.0.130."""

    def test_proxy_ops_auto_attaches_sync_tags(self) -> None:
        path = SRC / "services/apps/prowlarr/proxy_ops.py"
        if not path.is_file():
            self.skipTest("proxy_ops.py not present")
        text = path.read_text(encoding="utf-8")
        # Auto-tag block must be present.
        self.assertIn(
            "/api/v1/tag",
            text,
            "proxy_ops.ensure_flaresolverr_proxy no longer queries "
            "Prowlarr's tag list to auto-attach. Without this, the "
            "proxy ships with empty tags and Prowlarr never invokes "
            "FlareSolverr — every CloudFlare-protected indexer "
            "returns HTML to *arrs and qBit stays at 0 downloads.",
        )
        self.assertIn(
            'startswith("sync-")',
            text,
            "proxy_ops no longer filters tags by the 'sync-' prefix. "
            "Other tags (operator-defined manual tags) shouldn't "
            "drag the proxy's attachment scope.",
        )


# ---------------------------------------------------------------------------
# L13 — usenet indexers not tagged when no usenet download client is reachable
# ---------------------------------------------------------------------------
class UsenetIndexersSkippedWhenSabUnavailable(unittest.TestCase):
    """When SABnzbd (our only usenet download client) is off or
    unreachable, ``tag-indexers-for-apps`` must SKIP usenet
    indexers. Otherwise Sonarr/Radarr grab NZBs and hand them to
    their torrent client (qBit), which reads the bytes as a
    torrent and crashes with
    ``MonoTorrent.TorrentException: Invalid torrent file``. The
    *arr queue fills with ``downloadClientUnavailable`` and qBit
    stays at 0 downloads. (v1.0.130 — 7th and final bug in the
    qBit-not-downloading chain.)"""

    def test_usenet_indexer_gated_on_sab_reachability(self) -> None:
        path = SRC / "services/apps/prowlarr/indexer_app_match.py"
        if not path.is_file():
            self.skipTest("indexer_app_match not present")
        text = path.read_text(encoding="utf-8")
        self.assertIn(
            'idx.get("protocol") == "usenet"',
            text,
            "apply_indexer_app_tags no longer checks the indexer "
            "protocol — usenet indexers will get tagged even when "
            "SAB is off, and *arrs will crash on NZB bytes as "
            "torrent.",
        )
        self.assertIn(
            "sab_reachable",
            text,
            "Protocol gating must consult SAB's reachability. If "
            "SAB is reachable it's fine to tag usenet indexers; "
            "if not, skip them with a clear [INFO] log line.",
        )


if __name__ == "__main__":
    unittest.main()
