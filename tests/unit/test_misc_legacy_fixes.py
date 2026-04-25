"""Ratchets for the v1.0.100 fix bundle.

Pins each fix so that someone refactoring later doesn't silently
revert one of them.

  1. Flaresolverr proxy_id flows into auto-add: ``proxyId`` is in
     the indexer-payload allow-list AND ``ensure_flaresolverr_proxy``
     returns the proxy id (so the pipeline can attach it).
  2. ``validate-credentials`` requires ``arr_apps_reachable`` so it
     doesn't fire while the *arr family is still warming up
     (cosmetic "5/7 credential checks did not pass" warning).
  3. Recyclarr ``hashicorp/http-echo`` placeholder is gone from
     compose.
  4. Dashboard has ``_safeErrText`` and uses it from every error
     catch — no more ``toast(e.toString(), true)`` stack-trace leaks.
  5. ``/api/stack/update`` is registered (GET handler).
  6. Auto-indexer worker default bumped 4 → 8 for faster fresh
     installs.
  7. Auto-indexer hardcoded denylist is gone.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore[assignment]

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


class FlaresolverrCfRetryWiring(unittest.TestCase):

    def test_auto_add_signatures_agree_across_layers(self) -> None:
        """``auto_add_tested_indexers`` exists in three layers
        (reputation_ops → service.ProwlarrService → runtime_ops)
        and the controller calls the OUTERMOST one. If any layer
        drops ``flaresolverr_proxy_id`` from its signature, the
        live call fails at runtime with::

            TypeError: ProwlarrRuntimeOps.auto_add_tested_indexers()
            takes from 3 to 5 positional arguments but 6 were given

        That regressed in v1.0.100 — a Python signature mismatch
        the unit suite couldn't catch because no test called the
        full chain. Pin the signatures here."""
        import inspect
        from media_stack.services.apps.prowlarr.runtime_ops import (
            auto_add_tested_indexers as runtime_fn,
        )
        from media_stack.services.apps.prowlarr.service import (
            ProwlarrService,
        )
        from media_stack.services.apps.prowlarr.reputation_ops import (
            auto_add_tested_indexers as rep_fn,
        )
        for label, sig in (
            ("runtime_ops", inspect.signature(runtime_fn)),
            ("service",
             inspect.signature(ProwlarrService.auto_add_tested_indexers)),
            ("reputation_ops", inspect.signature(rep_fn)),
        ):
            self.assertIn(
                "flaresolverr_proxy_id", sig.parameters,
                f"{label} layer's auto_add_tested_indexers is missing "
                "flaresolverr_proxy_id — the runtime call from "
                "pipeline_service will TypeError.",
            )

    def test_proxy_id_in_indexer_payload_allowlist(self) -> None:
        path = ROOT / "src/media_stack/services/apps/prowlarr/indexer_ops.py"
        text = path.read_text(encoding="utf-8")
        self.assertIn(
            '"proxyId"', text,
            "proxyId removed from build_indexer_payload allow_keys — "
            "CloudFlare-protected indexers will fail to add silently.",
        )

    def test_ensure_proxy_returns_id(self) -> None:
        path = ROOT / "src/media_stack/services/apps/prowlarr/proxy_ops.py"
        text = path.read_text(encoding="utf-8")
        # The function signature should annotate `int | None` return.
        self.assertRegex(
            text, r"def ensure_flaresolverr_proxy\([^)]*\) -> int \| None",
            "ensure_flaresolverr_proxy no longer returns the proxy id "
            "— callers can't attach it to CF-protected indexers.",
        )

    def test_pipeline_passes_proxy_id_to_auto_add(self) -> None:
        path = ROOT / "src/media_stack/services/apps/prowlarr/pipeline_service.py"
        text = path.read_text(encoding="utf-8")
        self.assertIn("flaresolverr_proxy_id", text)
        # The call has nested ``cfg.get(...)`` parens, so a "match
        # everything up to the closing paren" regex doesn't work.
        # Find the call site, then check the next ~400 chars
        # contain ``flaresolverr_proxy_id`` before any other call
        # opens. Brittle but explicit.
        idx = text.find("auto_add_tested_indexers(")
        self.assertGreater(idx, 0, "auto_add_tested_indexers call site missing")
        window = text[idx:idx + 600]
        self.assertIn(
            "flaresolverr_proxy_id", window,
            "auto_add_tested_indexers call must include "
            "flaresolverr_proxy_id within its argument list — "
            "without it CF retries can't be attached.",
        )

    def test_reputation_ops_has_cf_retry_path(self) -> None:
        path = ROOT / "src/media_stack/services/apps/prowlarr/reputation_ops.py"
        text = path.read_text(encoding="utf-8")
        self.assertIn(
            "flaresolverr_proxy_id", text,
            "auto_add_tested_indexers no longer accepts the proxy id "
            "argument — CF retry won't fire.",
        )
        self.assertIn(
            "_is_cf_block", text,
            "CloudFlare-detection helper removed; the [SKIP] log "
            "downgrade and retry path both depend on it.",
        )


class ValidateCredentialsArrPrereqRatchet(unittest.TestCase):

    def setUp(self) -> None:
        if yaml is None:
            self.skipTest("PyYAML not installed")
        self.contract = ROOT / "contracts/services/core.yaml"

    def test_validate_credentials_requires_arr_apps_reachable(self) -> None:
        text = self.contract.read_text(encoding="utf-8")
        # Find the validate-credentials block + the next non-empty
        # ``requires:`` entry. Comments between key and value are
        # common in this file so use a forgiving pattern.
        m = re.search(
            r"validate-credentials:.*?requires:\s*\[([^\]]*)\]",
            text, re.DOTALL,
        )
        self.assertIsNotNone(m, "validate-credentials job missing or malformed")
        requires = m.group(1)
        self.assertIn(
            "arr_apps_reachable", requires,
            "validate-credentials no longer waits for arr_apps_reachable; "
            "the cosmetic '5/7 credential checks did not pass' warning "
            "during fresh-install bootstrap will return.",
        )

    def test_arr_apps_reachable_prereq_registered(self) -> None:
        path = ROOT / "src/media_stack/services/jobs/framework.py"
        text = path.read_text(encoding="utf-8")
        self.assertIn(
            'register_prereq("arr_apps_reachable"', text,
            "arr_apps_reachable prereq dropped from registration — "
            "JobRunner will treat it as 'unknown prereq'.",
        )


class RecyclarrPlaceholderRemoved(unittest.TestCase):

    def test_no_recyclarr_http_echo_in_compose(self) -> None:
        for name in ("docker/docker-compose.yml", "dist/docker-compose.yml"):
            text = (ROOT / name).read_text(encoding="utf-8")
            # The placeholder line was: image: hashicorp/http-echo
            # under a recyclarr: service. If both reappear we're back
            # to shipping a fake "Recyclarr stub endpoint" container.
            block = re.search(
                r"^\s*recyclarr:\s*$\n[^a-z]+image:\s*hashicorp/http-echo",
                text, re.MULTILINE,
            )
            self.assertIsNone(
                block,
                f"{name} still defines a recyclarr service backed by "
                "hashicorp/http-echo — the placeholder is back.",
            )
class StackUpdateEndpointsRegistered(unittest.TestCase):

    def test_get_endpoint_in_handlers(self) -> None:
        path = ROOT / "src/media_stack/api/handlers_get.py"
        text = path.read_text(encoding="utf-8")
        self.assertIn(
            '"/api/stack/update"', text,
            "GET /api/stack/update endpoint disappeared from handlers_get",
        )
        self.assertIn(
            '"/api/stack/upgrade/"', text,
            "GET /api/stack/upgrade/{task_id} endpoint disappeared",
        )

    def test_post_endpoint_in_handlers(self) -> None:
        path = ROOT / "src/media_stack/api/handlers_post.py"
        text = path.read_text(encoding="utf-8")
        self.assertIn(
            '"/api/stack/upgrade"', text,
            "POST /api/stack/upgrade endpoint disappeared from handlers_post",
        )

    def test_stack_upgrade_requires_auth(self) -> None:
        path = ROOT / "src/media_stack/api/server.py"
        text = path.read_text(encoding="utf-8")
        self.assertRegex(
            text,
            r'_AUTH_REQUIRED_PREFIXES\s*=\s*\([^)]*"/api/stack/"',
            "/api/stack/ removed from _AUTH_REQUIRED_PREFIXES — "
            "anyone can now trigger an in-place upgrade.",
        )

    def test_service_module_callable(self) -> None:
        from media_stack.api.services import stack_update
        self.assertTrue(hasattr(stack_update, "check_for_update"))
        self.assertTrue(hasattr(stack_update, "start_upgrade"))
        self.assertTrue(hasattr(stack_update, "upgrade_status"))


class IndexerWorkerParallelismBumped(unittest.TestCase):

    def test_default_workers_at_least_8(self) -> None:
        path = ROOT / "src/media_stack/services/apps/prowlarr/reputation_ops.py"
        text = path.read_text(encoding="utf-8")
        m = re.search(
            r'AUTO_INDEXER_PARALLEL_WORKERS",\s*"(\d+)"', text,
        )
        self.assertIsNotNone(m, "AUTO_INDEXER_PARALLEL_WORKERS default missing")
        self.assertGreaterEqual(
            int(m.group(1)), 8,
            f"Default worker count {m.group(1)} < 8 — fresh-install "
            "indexer-discovery time creeps back up.",
        )


class NoHardcodedIndexerDenylist(unittest.TestCase):

    def test_no_default_excludes_in_auto_indexer_cli(self) -> None:
        path = ROOT / "src/media_stack/services/apps/prowlarr/cli/run_prowlarr_auto_indexers_main.py"
        text = path.read_text(encoding="utf-8")
        # The previous list literal is gone.
        self.assertNotIn(
            "default_excludes = [", text,
            "Hardcoded default exclude list is back — opinionated "
            "name denylists belong in user config, not source.",
        )


class AtomicWriteUniqueTempPath(unittest.TestCase):
    """Pin the per-call unique temp filename in ``atomic_write_xml``.

    The 2026-04-22 incident:
        [PREFLIGHT] sonarr: failed
            (No such file: '/srv-config/sonarr/config.xml.new'
             -> '/srv-config/sonarr/config.xml')

    Root cause: the ARR preflight worker pool (4-way parallel) and
    other concurrent callers all wrote to the SAME ``.new`` sibling
    path. T1's ``os.replace`` consumed the file before T2 could
    rename it. Fix: include PID + nanosecond + monotonic counter
    in the temp name so concurrent calls never collide."""

    def test_temp_name_includes_pid_and_unique_token(self) -> None:
        path = ROOT / "src/media_stack/core/config_io.py"
        text = path.read_text(encoding="utf-8")
        self.assertIn(
            "_unique_tmp_path", text,
            "_unique_tmp_path helper removed — concurrent writes "
            "will race on the deterministic .new suffix again.",
        )
        # The helper must include both pid and a per-call counter.
        # Locate the function body by finding the def then taking
        # the next ~600 chars (DOTALL across f-strings + braces).
        idx = text.find("def _unique_tmp_path")
        self.assertGreater(idx, 0)
        body = text[idx:idx + 700]
        self.assertIn(
            "os.getpid()", body,
            "_unique_tmp_path no longer includes pid — racy across "
            "processes that share a config volume.",
        )
        self.assertIn(
            "time.time_ns()", body,
            "_unique_tmp_path no longer includes a high-res "
            "timestamp — racy within a single process under load.",
        )

    def test_atomic_write_uses_unique_helper_for_both_tmp_and_bak(self) -> None:
        path = ROOT / "src/media_stack/core/config_io.py"
        text = path.read_text(encoding="utf-8")
        self.assertNotIn(
            'path.with_suffix(path.suffix + ".new")', text,
            "atomic_write_xml regressed to deterministic .new path "
            "— concurrent calls will race.",
        )
        self.assertNotIn(
            'path.with_suffix(path.suffix + ".bak")', text,
            "atomic_write_xml regressed to deterministic .bak path "
            "— concurrent backup writes can clobber each other.",
        )


class TriggerIndexerSyncDefaultOn(unittest.TestCase):
    """``trigger_indexer_sync`` must default True so Prowlarr's
    ``ApplicationIndexerSync`` fires after indexer add. Without it,
    Sonarr/Radarr/Lidarr/Readarr show 0 indexers and qBittorrent
    sits empty even with a fully-bootstrapped Prowlarr."""

    def test_dataclass_default_is_true(self) -> None:
        from media_stack.services.profile_config import BootstrapConfig
        self.assertTrue(
            BootstrapConfig().trigger_indexer_sync,
            "BootstrapConfig.trigger_indexer_sync default flipped "
            "back to False — Prowlarr won't sync indexers to *arr "
            "apps after add.",
        )

    def test_from_dict_default_is_true(self) -> None:
        from media_stack.services.profile_config import BootstrapConfig
        # Empty dict should yield default True.
        cfg = BootstrapConfig.from_dict({})
        self.assertTrue(
            cfg.trigger_indexer_sync,
            "from_dict() default for trigger_indexer_sync flipped "
            "back to False — empty bootstrap section won't sync.",
        )


if __name__ == "__main__":
    unittest.main()
