"""Auto-indexer reputation and quarantine operations for Prowlarr."""

from __future__ import annotations


from media_stack.core.logging_utils import log_swallowed
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
import logging


class ProwlarrReputationOps:

    def coerce_exclude_name_tokens(self, raw_tokens: Any) -> list[str]:
        if isinstance(raw_tokens, list):
            return [str(item).strip().lower() for item in raw_tokens if str(item).strip()]
        if raw_tokens is None:
            return []
        text = str(raw_tokens).strip()
        if not text:
            return []
        return [item.strip().lower() for item in text.split(",") if item.strip()]

    def reputation_key(self, implementation: str, name: str) -> str:
        return f"{implementation}::{name}".lower()

    def load_reputation_state(self, path: Path) -> dict[str, Any]:
        if path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    return loaded
            except Exception as exc:
                log_swallowed(exc)
        return {"schema": 1, "indexers": {}}

    def save_reputation_state(self, service, path: Path, state: dict[str, Any]) -> bool:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            state["updated_at_epoch"] = int(time.time())
            path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
            return True
        except Exception as exc:
            service.log("[WARN] Auto indexer reputation: failed persisting state " f"to {path}: {exc}")
            return False

    def set_indexer_enabled(self,
        service,
        prowlarr_url: str,
        prowlarr_key: str,
        indexer: dict[str, Any],
        enabled: bool,
    ) -> bool:
        idx_id = indexer.get("id")
        if idx_id in (None, ""):
            return False
        payload = dict(indexer)
        payload["enable"] = bool(enabled)
        status, _, body = service.http_request(
            prowlarr_url,
            f"/api/v1/indexer/{idx_id}",
            api_key=prowlarr_key,
            method="PUT",
            payload=payload,
        )
        if status in (200, 201, 202):
            return True
        service.log(
            "[WARN] Auto indexer reputation: failed updating indexer enable state "
            f"(id={idx_id}, enable={enabled}, HTTP {status}): {body}"
        )
        return False

    @staticmethod
    def _load_curated_allowed_definitions(service) -> set[str] | None:
        """Load the curated public-tracker allowlist from
        ``contracts/curated-indexers.yaml``. Returns:

          - ``set[str]`` of allowed Prowlarr definition slugs when
            ``mode: allowlist`` is set in the YAML.
          - ``None`` when ``mode: all`` (no filtering — keep historic
            "discover everything Prowlarr ships" behavior).
          - ``None`` when the YAML can't be loaded — operators with
            no curated config see no behavior change.

        Source-of-truth file is ``contracts/curated-indexers.yaml``;
        an env override (``CURATED_INDEXERS_FILE``) lets sysadmins
        point at their own list.
        """
        env_path = os.environ.get("CURATED_INDEXERS_FILE", "").strip()
        candidates = [
            Path(env_path) if env_path else None,
            Path("/contracts/curated-indexers.yaml"),
            Path(__file__).resolve().parents[4] / "contracts" / "curated-indexers.yaml",
            Path("contracts/curated-indexers.yaml"),
        ]
        for cand in candidates:
            if cand and cand.is_file():
                try:
                    import yaml as _yaml
                    doc = _yaml.safe_load(cand.read_text(encoding="utf-8")) or {}
                    mode = str(doc.get("mode") or "all").lower().strip()
                    if mode != "allowlist":
                        return None
                    # Two accepted forms in the YAML:
                    #   1. ``allowed: [a, b, c]`` flat list (explicit
                    #      override — wins when non-empty).
                    #   2. ``categories: {tv: [...], movies: [...]}``
                    #      grouped by content type (default form;
                    #      union of all categories is the allowlist).
                    flat = doc.get("allowed") or []
                    if not isinstance(flat, list):
                        flat = []
                    merged: list[str] = [str(x) for x in flat if str(x).strip()]
                    if not merged:
                        cats = doc.get("categories") or {}
                        if isinstance(cats, dict):
                            for cat_list in cats.values():
                                if isinstance(cat_list, list):
                                    merged.extend(
                                        str(x) for x in cat_list if str(x).strip()
                                    )
                    out = {x.strip().lower() for x in merged if x.strip()}
                    if out:
                        service.log(
                            f"[INFO] Auto indexer: loaded curated "
                            f"allowlist from {cand} "
                            f"({len(out)} definitions)"
                        )
                        return out
                except Exception as exc:
                    service.log(
                        f"[WARN] Auto indexer: curated allowlist "
                        f"at {cand} failed to parse: {exc}"
                    )
                    return None
        return None

    def auto_add_tested_indexers(self,
        service,
        prowlarr_url: str,
        prowlarr_key: str,
        exclude_name_tokens: list[str] | None = None,
        reputation_cfg: dict[str, Any] | None = None,
        flaresolverr_proxy_id: int | None = None,
    ) -> None:
        status, schemas, body = service.http_request(
            prowlarr_url,
            "/api/v1/indexer/schema",
            api_key=prowlarr_key,
        )
        if status != 200 or not isinstance(schemas, list):
            raise RuntimeError(f"Prowlarr: failed to read indexer schema (HTTP {status}): {body}")

        status, existing, body = service.http_request(
            prowlarr_url,
            "/api/v1/indexer",
            api_key=prowlarr_key,
        )
        if status != 200 or not isinstance(existing, list):
            raise RuntimeError(f"Prowlarr: failed to list existing indexers (HTTP {status}): {body}")

        existing_keys = {
            (item.get("implementation"), item.get("name"))
            for item in existing
            if item.get("implementation") and item.get("name")
        }
        existing_by_key = {
            reputation_key(str(item.get("implementation")), str(item.get("name"))): item
            for item in existing
            if item.get("implementation") and item.get("name")
        }

        candidates: list[dict[str, Any]] = []
        for schema in schemas:
            presets = schema.get("presets") or []
            if presets:
                candidates.extend(presets)
            else:
                candidates.append(schema)

        # Curated allowlist (contracts/curated-indexers.yaml). Default
        # discovery enables every Cardigann definition Prowlarr ships
        # (~70 — most of which are dead, language-specific, or return
        # CloudFlare HTML). The allowlist pins ~10 known-reliable
        # public sources so a fresh deploy actually grabs something
        # on the first SeriesSearch instead of churning. Operators
        # can switch back via ``mode: all`` in the YAML.
        allowed_defs = self._load_curated_allowed_definitions(service)
        if allowed_defs is not None:
            before = len(candidates)
            allowed_lc = {x.lower().strip() for x in allowed_defs}
            candidates = [
                c for c in candidates
                if str(c.get("definitionName") or c.get("implementationName") or "").lower().strip() in allowed_lc
            ]
            service.log(
                f"[INFO] Auto indexer: curated allowlist active "
                f"({len(candidates)}/{before} candidates kept; "
                f"see contracts/curated-indexers.yaml to edit)"
            )

        configured_excludes = coerce_exclude_name_tokens(exclude_name_tokens)
        env_excludes = coerce_exclude_name_tokens(
            os.environ.get("AUTO_INDEXER_EXCLUDE_NAME_TOKENS", "")
        )
        exclude_tokens = list(dict.fromkeys(configured_excludes + env_excludes))
        if exclude_tokens:
            service.log(
                "[INFO] Auto indexer: excluding candidates matching name tokens: "
                + ", ".join(exclude_tokens)
            )

        reputation_cfg = dict(reputation_cfg or {})
        reputation_enabled = bool(reputation_cfg.get("enabled", True))
        reputation_state_path = Path(
            str(
                reputation_cfg.get("state_path")
                or os.environ.get(
                    "AUTO_INDEXER_REPUTATION_STATE_PATH",
                    "/srv-config/prowlarr/indexer-reputation-state.json",
                )
            )
        )
        # FIX A (v1.0.110): on initial discovery, score failures but
        # DO NOT quarantine+disable the indexer in Prowlarr. The
        # disable propagates through ApplicationIndexerSync as a
        # delete from each *arr's DB; *arr re-adds with a NEW id on
        # the next sync; *arr's RSS-cached releases reference the
        # OLD id; grab fails with "IndexerDefinition with ID N does
        # not exist". The reputation system's quarantine is only
        # safe AFTER the stack has stabilized (steady-state probes,
        # not the burst of 629 candidates during discovery).
        # Detect "initial discovery" by checking if the reputation
        # state file is empty / fresh.
        is_initial_discovery = not reputation_state_path.exists() or (
            not load_reputation_state(reputation_state_path).get("indexers")
        )
        quarantine_during_discovery = bool(
            reputation_cfg.get("quarantine_during_discovery", False)
        )
        quarantine_propagates = (
            (not is_initial_discovery) or quarantine_during_discovery
        )
        quarantine_threshold = int(reputation_cfg.get("quarantine_score_threshold", -10))
        quarantine_failures = int(reputation_cfg.get("quarantine_failure_threshold", 3))
        quarantine_ttl_hours = int(reputation_cfg.get("quarantine_ttl_hours", 72))
        success_delta = int(reputation_cfg.get("success_score_delta", 2))
        test_fail_delta = int(reputation_cfg.get("test_failure_score_delta", -4))
        create_fail_delta = int(reputation_cfg.get("create_failure_score_delta", -3))
        allow_untested_fallback = bool(reputation_cfg.get("allow_untested_fallback", False))
        untested_fallback_max_add = int(reputation_cfg.get("untested_fallback_max_add", 5))
        if untested_fallback_max_add <= 0:
            untested_fallback_max_add = 5
        configured_untested_tokens = coerce_exclude_name_tokens(
            reputation_cfg.get("untested_fallback_name_tokens")
        )
        env_untested_tokens = coerce_exclude_name_tokens(
            os.environ.get("AUTO_INDEXER_UNTESTED_FALLBACK_NAME_TOKENS", "")
        )
        untested_name_tokens = list(dict.fromkeys(configured_untested_tokens + env_untested_tokens))
        untested_fallback_added = 0

        reputation_state = load_reputation_state(reputation_state_path)
        if not isinstance(reputation_state.get("indexers"), dict):
            reputation_state["indexers"] = {}
        now_epoch = int(time.time())

        def state_for(impl: str, name: str) -> dict[str, Any]:
            key = reputation_key(impl, name)
            states = reputation_state["indexers"]
            item = states.get(key)
            if not isinstance(item, dict):
                item = {
                    "implementation": impl,
                    "name": name,
                    "score": 0,
                    "successes": 0,
                    "failures": 0,
                    "quarantined": False,
                    "quarantined_at_epoch": 0,
                }
                states[key] = item
            return item

        def maybe_quarantine(impl: str, name: str, rep: dict[str, Any]) -> None:
            nonlocal quarantined_now
            score = int(rep.get("score") or 0)
            failures = int(rep.get("failures") or 0)
            should_quarantine = (
                score <= quarantine_threshold
                and failures >= quarantine_failures
                and not bool(rep.get("quarantined", False))
            )
            if not should_quarantine:
                return

            rep["quarantined"] = True
            rep["quarantined_at_epoch"] = now_epoch
            quarantined_now += 1
            service.log(f"[WARN] Auto indexer: quarantined {name} (score={score}, failures={failures})")
            # Only propagate the disable to Prowlarr (which then
            # propagates as a delete to each *arr) when we're past
            # initial discovery — see "FIX A" note above.
            if not quarantine_propagates:
                service.log(
                    f"[INFO] Auto indexer: keeping {name} in Prowlarr "
                    "(initial-discovery quarantine; reputation-only)"
                )
                return
            existing_item = existing_by_key.get(reputation_key(str(impl), str(name)))
            if existing_item and bool(existing_item.get("enable", True)):
                if set_indexer_enabled(
                    service, prowlarr_url, prowlarr_key, existing_item, enabled=False
                ):
                    service.log(f"[OK] Auto indexer: disabled quarantined indexer {name}")

        heartbeat_every = int(os.environ.get("AUTO_INDEXER_HEARTBEAT_EVERY", "25"))
        heartbeat_every = max(1, heartbeat_every)
        log_skip_details = str(os.environ.get("AUTO_INDEXER_LOG_SKIPS", "0")).strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        # 8 workers cuts a fresh-install indexer-discovery from ~14 min
        # to ~5 min on the default profile (each worker is mostly
        # blocked on Prowlarr's per-indexer Cardigann probe; doubling
        # parallelism roughly halves wall time at no extra CPU cost).
        parallel_workers = int(os.environ.get("AUTO_INDEXER_PARALLEL_WORKERS", "8"))
        parallel_workers = max(1, parallel_workers)
        # Counters for CloudFlare-block accounting — let the summary
        # line (further down) report this without spamming a [FAIL]
        # per CF-blocked indexer with the embedded JSON body.
        cf_blocked = 0
        cf_recovered_via_proxy = 0
        _cf_pat = ("cloudflare", "captcha", "ddos-guard")

        def _is_cf_block(body_text: str) -> bool:
            if not body_text:
                return False
            lower = str(body_text).lower()
            return any(pat in lower for pat in _cf_pat)

        # Thread-safe counters.
        _stats_lock = threading.Lock()
        scanned = 0
        attempted = 0
        added = 0
        skipped_existing = 0
        skipped_excluded = 0
        skipped_test = 0
        failed_create = 0
        quarantined_now = 0
        skipped_quarantined = 0
        untested_fallback_added = 0

        def _test_and_add_candidate(payload: dict[str, Any], impl: str, name: str) -> None:
            """Test and optionally add a single indexer candidate (thread-safe)."""
            nonlocal scanned, attempted, added, skipped_existing, skipped_excluded
            nonlocal skipped_test, failed_create, quarantined_now, skipped_quarantined
            nonlocal untested_fallback_added, cf_blocked, cf_recovered_via_proxy

            with _stats_lock:
                scanned += 1
                _scanned = scanned

            key = (impl, name)
            with _stats_lock:
                if key in existing_keys:
                    skipped_existing += 1
                    return

            with _stats_lock:
                attempted += 1

            if exclude_tokens:
                name_lc = str(name).lower()
                if any(token in name_lc for token in exclude_tokens):
                    with _stats_lock:
                        skipped_excluded += 1
                    if log_skip_details:
                        service.log(f"[SKIP] {name}: excluded by name token policy")
                    return

            rep = state_for(str(impl), str(name))
            if reputation_enabled and bool(rep.get("quarantined", False)):
                quarantined_at = int(rep.get("quarantined_at_epoch") or 0)
                age_seconds = now_epoch - quarantined_at if quarantined_at > 0 else 0
                ttl_seconds = max(0, quarantine_ttl_hours * 3600)
                if ttl_seconds and age_seconds >= ttl_seconds:
                    rep["quarantined"] = False
                    rep["quarantined_at_epoch"] = 0
                    service.log(f"[INFO] Auto indexer: quarantine expired for {name}; retrying.")
                else:
                    with _stats_lock:
                        skipped_quarantined += 1
                    if log_skip_details:
                        service.log(f"[SKIP] {name}: quarantined by reputation policy")
                    return

            try:
                status, _, body = service.http_request(
                    prowlarr_url,
                    "/api/v1/indexer/test",
                    api_key=prowlarr_key,
                    method="POST",
                    payload=payload,
                )
            except Exception as exc:
                status, body = 599, str(exc)
            used_untested_fallback = False
            # CloudFlare retry: if the test failed with a CF-block
            # signature AND we have a FlareSolverr proxy registered,
            # transparently retry with proxyId attached. This is the
            # whole reason FlareSolverr is shipped — without this
            # retry, every CF-protected indexer (1337x, eztv, etc.)
            # silently never gets added.
            if (
                status not in (200, 201, 202)
                and flaresolverr_proxy_id
                and _is_cf_block(str(body))
            ):
                payload = dict(payload)
                payload["proxyId"] = flaresolverr_proxy_id
                try:
                    status, _, body = service.http_request(
                        prowlarr_url,
                        "/api/v1/indexer/test",
                        api_key=prowlarr_key,
                        method="POST",
                        payload=payload,
                    )
                except Exception as exc:
                    status, body = 599, str(exc)
                if status in (200, 201, 202):
                    with _stats_lock:
                        cf_recovered_via_proxy += 1
            if status not in (200, 201, 202):
                with _stats_lock:
                    skipped_test += 1
                    if _is_cf_block(str(body)):
                        cf_blocked += 1
                if reputation_enabled:
                    rep["score"] = int(rep.get("score") or 0) + test_fail_delta
                    rep["failures"] = int(rep.get("failures") or 0) + 1
                    rep["last_failure_epoch"] = now_epoch
                    maybe_quarantine(str(impl), str(name), rep)
                allow_fallback_for_name = not untested_name_tokens or any(
                    token in str(name).lower() for token in untested_name_tokens
                )
                with _stats_lock:
                    allow_fallback = (
                        allow_untested_fallback
                        and untested_fallback_added < untested_fallback_max_add
                        and allow_fallback_for_name
                    )
                if not allow_fallback:
                    if log_skip_details:
                        service.log(f"[SKIP] {name}: test failed (HTTP {status})")
                    return
                # Suppressed the per-indexer "[WARN] Auto indexer:
                # adding untested fallback" line — it fired hundreds
                # of times during a fresh install and read like errors.
                # The summary line at the end reports the count.
                used_untested_fallback = True

            try:
                status, _, body = service.http_request(
                    prowlarr_url,
                    "/api/v1/indexer",
                    api_key=prowlarr_key,
                    method="POST",
                    payload=payload,
                )
            except Exception as exc:
                status, body = 599, str(exc)
            # CF retry on the create call too — sometimes test passes
            # but the actual indexer-add probes the source again and
            # gets blocked. Same recovery path: attach proxyId, retry.
            if (
                status not in (200, 201, 202)
                and flaresolverr_proxy_id
                and not payload.get("proxyId")
                and _is_cf_block(str(body))
            ):
                payload = dict(payload)
                payload["proxyId"] = flaresolverr_proxy_id
                try:
                    status, _, body = service.http_request(
                        prowlarr_url,
                        "/api/v1/indexer",
                        api_key=prowlarr_key,
                        method="POST",
                        payload=payload,
                    )
                except Exception as exc:
                    status, body = 599, str(exc)
                if status in (200, 201, 202):
                    with _stats_lock:
                        cf_recovered_via_proxy += 1
            if status in (200, 201, 202):
                with _stats_lock:
                    existing_keys.add(key)
                    existing_by_key[reputation_key(str(impl), str(name))] = {
                        "implementation": impl,
                        "name": name,
                        "enable": True,
                    }
                    added += 1
                    if used_untested_fallback:
                        untested_fallback_added += 1
                if reputation_enabled:
                    rep["score"] = int(rep.get("score") or 0) + success_delta
                    rep["successes"] = int(rep.get("successes") or 0) + 1
                    rep["last_success_epoch"] = now_epoch
                    rep["quarantined"] = False
                    rep["quarantined_at_epoch"] = 0
                service.log(f"[ADD] {name}")
            elif status == 409 and "UNIQUE constraint failed" in str(body):
                # Already exists — another worker (or a previous run)
                # added it. Not a failure; treat as skipped_existing.
                with _stats_lock:
                    existing_keys.add(key)
                    skipped_existing += 1
            else:
                with _stats_lock:
                    failed_create += 1
                    if _is_cf_block(str(body)):
                        cf_blocked += 1
                if reputation_enabled:
                    rep["score"] = int(rep.get("score") or 0) + create_fail_delta
                    rep["failures"] = int(rep.get("failures") or 0) + 1
                    rep["last_failure_epoch"] = now_epoch
                # Compact log: don't dump the multi-line JSON body for
                # CF blocks (visually scary, repeated dozens of times
                # during a fresh install). Other failures still get
                # the full body for diagnosis.
                if _is_cf_block(str(body)):
                    service.log(
                        f"[SKIP] {name}: CloudFlare-blocked"
                        + (" (FlareSolverr retry also blocked)" if flaresolverr_proxy_id else " (no FlareSolverr proxy configured)")
                    )
                else:
                    service.log(f"[FAIL] {name}: create failed (HTTP {status}) {body}")

            if reputation_enabled:
                maybe_quarantine(str(impl), str(name), rep)

            if _scanned % heartbeat_every == 0:
                with _stats_lock:
                    service.log(
                        "[WAIT] Auto indexer progress: "
                        f"scanned={scanned}/{len(candidates)}, attempted={attempted}, "
                        f"added={added}, skipped_existing={skipped_existing}, skipped_excluded={skipped_excluded}, "
                        f"skipped_quarantined={skipped_quarantined}, skipped_test={skipped_test}, "
                        f"failed_create={failed_create}, quarantined_now={quarantined_now}"
                    )

        # Build workload: pre-filter candidates, then test+add in parallel.
        # De-dupe by (impl, name): Prowlarr's schema list can yield the
        # same indexer twice (e.g. a base Cardigann def AND a preset
        # variant with the same Name). Without this, two parallel
        # workers race on the same indexer — first wins, second hits
        # ``UNIQUE constraint failed: Indexers.Name`` (HTTP 409) and
        # the bootstrap log lights up red on a clean install.
        work_items: list[tuple[dict[str, Any], str, str]] = []
        seen_keys: set[tuple[str, str]] = set()
        for candidate in candidates:
            payload = service.build_indexer_payload(candidate)
            impl = payload.get("implementation")
            name = payload.get("name")
            if not impl or not name:
                continue
            key = (str(impl), str(name))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            work_items.append((payload, str(impl), str(name)))

        if parallel_workers > 1:
            service.log(
                f"[INFO] Auto indexer: testing {len(work_items)} candidates "
                f"with {parallel_workers} parallel workers"
            )
            with ThreadPoolExecutor(max_workers=parallel_workers) as pool:
                futures = [
                    pool.submit(_test_and_add_candidate, payload, impl, name)
                    for payload, impl, name in work_items
                ]
                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception as exc:
                        service.log(f"[WARN] Auto indexer worker error: {exc}")
        else:
            for payload, impl, name in work_items:
                _test_and_add_candidate(payload, impl, name)

        if reputation_enabled:
            save_reputation_state(service, reputation_state_path, reputation_state)

        service.log(
            "[OK] Auto indexer summary: "
            f"scanned={scanned}/{len(candidates)}, attempted={attempted}, added={added}, "
            f"skipped_existing={skipped_existing}, skipped_excluded={skipped_excluded}, skipped_test={skipped_test}, "
            f"skipped_quarantined={skipped_quarantined}, failed_create={failed_create}, "
            f"untested_fallback_added={untested_fallback_added}, "
            f"cf_blocked={cf_blocked}, cf_recovered_via_proxy={cf_recovered_via_proxy}, "
            f"quarantined_now={quarantined_now}"
        )


_instance = ProwlarrReputationOps()
coerce_exclude_name_tokens = _instance.coerce_exclude_name_tokens
reputation_key = _instance.reputation_key
load_reputation_state = _instance.load_reputation_state
save_reputation_state = _instance.save_reputation_state
set_indexer_enabled = _instance.set_indexer_enabled
auto_add_tested_indexers = _instance.auto_add_tested_indexers
