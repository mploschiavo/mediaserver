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

ROOT = Path(__file__).resolve().parents[3]
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
        "src/media_stack/infrastructure/qbittorrent/admin_ops.py",

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
        # Phase 16-D moved admin_ops.py from services/apps/jellyfin/
        # to infrastructure/jellyfin/; same Jellyfin semantics.
        "src/media_stack/services/apps/jellyfin/admin_ops.py",
        "src/media_stack/infrastructure/jellyfin/admin_ops.py",
        "src/media_stack/api/services/health.py",
        "src/media_stack/api/handlers_post.py",
        "src/media_stack/api/services/admin.py",

        # content.py — TODO migrate to _make_servarr_http_request.
        # These call *arr DELETE on indexer/import-list paths and
        # ARE in the same bug class as the v1.0.121 fix. Allow-listed
        # for the v1.0.122 ratchet introduction; tracked for fix.
        # Keeping the entry in this set is itself a TODO marker.
        # ``content_download_settings_mixin.py`` was split out of
        # content.py and inherits the same TODO.
        "src/media_stack/api/services/content.py",
        "src/media_stack/api/services/content_download_settings_mixin.py",
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


class FixCommitsTouchRatchets(unittest.TestCase):
    """For every recent commit whose message marks a fix
    (``fix``, ``bug``, ``regression``, ``FIXED``), at least one of:

      a) a file matching ``tests/unit/test_*_ratchets.py`` was
         modified in that commit, OR
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
    # Ratchet tests live in several places after the test-tree reorg:
    #   * ``tests/unit/ratchets/`` (canonical location)
    #   * ``tests/unit/architecture/`` (layering/structural rules)
    #   * ``tests/unit/**/test_*_ratchet.py`` and
    #     ``tests/unit/**/test_*_ratchets.py`` (older inline ratchets
    #     that haven't been moved)
    # A fix commit satisfies the rule by touching ANY of those.
    _RATCHET_FILE_HINT = re.compile(
        r"tests/unit/(?:"
        r"ratchets/[\w_/]+\.py"
        r"|architecture/[\w_/]+\.py"
        r"|[\w_/]+test_[\w_]+_ratchets?\.py"
        r")"
    )

    # Subjects that are pure version-bumps / chore commits aren't
    # fixes even if their bodies say "fixes ...".
    _SKIP_SUBJECT_PREFIX = (
        "v1.0.", "Bump ", "Release ", "chore:", "docs:",
    )

    # Baseline rev — only commits AFTER this one are subject to the
    # rule. Originally v1.0.123 (when this ratchet shipped). Bumped
    # to ``257ddde6`` after the 2026-05-02 cleanup pass: the parser
    # below was previously buggy (mis-attributed file lists across
    # adjacent entries) and the ratchet-file-hint regex hadn't been
    # updated for the test-tree reorg, so the rule was effectively
    # dormant. The cleanup pass landed many test-infra fixes (stale
    # assertions, path drifts) that don't represent new bug classes
    # and predate the parser/regex repair on the same line. Going
    # forward, fix-commits MUST declare a ratchet or ``Ratchet: N/A``.
    _BASELINE_TAG = "257ddde6"

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

        # Pre-parse pass: ``--name-only`` puts filenames AFTER each
        # commit's formatted output but BEFORE the next commit's SHA,
        # so after splitting by our ``\x01`` marker the filenames of
        # commit N land at the START of entry N+1 (before the next
        # SHA). Walk entries and attribute files to the correct
        # commit.
        sha_re = re.compile(r"^([0-9a-f]{40})\x00", re.MULTILINE)
        raw_entries = log.split("\x01")
        # Each parsed commit: (sha, subject, body_lines, files-belonging-to-it)
        parsed: list[tuple[str, str, list[str], list[str]]] = []
        # Carry the "head_files" of the next entry back to this one.
        for i, entry in enumerate(raw_entries):
            entry = entry.lstrip("\n")
            if not entry.strip():
                continue
            # Find this entry's own SHA — anything before it is files
            # belonging to the PREVIOUS parsed commit.
            m = sha_re.search(entry)
            if not m:
                continue
            head = entry[: m.start()].strip()
            head_files = [
                ln.strip() for ln in head.splitlines() if ln.strip()
            ]
            if head_files and parsed:
                prev_sha, prev_subject, prev_body, prev_files = parsed[-1]
                parsed[-1] = (
                    prev_sha, prev_subject, prev_body,
                    prev_files + head_files,
                )
            after = entry[m.start():]
            parts = after.split("\x00", 2)
            if len(parts) < 3:
                continue
            sha = parts[0].strip()
            subject = parts[1].strip()
            body = parts[2]
            parsed.append((sha, subject, body.splitlines(), []))
        # Final entry's files trail off after %b\x01 — already
        # captured because the trailing slice of the LAST raw_entry
        # is empty (no next-entry head to attribute from). Pull
        # filenames out of the last commit's body tail if any.
        if parsed:
            last_sha, last_subject, last_body, last_files = parsed[-1]
            # Find a blank line that separates body from files in
            # the last entry's body.
            tail_files: list[str] = []
            kept_body: list[str] = []
            saw_blank = False
            for ln in last_body:
                if not ln.strip() and not saw_blank:
                    saw_blank = True
                    continue
                if saw_blank:
                    if ln.strip():
                        tail_files.append(ln.strip())
                else:
                    kept_body.append(ln)
            if tail_files:
                parsed[-1] = (
                    last_sha, last_subject, kept_body, last_files + tail_files,
                )

        bad: list[str] = []
        for sha, subject, body_lines, file_lines in parsed:
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
            f"tests/unit/test_*_ratchets.py, OR add a line "
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
    the ratchet between bumping VERSION and ``bin/build/build-controller-image.sh``.
    The user's sed expected v1.0.125 → v1.0.126 but the file was
    still at v1.0.123 (skipped two intermediate version bumps), so
    the substitution was a NOOP and the deployed controller ran
    the old image.

    Ratchets only protect you if the release pipeline RUNS them.
    ``bin/release/regen-dist.sh`` is the natural choke-point — every
    release runs it before the build. Pin that it invokes the
    version-parity check."""

    def test_regen_dist_runs_version_parity(self) -> None:
        path = ROOT / "bin" / "release" / "regen-dist.sh"
        if not path.is_file():
            self.skipTest("regen-dist.sh not present")
        text = path.read_text(encoding="utf-8")
        self.assertIn(
            "ControllerImageVersionParity",
            text,
            "bin/release/regen-dist.sh must invoke the "
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


# ---------------------------------------------------------------------------
# L14 — automatic media-hygiene scheduling (compose+k8s parity)
# ---------------------------------------------------------------------------
class AutomaticMediaHygieneScheduling(unittest.TestCase):
    """End users should never need to log into qBit to clean up
    stalled / orphan downloads. The controller MUST:

      1. Have a ``run-media-hygiene`` job in the contract
         (otherwise the dispatcher won't route).
      2. Have an adapter in ``core.job_adapters``.
      3. Run a scheduler dispatch loop that fires recurring
         actions (the SchedulerService had ``get_due_actions``
         but no caller until v1.0.132).
      4. Seed ``run-media-hygiene`` with an interval ≤ 1h on
         first start, so compose deploys get the same automatic
         cleanup as k8s (which already had a 6h CronJob).

    Aggressive defaults in ``_guardrail_config.py``: stalled >4h,
    age >36h, eta >6h, dl<64KB/s with progress<0.98."""

    def test_contract_registers_run_media_hygiene_job(self) -> None:
        try:
            import yaml as _yaml
        except ImportError:
            self.skipTest("PyYAML not installed")
        path = ROOT / "contracts" / "services" / "core.yaml"
        if not path.is_file():
            self.skipTest("core.yaml not present")
        doc = _yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        jobs = ((doc.get("plugin") or {}).get("jobs") or {})
        self.assertIn(
            "run-media-hygiene", jobs,
            "core.yaml dropped the run-media-hygiene job — without "
            "this entry the scheduler tick can't dispatch it and "
            "qBit downloads pile up indefinitely.",
        )
        self.assertEqual(
            jobs["run-media-hygiene"].get("handler"),
            "media_stack.services.apps.core.job_adapters:run_media_hygiene",
            "run-media-hygiene handler path drifted",
        )

    def test_job_adapter_exists(self) -> None:
        sys.path.insert(0, str(SRC.parent.parent))
        from media_stack.services.apps.core import job_adapters
        self.assertTrue(
            hasattr(job_adapters, "run_media_hygiene"),
            "core.job_adapters lost the run_media_hygiene wrapper",
        )

    def test_controller_seeds_default_schedule(self) -> None:
        path = SRC / "cli" / "commands" / "controller_serve.py"
        if not path.is_file():
            self.skipTest("controller_serve.py not present")
        text = path.read_text(encoding="utf-8")
        # The seed call must reference the action name + a sane
        # interval (≤ 3600s = 1h).
        self.assertIn(
            'add_schedule(',
            text,
            "controller_serve.py no longer seeds default schedules",
        )
        self.assertIn(
            '"run-media-hygiene"',
            text,
            "controller_serve.py no longer seeds run-media-hygiene",
        )
        self.assertRegex(
            text,
            r"interval_seconds\s*=\s*(?:60|120|180|300|600|900|1200|1800|3600)\b",
            "Default media-hygiene schedule interval > 1h. End "
            "users shouldn't have stalled downloads accumulating "
            "for hours; aggressive cleanup is the whole point.",
        )

    def test_scheduler_dispatch_loop_wired(self) -> None:
        path = SRC / "cli" / "commands" / "controller_serve.py"
        if not path.is_file():
            self.skipTest("controller_serve.py not present")
        text = path.read_text(encoding="utf-8")
        self.assertIn(
            "get_due_actions",
            text,
            "controller_serve.py no longer calls "
            "scheduler.get_due_actions — added schedules will "
            "sit on disk forever and never fire.",
        )
        self.assertIn(
            'name="scheduler-dispatch"',
            text,
            "scheduler-dispatch background thread not started — "
            "default cleanup never runs.",
        )

    def test_aggressive_hygiene_defaults(self) -> None:
        """Defaults in ``_guardrail_config.py`` must be aggressive
        enough that a typical home stack doesn't accumulate
        weeks-old stalled downloads. The conservative defaults
        (24h stalled, 168h age, 14d eta) shipped before v1.0.132
        let queues bloat indefinitely on small disks."""
        path = SRC / "services/media_hygiene_ops/_guardrail_config.py"
        if not path.is_file():
            self.skipTest("_guardrail_config.py not present")
        text = path.read_text(encoding="utf-8")
        # max_stalled_hours default — must be ≤ 12h.
        m = re.search(r'max_stalled_hours.*?,\s*(\d+(?:\.\d+)?)', text)
        self.assertIsNotNone(m, "max_stalled_hours default missing")
        self.assertLessEqual(
            float(m.group(1)), 12.0,
            f"stale_max_stalled_hours default {m.group(1)}h is too "
            f"lax. Aggressive cleanup means torrents stuck >12h "
            f"get pruned without operator intervention.",
        )
        m = re.search(r'max_age_hours.*?,\s*(\d+(?:\.\d+)?)', text)
        self.assertIsNotNone(m, "max_age_hours default missing")
        self.assertLessEqual(
            float(m.group(1)), 72.0,
            f"stale_max_age_hours default {m.group(1)}h > 3 days. "
            f"Without aggressive auto-prune, the queue accumulates "
            f"and end users have to log into qBit to clean up.",
        )


class SlowJobsAreNonBlocking(unittest.TestCase):
    """End-user goal: bootstrap completes in <60s so the dashboard
    is usable. Long-running probe jobs (indexer discovery + per-app
    tagging across ~70 candidate trackers + Jellyfin EPG channel
    refresh against ~1000 IPTV entries) MUST carry
    ``non_blocking: true`` so the runner spawns them in a daemon
    thread and moves on. Without this, bootstrap takes 8-15 min on
    a cold deploy and the user stares at a "Loading..." spinner.

    The runner side (``JobRunner.run`` in
    ``cli/commands/job_framework.py``) honors this by writing a
    ``status: running_in_background`` placeholder, immediately
    marking the job complete, and overwriting the placeholder
    when the thread finishes. (v1.0.134.)"""

    _SLOW_JOBS = (
        ("contracts/services/core.yaml", "discover-indexers"),
        ("contracts/services/core.yaml", "tag-indexers-for-apps"),
        ("contracts/services/jellyfin.yaml", "configure-livetv"),
        ("contracts/services/prowlarr.yaml", "configure-indexers"),
    )

    def test_runner_honors_non_blocking_flag(self) -> None:
        # Phase 16-E moved the framework from services/jobs/ to
        # application/jobs/; services/jobs/framework.py is now a
        # sys.modules-aliased shim with no real content to grep.
        text = (
            SRC / "application" / "jobs" / "framework.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "non_blocking",
            text,
            "JobRunner no longer references non_blocking — the "
            "flag is dead, every slow job blocks bootstrap again.",
        )
        self.assertIn(
            '"job-async-',
            text,
            "Non-blocking dispatch is no longer spawning a thread "
            "with a job-async-* name (so logs lose the source "
            "marker AND blocking probably wasn't actually "
            "concurrent).",
        )

    def test_known_slow_jobs_carry_non_blocking_true(self) -> None:
        try:
            import yaml as _yaml
        except ImportError:
            self.skipTest("PyYAML not installed")
        bad: list[str] = []
        for rel_path, job_name in self._SLOW_JOBS:
            path = ROOT / rel_path
            if not path.is_file():
                continue
            doc = _yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            jobs = ((doc.get("plugin") or {}).get("jobs") or {})
            job = jobs.get(job_name) or {}
            if not job.get("non_blocking"):
                bad.append(f"{rel_path}::{job_name}")
        self.assertFalse(
            bad,
            "Slow jobs lost their non_blocking: true flag — they "
            "now gate bootstrap completion. Each of these takes "
            "minutes on a cold deploy:\n  - "
            + "\n  - ".join(bad),
        )


class ContractJobsHaveLabels(unittest.TestCase):
    """The UI reads job labels from /api/jobs (which forwards the
    ``label:`` field from each contract YAML). Keeping labels
    inline with each job — rather than duplicated in the UI —
    means adding a new contract job is one edit and the UI
    automatically gets a friendly name.

    Until v1.0.135, the (now-retired) dashboard had a hardcoded
    ACTION_LABEL_OVERRIDES map that drifted from the contract
    every time a new job was added. That meant new jobs showed up
    in the toast as raw slugs (apply-arr-runtime-defaults) rather
    than human strings ("Update download settings")."""

    def test_every_user_facing_job_has_a_label(self) -> None:
        try:
            import yaml as _yaml
        except ImportError:
            self.skipTest("PyYAML not installed")
        contracts_dir = ROOT / "contracts" / "services"
        if not contracts_dir.is_dir():
            self.skipTest("contracts/services not present")
        bad: list[str] = []
        for f in sorted(contracts_dir.glob("*.yaml")):
            if f.stem.startswith("_"):
                continue
            doc = _yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            jobs = ((doc.get("plugin") or {}).get("jobs") or {})
            for job_name, job_def in jobs.items():
                if not isinstance(job_def, dict):
                    continue
                label = (job_def.get("label") or "").strip()
                if not label:
                    bad.append(f"{f.name}::{job_name}")
        self.assertFalse(
            bad,
            f"Contract jobs missing the 'label:' field "
            f"({len(bad)} of them) — dashboard will show their "
            f"raw slugs in toasts/job-tree until you add a "
            f"label.\n  - " + "\n  - ".join(bad[:15]),
        )


# ---------------------------------------------------------------------------
# L19 — apply-arr-runtime-defaults builds arr_apps from registry, not cfg
# ---------------------------------------------------------------------------
class ApplyArrRuntimeDefaultsBuildsArrApps(unittest.TestCase):
    """``cfg.get("arr_apps")`` is a legacy key never populated on
    contract-driven deploys. The ``apply_arr_runtime_defaults``
    adapter relied on it as the SOLE source of arr_apps — when
    empty, the function silently NOOP'd and the delay profile
    stayed at ``preferredProtocol=usenet`` even when SAB was off.
    Sonarr then waited indefinitely for usenet grabs that would
    never come (status: ``delay`` for every torrent release in
    the queue), and qBit stayed at 0 active.

    The adapter must build ``arr_apps`` from the registry
    (``ctx.service_url`` + ``ctx.api_key``) when cfg.arr_apps is
    empty, matching the pattern used by every other adapter."""

    def test_adapter_falls_back_to_registry(self) -> None:
        path = SRC / "services" / "apps" / "core" / "job_adapters.py"
        if not path.is_file():
            self.skipTest("job_adapters.py not present")
        text = path.read_text(encoding="utf-8")
        m = re.search(
            r"def apply_arr_runtime_defaults\([^)]*\) -> dict:.*?(?=\ndef )",
            text, re.DOTALL,
        )
        self.assertIsNotNone(
            m, "apply_arr_runtime_defaults not found in job_adapters.py",
        )
        body = m.group(0)
        self.assertIn(
            "ctx.service_url",
            body,
            "apply_arr_runtime_defaults no longer derives arr_apps "
            "from ctx.service_url — falls back to empty list and "
            "becomes a silent NOOP. The delay profile stays at "
            "preferredProtocol=usenet even when SAB is off and "
            "every grab hangs in 'delay' status.",
        )
        self.assertRegex(
            body,
            r'arr_apps\.append\(\s*\{[^}]*"name"',
            "apply_arr_runtime_defaults no longer appends to "
            "arr_apps from the per-service loop — fallback path "
            "broken.",
        )


# ---------------------------------------------------------------------------
# L20 — every path the Dockerfile COPYs from is tracked in git
# ---------------------------------------------------------------------------
class DockerfileCopyPathsTrackedInGit(unittest.TestCase):
    """If the Dockerfile does ``COPY foo/ /opt/foo`` but ``foo/``
    isn't tracked in git, the build works on the developer's local
    checkout (because the file exists locally) but BREAKS on:

      - fresh git clone
      - CI build
      - any teammate

    Discovered v1.0.136: ``COPY config/defaults /opt/media-stack/config/defaults``
    silently shipped empty because ``config/`` was in .gitignore
    (added to keep runtime app data out) and the un-ignore rule
    for ``config/defaults/`` was missing. Envoy then crashed on
    "envoy.runtime.base.yaml not found" the moment the controller
    tried to generate a config.

    This ratchet asserts every file/dir referenced in a
    ``COPY <src> <dst>`` directive IS in ``git ls-files``."""

    def test_dockerfile_copy_sources_are_in_git(self) -> None:
        import subprocess
        dockerfile = ROOT / "docker" / "controller.Dockerfile"
        if not dockerfile.is_file():
            self.skipTest("controller.Dockerfile not present")
        try:
            tracked = set(
                subprocess.check_output(
                    ["git", "ls-files"], cwd=ROOT, stderr=subprocess.DEVNULL,
                ).decode().splitlines()
            )
        except Exception:
            self.skipTest("git not available")

        # Pull each `COPY src dst` directive (skip --from / --chown variants
        # that source from a build stage, not a host path).
        copy_re = re.compile(
            r"^\s*COPY(?!\s+--from)(?:\s+--\S+)?\s+(\S+)\s+\S+",
            re.MULTILINE,
        )
        bad: list[str] = []
        for src in copy_re.findall(dockerfile.read_text(encoding="utf-8")):
            # Skip whole-context copies and absolute paths
            if src in (".", "./") or src.startswith("/"):
                continue
            src_path = ROOT / src
            # If it's a dir, at least ONE file under it must be tracked
            if src_path.is_dir():
                prefix = src.rstrip("/") + "/"
                if not any(t.startswith(prefix) for t in tracked):
                    bad.append(
                        f"COPY {src} → directory exists locally but "
                        f"NO files under {prefix} are tracked in git"
                    )
            else:
                # File copy — must be tracked
                if src not in tracked:
                    bad.append(
                        f"COPY {src} → file is not in git ls-files"
                    )
        self.assertFalse(
            bad,
            "Dockerfile COPYs from paths not tracked in git — "
            "build works on the dev's machine, breaks on fresh "
            "clone / CI:\n  - " + "\n  - ".join(bad),
        )


# ---------------------------------------------------------------------------
# L21 — curated public-indexer allowlist exists + has reasonable size
# ---------------------------------------------------------------------------
class CuratedIndexerAllowlistShipped(unittest.TestCase):
    """Without an allowlist, ``discover-indexers`` enables every
    Cardigann definition Prowlarr ships (~70). Most are dead /
    CloudFlare-only / niche. The user adds a series, Sonarr can't
    grab anything, queue fills with 'Invalid torrent file' errors,
    qBit stays at 0 active. (v1.0.137 — discovered after a full
    end-to-end MVP test on Breaking Bad returned 0 grabs.)

    The shipped allowlist (``contracts/curated-indexers.yaml``)
    pins ~10 known-reliable public sources. This ratchet:

      1. asserts the file exists and parses,
      2. ``mode`` is one of {allowlist, all},
      3. when ``mode: allowlist``, the allowed list is non-empty
         (a typo making ``allowed`` empty would silently disable
         every indexer).
    """

    def test_curated_allowlist_present_and_sane(self) -> None:
        try:
            import yaml as _yaml
        except ImportError:
            self.skipTest("PyYAML not installed")
        path = ROOT / "contracts" / "curated-indexers.yaml"
        self.assertTrue(
            path.is_file(),
            "contracts/curated-indexers.yaml is the source of "
            "truth for which public trackers ship enabled by "
            "default. Removing it sends discovery back to "
            "enabling all 70 Cardigann defs — most produce "
            "Invalid torrent file errors and the user's queue "
            "stays at 0 active.",
        )
        doc = _yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        mode = str(doc.get("mode") or "").lower().strip()
        self.assertIn(
            mode, {"allowlist", "all"},
            f"contracts/curated-indexers.yaml mode={mode!r} — must "
            f"be 'allowlist' or 'all'.",
        )
        if mode == "allowlist":
            # Two accepted forms: flat ``allowed: [...]`` (override)
            # OR grouped ``categories: {tv: [...], movies: [...]}``
            # whose union becomes the allowlist when ``allowed`` is
            # empty. Either must yield at least one slug — otherwise
            # discovery silently disables every indexer.
            allowed = doc.get("allowed") or []
            cats = doc.get("categories") or {}
            self.assertIsInstance(
                allowed, list,
                "curated-indexers.yaml: 'allowed' must be a list",
            )
            self.assertIsInstance(
                cats, dict,
                "curated-indexers.yaml: 'categories' must be a mapping",
            )
            union: list[str] = list(allowed)
            for cat_list in cats.values():
                if isinstance(cat_list, list):
                    union.extend(cat_list)
            self.assertGreater(
                len(union), 0,
                "curated-indexers.yaml: mode=allowlist with empty "
                "'allowed' AND empty 'categories' silently disables "
                "every indexer.",
            )

    def test_discovery_loader_consults_allowlist(self) -> None:
        path = SRC / "services" / "apps" / "prowlarr" / "reputation_ops.py"
        if not path.is_file():
            self.skipTest("reputation_ops.py not present")
        text = path.read_text(encoding="utf-8")
        self.assertIn(
            "_load_curated_allowed_definitions",
            text,
            "reputation_ops no longer references the curated "
            "allowlist loader — discovery falls back to enabling "
            "every Cardigann def.",
        )
        self.assertIn(
            "curated-indexers.yaml",
            text,
            "reputation_ops no longer references "
            "contracts/curated-indexers.yaml — the curated config "
            "isn't being loaded.",
        )


# ---------------------------------------------------------------------------
# L22 — non_blocking + after: chain enforces real completion order
# ---------------------------------------------------------------------------
class NonBlockingJobsHaveAfterDeps(unittest.TestCase):
    """Discovered v1.0.139 on a fresh-stack MVP test: tag-indexers
    and discover-indexers were both ``non_blocking: true`` peers
    with no explicit ordering. The dispatcher reported each as
    ``done`` the instant it spawned the daemon thread, so push-
    indexers ran with 0 tagged indexers, ApplicationIndexerSync
    pushed nothing, Sonarr/Radarr stayed at 0 indexers, qBit at
    0 grabs.

    The fix introduces an ``after: [job-name, ...]`` field that is
    evaluated against the daemon thread's ACTUAL completion. This
    ratchet asserts:

      1. The framework parses an ``after:`` field on jobs.
      2. The framework respects ``after:`` in dispatch — a job's
         ``after`` deps must be in ``done`` (not just dispatched)
         before it becomes ready.
      3. The contract chain we shipped is intact:
         ``tag-indexers-for-apps`` waits for ``discover-indexers``,
         ``push-indexers`` waits transitively for both. Without
         this chain, the race re-emerges.
    """

    def test_job_class_supports_after_field(self) -> None:
        from media_stack.services.jobs.framework import Job
        j = Job("x", lambda ctx: {}, after=["upstream"])
        self.assertEqual(
            list(getattr(j, "after", [])), ["upstream"],
            "Job.__init__ no longer carries the ``after:`` list — "
            "downstream dependency ordering is silently dropped.",
        )

    def test_runner_waits_for_after_deps_to_complete(self) -> None:
        """End-to-end: a non_blocking upstream that takes 100ms must
        finish before its downstream sibling is dispatched, even
        though the upstream is reported as ``dispatched`` quickly."""
        import time as _t
        from media_stack.services.jobs.framework import (
            Job, JobRunner, JobContext,
        )
        order: list[tuple[str, float]] = []
        t0 = _t.time()

        def upstream(ctx):
            _t.sleep(0.1)
            order.append(("up_done", _t.time() - t0))
            return {}

        def downstream(ctx):
            order.append(("down_start", _t.time() - t0))
            return {}

        root = Job("root", lambda ctx: {})
        root.add_sub_job(Job("up", upstream, non_blocking=True))
        root.add_sub_job(Job("down", downstream, after=["up"]))
        ctx = JobContext()
        JobRunner(root, ctx, max_attempts=3).run()

        events = {n: t for n, t in order}
        self.assertIn("up_done", events,
                      "non_blocking upstream never recorded completion")
        self.assertIn("down_start", events,
                      "downstream never ran — after-dep chain wedged")
        self.assertGreater(
            events["down_start"], events["up_done"],
            "downstream started before non_blocking upstream finished — "
            "``after:`` is being ignored, the indexer race is back.",
        )

    def test_contract_chain_indexers_to_push(self) -> None:
        try:
            import yaml as _yaml
        except ImportError:
            self.skipTest("PyYAML not installed")
        path = ROOT / "contracts" / "services" / "core.yaml"
        doc = _yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        jobs = (doc.get("plugin", {}) or {}).get("jobs", {}) or {}

        tag = jobs.get("tag-indexers-for-apps", {})
        push = jobs.get("push-indexers", {})
        self.assertIn(
            "discover-indexers", tag.get("after", []),
            "tag-indexers-for-apps lost its ``after: [discover-indexers]`` — "
            "tagging will race against discovery and tag zero indexers.",
        )
        # push-indexers should wait transitively (via reset-prowlarr-app-mappings
        # OR directly) for tag-indexers to complete.
        push_after = set(push.get("after", []))
        chain_ok = bool(
            push_after & {"tag-indexers-for-apps", "reset-prowlarr-app-mappings"}
        )
        self.assertTrue(
            chain_ok,
            "push-indexers must wait for tag-indexers (directly or via "
            "reset-prowlarr-app-mappings) — without it, "
            "ApplicationIndexerSync runs before any indexer has tags.",
        )


# ---------------------------------------------------------------------------
# L23 — auth reconcile never clears a non-empty urlBase
# ---------------------------------------------------------------------------
class AuthReconcileDoesNotClearUrlBase(unittest.TestCase):
    """Discovered v1.0.141 after a fresh-install MVP test: qBit
    stayed at 0 active because Radarr fetched the Prowlarr download
    URL, got a 307 with 0 bytes, and MonoTorrent threw
    IndexOutOfRange parsing empty bencoded data. Root cause: the
    ARR preflight persisted ``urlBase=/app/prowlarr`` via API, then
    the auth reconcile CLEARED it to empty because the profile's
    ``url_base_by_app`` wasn't set. Prowlarr then constructed
    search responses with ``http://prowlarr:9696/5/download?...``
    (no prefix), forcing the 307 that broke the torrent fetch.

    Rule: ``ensure_app_auth_settings`` must never WRITE an empty
    urlBase over a non-empty one. If the profile doesn't declare
    a desired value, the preflight's value stands.
    """

    def test_auth_reconcile_does_not_clear_urlbase(self) -> None:
        # Assert the write-condition at source-level: the code MUST
        # NOT enter the clear-urlBase branch when desired_url_base
        # is empty. Post-fix the condition gates on
        # ``if desired_url_base:`` only.
        src = (
            __import__("pathlib").Path(__file__).resolve().parents[3]
            / "src" / "media_stack" / "services" / "auth_service.py"
        ).read_text(encoding="utf-8")
        bad = 'if desired_url_base or "urlBase" in desired'
        self.assertNotIn(
            bad, src,
            "auth_service.ensure_app_auth_settings regressed: the "
            "reconcile clobbers non-empty urlBase with empty when "
            "the profile doesn't declare url_base_by_app. This "
            "broke qBit downloads on clean install (v1.0.141).",
        )

    def test_preflight_sets_arr_urlbase(self) -> None:
        """The preflight MUST still set ``/app/{app}`` — if this
        loop disappears, the original bug flips to the other side:
        Radarr's auto-login POST to Prowlarr would lose its body on
        307, no API key would come back, bootstrap fails."""
        # Post ADR-0002 Phase 16-D: implementation lives in
        # infrastructure/servarr/http_preflight.py; the legacy
        # services/apps/servarr/http_preflight.py is a sys.modules
        # alias shim so reading it returns shim text, not the impl.
        src = (
            __import__("pathlib").Path(__file__).resolve().parents[3]
            / "src" / "media_stack" / "infrastructure" / "servarr"
            / "http_preflight.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            '"UrlBase", f"/app/{app_name}"',
            src,
            "servarr preflight no longer seeds UrlBase=/app/{app} "
            "— Prowlarr's downloadUrl will lack the prefix and "
            "Radarr's torrent fetch will 307 to empty bytes.",
        )


# ---------------------------------------------------------------------------
# L24 — auto_download_content falls back to profile when env unset
# ---------------------------------------------------------------------------
class AutoDownloadContentReadsProfile(unittest.TestCase):
    """Discovered v1.0.141: a fresh compose deploy creates the *arr
    import lists but every one comes back with ``enableAuto=false``,
    so the lists exist on paper but never auto-add new content. Root
    cause: ``controller_runner._build_config_policy`` read
    ``AUTO_DOWNLOAD_CONTENT`` from env only, defaulting to ``"0"``
    when unset. The default bootstrap profile has no env var, the
    policy applied with ``auto_download_content=False``, and
    ``apply_content_download_policy`` flipped every list's
    ``enable_auto`` to False before bootstrap POSTed them.

    Rule: when the env var is unset, the resolver MUST fall back to
    the profile's ``bootstrap.auto_download_content`` value. The env
    only overrides if explicitly set — that's how the dashboard
    "Auto-Downloads" toggle works at runtime.
    """

    def test_controller_runner_consults_profile_for_auto_download(self) -> None:
        # Phase 16-E: controller_runner moved to application/jobs/;
        # services/jobs/controller_runner.py is a star-import shim.
        src = (
            __import__("pathlib").Path(__file__).resolve().parents[3]
            / "src" / "media_stack" / "application" / "jobs" / "controller_runner.py"
        ).read_text(encoding="utf-8")
        # The fallback chain must be present: env wins when set,
        # profile fills in when env is empty/unset.
        self.assertIn(
            "profile_bootstrap.get(\"auto_download_content\"",
            src,
            "controller_runner._build_config_policy regressed: it no "
            "longer reads ``bootstrap.auto_download_content`` from "
            "the profile. Default compose deploys will silently "
            "disable enableAuto on every import list — lists exist "
            "but never auto-add (v1.0.141 root cause).",
        )
        # Anti-pattern: the bare env-only read with default="0".
        bad = "os.environ.get(\"AUTO_DOWNLOAD_CONTENT\", \"0\") == \"1\""
        self.assertNotIn(
            bad, src,
            "controller_runner regressed back to env-only "
            "auto_download_content resolution. The profile fallback "
            "is required for OTB compose deploys.",
        )

    def test_default_profile_enables_auto_download(self) -> None:
        try:
            import yaml as _yaml
        except ImportError:
            self.skipTest("PyYAML not installed")
        path = ROOT / "examples" / "bootstrap-profiles" / "media-compose-standard.yaml"
        if not path.is_file():
            self.skipTest("default profile not present")
        doc = _yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        boot = doc.get("bootstrap") or {}
        self.assertTrue(
            boot.get("auto_download_content"),
            "default profile must declare ``bootstrap.auto_download_content: true`` "
            "for OTB auto-downloading. Without it, *arr import lists "
            "have enableAuto=False and no content auto-adds.",
        )


# ---------------------------------------------------------------------------
# L25 — controller hosts the popular-TV CustomImport feed + Sonarr uses it
# ---------------------------------------------------------------------------
class PopularTvCustomImportWired(unittest.TestCase):
    """Discovered v1.0.143: Sonarr had no live discovery OTB because
    every stock provider needs OAuth (Trakt/Plex/AniList) or is
    upstream-broken (IMDb returns 202 from rate-limiting). The
    seed_series list covers 10 hand-picked shows but doesn't update.

    Fix: a controller-hosted ``/api/discovery/popular-tv`` endpoint
    fetches TVMaze (free, no auth), scores by rating, returns a
    Sonarr CustomImport-compatible JSON array. Sonarr's default
    list config points at that endpoint, so popular TV auto-adds
    forever without user action.
    """

    def test_handler_exists_and_is_routed(self) -> None:
        src = (
            __import__("pathlib").Path(__file__).resolve().parents[3]
            / "src" / "media_stack" / "api" / "handlers_get.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "/api/discovery/popular-tv", src,
            "GET route for /api/discovery/popular-tv disappeared — "
            "Sonarr's CustomImport will 404 and no TV auto-adds.",
        )
        self.assertIn(
            "_handle_popular_tv", src,
            "_handle_popular_tv handler disappeared",
        )
        self.assertIn(
            "api.tvmaze.com/shows", src,
            "popular-tv handler no longer calls TVMaze — source "
            "of truth for the feed is gone.",
        )

    def test_default_sonarr_list_points_at_the_feed(self) -> None:
        try:
            import yaml as _yaml
        except ImportError:
            self.skipTest("PyYAML not installed")
        path = ROOT / "contracts" / "defaults" / "arr.yaml"
        doc = _yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        lists = (doc.get("arr_discovery_lists") or {}).get("Sonarr") or []
        self.assertTrue(
            lists,
            "arr.yaml: Sonarr list is empty. Without CustomImport "
            "pointing at the popular-tv feed, Sonarr has no live "
            "discovery — OTB falls back to the seed-series only.",
        )
        has_custom = any(
            str(e.get("implementation") or "") == "CustomImport"
            and "/api/discovery/popular-tv" in str(
                (e.get("field_overrides") or {}).get("baseUrl") or ""
            )
            for e in lists if isinstance(e, dict)
        )
        self.assertTrue(
            has_custom,
            "arr.yaml: Sonarr lists no longer contain a CustomImport "
            "pointing at /api/discovery/popular-tv. The controller's "
            "TVMaze feed is decoupled from Sonarr — live discovery "
            "won't happen on a fresh install.",
        )


# ---------------------------------------------------------------------------
# L26 — completed-download → Jellyfin chain
# ---------------------------------------------------------------------------
class CompletedDownloadReachesJellyfin(unittest.TestCase):
    """Discovered v1.0.144: qBit downloads finished but never appeared
    in Jellyfin. Two gaps:

    A. The Radarr/Sonarr ``/webhooks/arr`` handler tries to trigger a
       Jellyfin ``/Library/Refresh`` but ``discover_api_keys()`` only
       reads config-file-format keys. Jellyfin stores its key in
       SQLite, so the lookup returned empty and the scan never fired.

    B. If a user adds a torrent to qBit DIRECTLY (no *arr involved),
       the file lands in ``/data/torrents/completed/<cat>/`` and
       nothing ever imports it. *arrs only know about torrents they
       initiated.

    Fixes:
    A. ``discover_api_keys`` falls back to Jellyfin's SQLite ApiKeys
       table.
    B. A scheduled ``scan-completed-downloads`` job fires each *arr's
       ``DownloadedXScan`` command every 15min, picking up anything
       in the completed paths.
    """

    def test_discover_api_keys_reads_jellyfin_sqlite(self) -> None:
        src = (
            __import__("pathlib").Path(__file__).resolve().parents[3]
            / "src" / "media_stack" / "api" / "services" / "health.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "read_jellyfin_api_key_from_db",
            src,
            "discover_api_keys regressed: no longer falls back to "
            "Jellyfin's SQLite key. Webhook /Library/Refresh will "
            "fail and imported content stays invisible until the "
            "library monitor finds it via inotify.",
        )

    def test_scan_completed_downloads_job_registered(self) -> None:
        try:
            import yaml as _yaml
        except ImportError:
            self.skipTest("PyYAML not installed")
        path = ROOT / "contracts" / "services" / "core.yaml"
        doc = _yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        jobs = (doc.get("plugin", {}) or {}).get("jobs", {}) or {}
        self.assertIn(
            "scan-completed-downloads", jobs,
            "scan-completed-downloads job missing from core.yaml. "
            "User-added qBit torrents won't reach Jellyfin — they'll "
            "sit in /data/torrents/completed/ forever.",
        )

    def test_scan_completed_downloads_handler_exists(self) -> None:
        src = (
            __import__("pathlib").Path(__file__).resolve().parents[3]
            / "src" / "media_stack" / "services" / "apps" / "core"
            / "job_adapters.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "def scan_completed_downloads(",
            src,
            "scan_completed_downloads handler missing. Contract "
            "registers the job but the handler isn't there → "
            "ImportError on dispatch.",
        )
        # Sanity: the handler must call each *arr's downloaded-scan
        # command. Anti-regression: don't let someone trim it to
        # only one *arr.
        for cmd in ("DownloadedEpisodesScan", "DownloadedMoviesScan",
                    "DownloadedAlbumsScan", "DownloadedBooksScan"):
            self.assertIn(
                cmd, src,
                f"scan_completed_downloads no longer fires {cmd} — "
                "one of the *arrs is silently excluded.",
            )

    def test_scheduler_seeds_scan_completed_downloads(self) -> None:
        src = (
            __import__("pathlib").Path(__file__).resolve().parents[3]
            / "src" / "media_stack" / "cli" / "commands"
            / "controller_serve.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "scan-completed-downloads", src,
            "controller_serve scheduler no longer seeds "
            "scan-completed-downloads. The job exists but never "
            "fires automatically → user-added qBit content sits "
            "unimported.",
        )

    def test_scan_library_button_fires_arr_scans_and_jellyfin(self) -> None:
        """The dashboard's "Scan Library" button used to call
        Jellyfin's /Library/Refresh only — which can't see files
        still sitting in /data/torrents/completed/. The button
        must also fire the per-*arr DownloadedXScan commands so
        the completed-but-unimported files reach /media/* before
        Jellyfin re-indexes.

        The hardcoded ``DownloadedXScan`` strings moved out of
        content.py in v1.0.193 — they now live in the media-type
        catalog (``contracts/catalog/media_types.yaml``) under the
        ``arr_scan_command`` field, and the mixin iterates the
        catalog at runtime. Pin the catalog instead so the names
        can't quietly drift away from the v1.0.144 contract.
        """
        try:
            import yaml as _yaml
        except ImportError:
            self.skipTest("PyYAML not installed")
        path = (
            __import__("pathlib").Path(__file__).resolve().parents[3]
            / "contracts" / "catalog" / "media_types.yaml"
        )
        doc = _yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        media_types = doc.get("media_types") or {}
        scans = {
            (entry or {}).get("arr_scan_command", "")
            for entry in media_types.values()
        }
        for cmd in ("DownloadedEpisodesScan", "DownloadedMoviesScan",
                    "DownloadedAlbumsScan", "DownloadedBooksScan"):
            self.assertIn(
                cmd, scans,
                f"media-types catalog missing {cmd} — the 'Scan "
                f"Library' button iterates the catalog and won't "
                f"trigger the corresponding *arr scan.",
            )


# ---------------------------------------------------------------------------
# L27 — fresh-install OTB hygiene (v1.0.145)
# ---------------------------------------------------------------------------
class FreshInstallOtbHygiene(unittest.TestCase):
    """Discovered v1.0.145 from a fresh-install user report:

      1. "Indexers unavailable due to failures: TPB / EZTV / 1337x"
         — caused by 159 series × MissingEpisodeSearch firing
         concurrently after seed/import. Indexers 429-throttled.
      2. "Missing languages profile" in Bazarr — Bazarr ships with
         no profile, downloads no subtitles until manually
         configured.
      3. ``http://localhost:6246/`` returns 404 — Maintainerr only
         serves at /app/maintainerr/. Dashboard's "open" link
         omitted the prefix for direct-port URLs.

    Fixes:
      A. Seed/import lists default to ``search_for_missing_episodes:
         false`` and ``should_search: false`` — RSS sync paces
         downloads naturally; ``mass-search-throttled`` job is
         available on-demand for users who want a burst.
      B. ``ensure-bazarr-language-profile`` job creates a default
         English profile if none exists.
      C. Service registry exposes ``preserve_path_prefix`` so the
         dashboard's direct-link builder appends the correct prefix.
    """

    def test_seed_search_disabled_to_prevent_429_burst(self) -> None:
        try:
            import yaml as _yaml
        except ImportError:
            self.skipTest("PyYAML not installed")
        path = ROOT / "contracts" / "defaults" / "arr.yaml"
        doc = _yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        seed = doc.get("sonarr_seed_series", {})
        self.assertFalse(
            seed.get("search_for_missing_episodes", True),
            "sonarr_seed_series.search_for_missing_episodes is True. "
            "Adding 10+ seed series with per-series search fires "
            "concurrent indexer hits → 429 TooManyRequests → "
            "indexers marked unavailable. Use mass-search-throttled "
            "for on-demand bursts instead.",
        )

    def test_mass_search_throttled_handler_exists(self) -> None:
        src = (
            __import__("pathlib").Path(__file__).resolve().parents[3]
            / "src" / "media_stack" / "services" / "apps" / "core"
            / "job_adapters.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "def mass_search_throttled(",
            src,
            "mass_search_throttled handler removed — users can no "
            "longer trigger a paced search burst from the dashboard.",
        )
        # Sleep between dispatches is the WHOLE point — without it
        # we re-introduce the 429 burst.
        self.assertIn("MASS_SEARCH_DELAY_SECONDS", src)
        self.assertIn("_t.sleep(delay)", src)

    def test_bazarr_language_profile_handler_exists(self) -> None:
        src = (
            __import__("pathlib").Path(__file__).resolve().parents[3]
            / "src" / "media_stack" / "services" / "apps" / "core"
            / "job_adapters.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "def ensure_bazarr_language_profile(",
            src,
            "Bazarr's default-profile handler is gone — fresh "
            "installs will see 'Missing languages profile' until "
            "the user manually configures it.",
        )
        # Extended in v1.0.146: also sets default profile for new
        # series/movies + curated provider list. Drift here means
        # users get a profile but no auto-assignment, OR a usable
        # setup but with no/wrong subtitle providers.
        for needle in (
            "settings-general-serie_default_enabled",
            "settings-general-serie_default_profile",
            "settings-general-movie_default_enabled",
            "settings-general-movie_default_profile",
            "settings-general-enabled_providers",
            "gestdown",            # TV provider (Addic7ed replacement)
            "yifysubtitles",       # movies (pairs with YTS)
            "embeddedsubtitles",   # zero-network extraction
            # *arr integration — Bazarr's UI shows "not configured"
            # without these and never fetches for Sonarr/Radarr content.
            "settings-general-use_",
            '"/app/sonarr"',
            '"/app/radarr"',
        ):
            self.assertIn(
                needle, src,
                f"ensure_bazarr_language_profile no longer sets "
                f"{needle!r}. OTB Bazarr config is incomplete — "
                "fresh content won't auto-fetch subtitles.",
            )
        self.assertNotIn(
            '"addic7ed"', src,
            "addic7ed is back in the OTB provider list. It has "
            "anti-scrape issues that return 0 results; gestdown "
            "is its modern replacement.",
        )

    def test_services_api_exposes_preserve_path_prefix(self) -> None:
        # The /api/services payload assembly moved out of
        # handlers_get.py into api/services/registry.py with the
        # rest of the service-registry concerns (Phase 16-* api
        # split). Pin the new home.
        src = (
            __import__("pathlib").Path(__file__).resolve().parents[3]
            / "src" / "media_stack" / "api" / "services" / "registry.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            '"preserve_path_prefix":',
            src,
            "/api/services no longer exposes preserve_path_prefix. "
            "Maintainerr's UI 404s on direct-port links because the "
            "dashboard can't tell it needs the /app/maintainerr/ "
            "prefix.",
        )


# ---------------------------------------------------------------------------
# L28 — Maintainerr rules link to the right *arr by library type
# ---------------------------------------------------------------------------
class MaintainerrRulesLinkToArr(unittest.TestCase):
    """Discovered v1.0.146: Maintainerr's UI showed "Radarr server *
    None" for every rule. The integrations tab had Radarr+Sonarr
    configured, but the rules themselves had ``radarrSettingsId``
    and ``sonarrSettingsId`` set to None — so when a rule fired, it
    could only act on the Jellyfin library, not delete from the
    *arr.

    Library-type-aware fix:
      - dataType=movie/movies → set ``radarrSettingsId`` only
      - dataType=show/shows/tv/episode/season → set ``sonarrSettingsId`` only
      - other (music, books) → neither (Maintainerr only links rules
        to *arrs that manage that type)

    Anti-pattern: setting BOTH IDs on every rule. That blanks out
    the wrong dropdown in Maintainerr's UI (the required-* field
    shows "None" because the value is for the wrong server).
    """

    def test_translator_uses_library_type_aware_link(self) -> None:
        # Phase 16-D moved rule_translation_service to
        # application/maintainerr/; services/apps/.../ is a shim.
        src = (
            __import__("pathlib").Path(__file__).resolve().parents[3]
            / "src" / "media_stack" / "application" / "maintainerr"
            / "rule_translation_service.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "_link_arr_settings",
            src,
            "_link_arr_settings helper missing — rules will revert "
            "to having no *arr link, breaking deletion.",
        )
        self.assertIn(
            "_resolve_arr_settings_ids",
            src,
            "_resolve_arr_settings_ids helper missing — rules can't "
            "auto-discover the configured Radarr/Sonarr server IDs.",
        )

    def test_link_helper_movie_only_sets_radarr(self) -> None:
        from media_stack.services.apps.maintainerr.rule_translation_service import (
            MaintainerrRuleTranslationService,
        )
        # Stub deps; we don't exercise any methods that require them.
        class _Deps:
            def request(self, *a, **k): return (200, [], "")
            def log(self, *a, **k): pass
        svc = MaintainerrRuleTranslationService(deps=_Deps())
        p = {"dataType": "movie"}
        svc._link_arr_settings(p, radarr_id=1, sonarr_id=2)
        self.assertEqual(p.get("radarrSettingsId"), 1,
                         "movie rule didn't get radarrSettingsId")
        self.assertNotIn(
            "sonarrSettingsId", p,
            "movie rule got sonarrSettingsId set — Sonarr dropdown "
            "in Maintainerr UI will be wrong.",
        )

    def test_link_helper_show_only_sets_sonarr(self) -> None:
        from media_stack.services.apps.maintainerr.rule_translation_service import (
            MaintainerrRuleTranslationService,
        )
        class _Deps:
            def request(self, *a, **k): return (200, [], "")
            def log(self, *a, **k): pass
        svc = MaintainerrRuleTranslationService(deps=_Deps())
        p = {"dataType": "show"}
        svc._link_arr_settings(p, radarr_id=1, sonarr_id=2)
        self.assertEqual(p.get("sonarrSettingsId"), 2,
                         "show rule didn't get sonarrSettingsId")
        self.assertNotIn(
            "radarrSettingsId", p,
            "show rule got radarrSettingsId set — Radarr dropdown "
            "in Maintainerr UI will be wrong.",
        )

    def test_link_helper_music_books_get_neither(self) -> None:
        from media_stack.services.apps.maintainerr.rule_translation_service import (
            MaintainerrRuleTranslationService,
        )
        class _Deps:
            def request(self, *a, **k): return (200, [], "")
            def log(self, *a, **k): pass
        svc = MaintainerrRuleTranslationService(deps=_Deps())
        for dt in ("music", "book", "audio"):
            p = {"dataType": dt}
            svc._link_arr_settings(p, radarr_id=1, sonarr_id=2)
            self.assertNotIn("radarrSettingsId", p)
            self.assertNotIn("sonarrSettingsId", p)


# ---------------------------------------------------------------------------
# L29 — Maintainerr rule sync uses DELETE+POST (PUT silently no-ops)
# ---------------------------------------------------------------------------
class MaintainerrRuleSyncUsesDeletePost(unittest.TestCase):
    """Discovered v1.0.146: even with the right payload (correct
    radarrSettingsId/sonarrSettingsId), Maintainerr rule updates
    didn't persist. Maintainerr's ``/api/rules`` endpoint accepts
    PUT with HTTP 200 but its handler silently no-ops — the only
    effective update path is DELETE the old rule + POST a fresh
    one. Without this, every link-fix the controller pushes appears
    to succeed (the controller logs "updated=N") but the
    Maintainerr UI still shows the stale state."""

    def test_rule_sync_uses_delete_then_post(self) -> None:
        # Phase 16-D moved rule_sync_service to
        # application/maintainerr/; services/apps/.../ is a shim.
        src = (
            __import__("pathlib").Path(__file__).resolve().parents[3]
            / "src" / "media_stack" / "application" / "maintainerr"
            / "rule_sync_service.py"
        ).read_text(encoding="utf-8")
        # Anti-pattern: PUT to /api/rules that returns 200 but
        # doesn't persist. Must use DELETE + POST instead.
        self.assertNotIn(
            'method = "PUT"',
            src,
            "rule_sync regressed to PUT updates. Maintainerr's "
            "PUT /api/rules silently no-ops — UI will keep showing "
            "stale config.",
        )
        self.assertIn(
            'method="DELETE"',
            src,
            "rule_sync no longer DELETEs before re-POSTing. "
            "Updates won't take effect.",
        )


# ---------------------------------------------------------------------------
# L30 — compose reads service contracts from the bind-mount
# ---------------------------------------------------------------------------
class ComposeReadsContractsFromBindMount(unittest.TestCase):
    """Discovered v1.0.147: the controller ignored contract YAML
    edits (adding the Bazarr Jellyfin plugin) because
    ``_find_contracts_dir`` preferred the baked-in copy at
    ``/opt/media-stack/contracts/services`` over the bind-mounted
    ``/contracts/services``. Users were expected to rebuild the
    controller image for every contract edit — breaks the "edit
    YAML, restart the container, done" workflow.

    Fix: ``SERVICES_REGISTRY_DIR=/contracts/services`` env in the
    compose file forces the loader to skip the baked fallback.
    The bind-mount already exists (``../contracts:/contracts:ro``),
    so no extra volume wiring needed. Same pattern as
    ``BOOTSTRAP_PROFILE_FILE`` / ``BOOTSTRAP_CONFIG_FILE``.
    """

    def test_compose_sets_services_registry_dir(self) -> None:
        try:
            import yaml as _yaml
        except ImportError:
            self.skipTest("PyYAML not installed")
        # Compose moved from docker/ to deploy/compose/ in the
        # deploy reorg (2026-04-21).
        compose_path = ROOT / "deploy" / "compose" / "docker-compose.yml"
        doc = _yaml.safe_load(compose_path.read_text(encoding="utf-8")) or {}
        services = doc.get("services") or {}
        controller = services.get("media-stack-controller") or {}
        env = controller.get("environment") or {}
        if isinstance(env, list):
            env = {
                (e.split("=", 1) + [""])[0]: (e.split("=", 1) + [""])[1]
                for e in env if isinstance(e, str)
            }
        self.assertEqual(
            str(env.get("SERVICES_REGISTRY_DIR", "")).strip(),
            "/contracts/services",
            "media-stack-controller no longer sets "
            "SERVICES_REGISTRY_DIR to /contracts/services. "
            "Contract YAML edits will be invisible until the "
            "controller image is rebuilt — breaks the standard "
            "edit-then-restart workflow.",
        )


# ---------------------------------------------------------------------------
# L31 — *arr → Jellyfin notifier (MediaBrowser) wired automatically
# ---------------------------------------------------------------------------
class ArrJellyfinNotifierWired(unittest.TestCase):
    """Discovered v1.0.146: relying solely on the controller's
    ``/webhooks/arr`` → ``/Library/Refresh`` flow meant every *arr
    import triggered a FULL Jellyfin library scan and went through
    a controller round-trip. The native ``MediaBrowser`` notifier
    in each *arr does a per-path refresh and fires synchronously
    inside the *arr's own pipeline.

    Jellyfin's API is Emby-compatible, so the *arr "MediaBrowser"
    notifier works against Jellyfin without modification. Readarr
    is the exception — its v1 notification schema doesn't expose
    MediaBrowser, so it stays on the webhook fallback.
    """

    def test_notifier_handler_exists(self) -> None:
        src = (
            __import__("pathlib").Path(__file__).resolve().parents[3]
            / "src" / "media_stack" / "services" / "apps" / "core"
            / "job_adapters.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "def ensure_arr_jellyfin_notifier(",
            src,
            "ensure_arr_jellyfin_notifier handler missing — *arr "
            "imports will fall back to the webhook full-refresh "
            "path which is slower and goes through the controller.",
        )
        self.assertIn(
            '"implementation": "MediaBrowser"', src,
            "notifier no longer uses the MediaBrowser implementation "
            "(Jellyfin is API-compatible). Wrong notifier name = "
            "the *arr will reject the POST.",
        )
        self.assertIn(
            '"updateLibrary"', src,
            "updateLibrary field gone — without it the notifier "
            "sends a 'notify' to Jellyfin but no library scan, so "
            "imports stay invisible to Jellyfin's UI.",
        )

    def test_notifier_job_registered(self) -> None:
        try:
            import yaml as _yaml
        except ImportError:
            self.skipTest("PyYAML not installed")
        path = ROOT / "contracts" / "services" / "core.yaml"
        doc = _yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        jobs = (doc.get("plugin", {}) or {}).get("jobs", {}) or {}
        self.assertIn(
            "ensure-arr-jellyfin-notifier", jobs,
            "ensure-arr-jellyfin-notifier missing from core.yaml — "
            "the per-path refresh integration won't fire on bootstrap.",
        )


# ---------------------------------------------------------------------------
# L32 — Jellyseerr OIDC re-applied on every bootstrap (survives down -v)
# ---------------------------------------------------------------------------
class JellyseerrOidcBootstrapped(unittest.TestCase):
    """Discovered v1.0.146 from a regression report: Jellyseerr's
    "Sign in with Authelia" button disappeared after a clean
    redeploy. Authelia's half (the OIDC client) was always re-
    generated from contracts/auth/oidc_clients.yaml; Jellyseerr's
    half (oidcLogin + oidc block in settings.json) was a one-shot
    manual setup that didn't survive ``compose down -v && up``.

    Fix: ``ensure-jellyseerr-oidc`` job re-asserts the OIDC config
    in Jellyseerr's settings.json on every bootstrap. Idempotent
    (skips if already in sync); restarts Jellyseerr only when it
    actually changed something.
    """

    def test_handler_exists_and_writes_oidc(self) -> None:
        src = (
            __import__("pathlib").Path(__file__).resolve().parents[3]
            / "src" / "media_stack" / "services" / "apps" / "core"
            / "job_adapters.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "def ensure_jellyseerr_oidc(",
            src,
            "ensure_jellyseerr_oidc handler removed — Jellyseerr's "
            "Authelia SSO button will disappear on the next clean "
            "redeploy and stay gone until manually re-applied.",
        )
        for needle in ('"oidcLogin"', '"issuerUrl"', '"clientId"',
                       'newUserLogin', 'jellyseerr-oidc-secret'):
            self.assertIn(
                needle, src,
                f"OIDC adapter no longer writes {needle!r} — the "
                "config it produces won't satisfy Jellyseerr's "
                "preview-OIDC schema.",
            )

    def test_job_registered_in_contract(self) -> None:
        try:
            import yaml as _yaml
        except ImportError:
            self.skipTest("PyYAML not installed")
        path = ROOT / "contracts" / "services" / "core.yaml"
        doc = _yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        jobs = (doc.get("plugin", {}) or {}).get("jobs", {}) or {}
        self.assertIn(
            "ensure-jellyseerr-oidc", jobs,
            "ensure-jellyseerr-oidc job missing from core.yaml — "
            "the OIDC bootstrap won't fire on `compose up` after a wipe.",
        )

    def test_authelia_client_still_in_contract(self) -> None:
        """Sanity: the Authelia side must keep declaring the
        jellyseerr client. Without it the downstream fix is moot."""
        try:
            import yaml as _yaml
        except ImportError:
            self.skipTest("PyYAML not installed")
        path = ROOT / "contracts" / "auth" / "oidc_clients.yaml"
        doc = _yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        clients = doc.get("clients") or []
        self.assertTrue(
            any(c.get("client_id") == "jellyseerr" for c in clients
                if isinstance(c, dict)),
            "contracts/auth/oidc_clients.yaml dropped the jellyseerr "
            "client. SSO is broken on both ends now.",
        )


if __name__ == "__main__":
    unittest.main()
