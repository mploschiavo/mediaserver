"""Per-app indexer matching via Prowlarr tags.

The deployment-side fix for Radarr (and friends) shipping with 0
indexers after a fresh bootstrap.

**The problem this solves**

Prowlarr's ApplicationIndexerSync pushes any indexer whose
``capabilities.categories`` overlaps with the app's
``syncCategories`` (e.g., Radarr wants 2000-2999 / Movies). Many
indexers (Bangumi Moe, Mikan, AnimeTosho, anime-focused trackers,
regional torrent sites) declare a movie sub-category in their
schema but their actual probes against a movie query return zero
results. *arr apps test on add and reject with HTTP 400 / "No
Results in configured categories." Result: out of ~73 Prowlarr
indexers, ~33 advertise movie capability, but only a handful
actually return movie results. Everything else gets rejected and
Radarr stays empty.

**The fix**

Pre-test each indexer against each app's category set with a real
search query, then use Prowlarr's tag-based application sync to
filter so only indexers known to return results for an app get
pushed to that app.

Steps:

  1. **Probe**: for each ``(indexer, app)`` pair, issue a
     ``/api/v1/search?type=search&categories=...`` against
     Prowlarr targeting that single indexer + the app's category
     range. Cache results in
     ``/srv-config/prowlarr/indexer-app-match.json`` so we don't
     re-probe on every reconcile.

  2. **Tag**: ensure four Prowlarr tags exist —
     ``sync-sonarr``, ``sync-radarr``, ``sync-lidarr``,
     ``sync-readarr``. PUT each indexer's ``tags`` field to
     include only the tags for apps it returns results for.

  3. **Filter on the app side**: PUT each Prowlarr application's
     ``tags`` field to its sync-tag (``sync-radarr`` for Radarr,
     etc.). Prowlarr's built-in sync semantics then only push
     indexers carrying that tag — no per-indexer rejection from
     the *arr side because the indexer is known-good.

After this runs, ApplicationIndexerSync becomes idempotent and
correct: each *arr gets exactly the indexers that work for its
content type. ``addOnly`` sync (FIX C) plus this per-app
tagging (FIX B) plus no-quarantine-during-discovery (FIX A) =
stable indexer state across reconciles.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from media_stack.core.service_registry.registry import service_internal_url

_log = logging.getLogger("media_stack.indexer_app_match")


# Per-app category ranges. Source: Newznab category numbering
# (https://newznab.readme.io/docs/api-category) which Prowlarr
# adopts wholesale.
APP_CATEGORIES: dict[str, list[int]] = {
    "sonarr":  [5000, 5010, 5020, 5030, 5040, 5045, 5050, 5060, 5070, 5080],   # TV
    "radarr":  [2000, 2010, 2020, 2030, 2040, 2045, 2050, 2060, 2070, 2080, 2090],  # Movies
    "lidarr":  [3000, 3010, 3020, 3030, 3040, 3050, 3060],   # Audio
    "readarr": [7000, 7010, 7020, 7030, 7050, 7060, 8000, 8010, 8020],  # Books
}

# Prowlarr tag label per app.  Stable + descriptive — appears in
# the Prowlarr UI so an operator can see "this indexer is in the
# sync-radarr group" without digging.
APP_TAGS: dict[str, str] = {
    "sonarr":  "sync-sonarr",
    "radarr":  "sync-radarr",
    "lidarr":  "sync-lidarr",
    "readarr": "sync-readarr",
}

# Probe cache: per-indexer-per-app match result.  Cached because
# probing 73 indexers × 4 apps = 292 search queries is slow on a
# fresh install (each query ~1-5s; total ~5-25 minutes worst
# case).  Cache file lives next to indexer-reputation-state.json.
_CACHE_VERSION = 3  # bumped v1.0.140 — probe now uses real query (was empty)
_DEFAULT_CACHE_PATH = Path(
    os.environ.get(
        "INDEXER_APP_MATCH_STATE_PATH",
        "/srv-config/prowlarr/indexer-app-match.json",
    )
)
# Re-probe an indexer only this often once we've already classified
# it once. New installs probe every indexer; subsequent reconciles
# skip the probe for indexers already in the cache.
_CACHE_TTL_HOURS = int(os.environ.get("INDEXER_APP_MATCH_TTL_HOURS", "168"))


def _load_cache(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": _CACHE_VERSION, "indexers": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") != _CACHE_VERSION:
            return {"version": _CACHE_VERSION, "indexers": {}}
        if not isinstance(data.get("indexers"), dict):
            data["indexers"] = {}
        return data
    except Exception as exc:
        _log.debug("indexer-app-match cache read failed: %s", exc)
        return {"version": _CACHE_VERSION, "indexers": {}}


def _save_cache(path: Path, data: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        data["updated_at_epoch"] = int(time.time())
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    except Exception as exc:
        _log.debug("indexer-app-match cache write failed: %s", exc)


def _ensure_prowlarr_tag(
    *,
    prowlarr_url: str,
    api_key: str,
    http_request,
    label: str,
) -> int | None:
    """Return the Prowlarr tag id for ``label``, creating it if
    missing. Returns None on hard failure (caller logs)."""
    status, body, _ = http_request(
        prowlarr_url, "/api/v1/tag", api_key=api_key,
    )
    if status == 200 and isinstance(body, list):
        match = next(
            (t for t in body
             if str(t.get("label", "")).strip().lower() == label.lower()),
            None,
        )
        if match and match.get("id") is not None:
            try:
                return int(match["id"])
            except (TypeError, ValueError):
                logging.getLogger("media_stack").debug("[DEBUG] Swallowed exception", exc_info=True)
    status, body, raw = http_request(
        prowlarr_url, "/api/v1/tag", api_key=api_key,
        method="POST", payload={"label": label},
    )
    if status not in (200, 201, 202):
        return None
    if isinstance(body, dict) and body.get("id") is not None:
        try:
            return int(body["id"])
        except (TypeError, ValueError):
            return None
    return None


def _probe_indexer_for_app(
    *,
    prowlarr_url: str,
    api_key: str,
    http_request,
    indexer_id: int,
    app: str,
) -> bool:
    """True if the indexer returns ≥1 result for the app's
    categories. Uses Prowlarr's ``/api/v1/search`` with type=search
    and the app's category set, scoped to the single indexer.

    Prowlarr's ``/api/v1/search`` requires ``categories`` as
    REPEATED query params (``categories=5000&categories=5010``);
    a comma-separated list returns HTTP 400 with
    ``"value '5000,5010,...' is not a valid categories value"``.
    That 400 had silently classified every indexer as "no app
    match" and poisoned the indexer-app-match cache for the
    project's whole history. (Fixed v1.0.122.)
    """
    cats = APP_CATEGORIES.get(app, [])
    if not cats:
        return False
    # Per-app probe term. An EMPTY ``query=`` returned 0 hits for many
    # broad indexers (TPB, 1337x, etc.) when combined with a category
    # filter — Cardigann definitions translate the empty query into a
    # request that the upstream tracker can't satisfy. The fix is a
    # short, well-seeded term that any tracker covering the app's
    # content type WILL have. Public-domain / well-known content keeps
    # this safe as a probe (no risk of returning nothing for legal
    # reasons). (v1.0.140 — root-caused after Pirate Bay returned 100
    # hits for "Inception" with no category filter and 0 hits for the
    # same query with categories=2000.)
    _PROBE_QUERY = {
        "sonarr":  "office",   # broad TV title — eztv, TPB, etc. carry it.
        "radarr":  "inception",  # Famous movie — every movie tracker has it.
        "lidarr":  "metallica",  # Major artist — universal music coverage.
        "readarr": "shakespeare",  # Classic author — universal book coverage.
    }
    query = _PROBE_QUERY.get(app, "linux")
    cat_qs = "&".join(f"categories={c}" for c in cats)
    from urllib.parse import quote as _quote
    path = (
        f"/api/v1/search?type=search&query={_quote(query)}&{cat_qs}"
        f"&indexerIds={indexer_id}&limit=5"
    )
    status, body, _ = http_request(prowlarr_url, path, api_key=api_key)
    if status != 200:
        return False
    if not isinstance(body, list):
        return False
    return len(body) > 0


def _resolve_per_indexer_apps(
    *,
    prowlarr_url: str,
    api_key: str,
    http_request,
    indexer: dict[str, Any],
    cache: dict[str, Any],
    log,
) -> set[str]:
    """Return the set of app names the indexer should sync to.
    Reads from cache first; only probes when the cache entry is
    missing or expired."""
    iid = indexer.get("id")
    impl = indexer.get("implementation", "")
    name = indexer.get("name", "")
    if iid is None:
        return set()
    cache_key = f"{iid}:{impl}:{name}"
    entry = cache["indexers"].get(cache_key) or {}
    now = int(time.time())
    ttl_seconds = max(1, _CACHE_TTL_HOURS) * 3600
    age = now - int(entry.get("probed_at_epoch") or 0)

    matched: set[str] = set()
    if age < ttl_seconds and isinstance(entry.get("apps"), list):
        matched = set(entry["apps"])
        return matched

    # Fresh probe.
    apps_with_results: list[str] = []
    for app, cats in APP_CATEGORIES.items():
        # Capability pre-filter: skip the search if the indexer
        # doesn't even claim to support any of the app's
        # categories.  Cheap win — the schema check is local.
        cap_cats = (indexer.get("capabilities") or {}).get("categories") or []
        cap_ids: set[int] = set()
        for c in cap_cats:
            try:
                cap_ids.add(int(c.get("id")))
            except (TypeError, ValueError):
                logging.getLogger("media_stack").debug("[DEBUG] Swallowed exception", exc_info=True)
            for sub in (c.get("subCategories") or []):
                try:
                    cap_ids.add(int(sub.get("id")))
                except (TypeError, ValueError):
                    logging.getLogger("media_stack").debug("[DEBUG] Swallowed exception", exc_info=True)
        if cap_ids and not any(c in cap_ids for c in cats):
            continue
        if _probe_indexer_for_app(
            prowlarr_url=prowlarr_url,
            api_key=api_key,
            http_request=http_request,
            indexer_id=int(iid),
            app=app,
        ):
            apps_with_results.append(app)
    cache["indexers"][cache_key] = {
        "id": int(iid),
        "implementation": impl,
        "name": name,
        "apps": apps_with_results,
        "probed_at_epoch": now,
    }
    if apps_with_results:
        log(f"[OK] indexer-app-match: {name} → {','.join(apps_with_results)}")
    else:
        log(f"[INFO] indexer-app-match: {name} → no app match (capability claims didn't survive probe)")
    return set(apps_with_results)


def apply_indexer_app_tags(
    *,
    prowlarr_url: str,
    prowlarr_api_key: str,
    http_request,
    log,
    cache_path: Path | None = None,
) -> dict[str, Any]:
    """Run the full tagging pipeline.

    1. Ensure Prowlarr has a tag per app (sync-sonarr, etc.).
    2. For each indexer, probe per app + tag accordingly.
    3. For each Prowlarr application, set its ``tags`` field to
       its sync-tag so Prowlarr's normal ApplicationIndexerSync
       only pushes matching indexers.

    Returns a summary ``{tags: {app: id}, indexers_classified: N,
    cache_hits: N, app_tag_set: {app: bool}}``."""
    cache_path = cache_path or _DEFAULT_CACHE_PATH
    cache = _load_cache(cache_path)

    # Step 1 — tags.
    tag_ids: dict[str, int] = {}
    for app, label in APP_TAGS.items():
        tid = _ensure_prowlarr_tag(
            prowlarr_url=prowlarr_url,
            api_key=prowlarr_api_key,
            http_request=http_request,
            label=label,
        )
        if tid is None:
            log(f"[WARN] indexer-app-match: failed to ensure tag '{label}'")
            continue
        tag_ids[app] = tid

    # Step 2 — probe + tag indexers.
    status, indexers, _ = http_request(
        prowlarr_url, "/api/v1/indexer", api_key=prowlarr_api_key,
    )
    if status != 200 or not isinstance(indexers, list):
        return {"error": f"failed to list indexers (HTTP {status})"}

    # Which download-client protocols are actually available?
    # If SABnzbd is off (usenet disabled), usenet-only indexers
    # must NOT be tagged — Sonarr/Radarr would try to grab the
    # NZB, hand it to their only download client (qBit), which
    # reads the NZB bytes as a torrent and crashes with
    # ``MonoTorrent.TorrentException: Invalid torrent file``.
    # Every grab attempt then fills the *arr queue with
    # ``downloadClientUnavailable`` and qBit stays at zero.
    # (v1.0.130 — 7th bug in the qBit-not-downloading chain.)
    _arr_status, _arr_proxies, _ = http_request(
        prowlarr_url, "/api/v1/applications", api_key=prowlarr_api_key,
    )
    # Query Prowlarr's download client list: Prowlarr proxies DLs
    # to the *arrs; it's enough to know whether any *arr has at
    # least one torrent + usenet client respectively by checking
    # the indexer's protocol against our local SAB-enabled flag.
    # Simpler heuristic: SAB is the only usenet client we ship.
    # If the SABnzbd service is unreachable OR explicitly off,
    # usenet indexers can't produce grabs.
    sab_reachable = False
    try:
        _s, _b, _ = http_request(
            service_internal_url("sabnzbd"), "/sabnzbd/api?mode=version",
            api_key="",
        )
        sab_reachable = (_s == 200)
    except Exception:
        sab_reachable = False

    cache_hits = 0
    classified = 0
    for idx in indexers:
        iid = idx.get("id")
        if iid is None:
            continue
        # Usenet indexers are useless without a usenet download
        # client. SAB is the only one we ship; when it's
        # unreachable, ACTIVELY UNTAG any usenet indexer that
        # carries our sync-* tags so Prowlarr stops pushing them
        # to *arrs. Without the un-tag step, indexers tagged
        # under a previous code revision stay tagged forever and
        # the *arr keeps grabbing NZBs into its torrent client
        # (the v1.0.130 skip-on-tag fix prevented NEW tagging
        # but didn't repair the old state). Self-healing on every
        # tag run.
        if idx.get("protocol") == "usenet" and not sab_reachable:
            current_tags = list(idx.get("tags") or [])
            sync_tag_id_set_for_purge = set()
            # Re-fetch tag ids defensively in case tag_ids was
            # not yet populated (e.g. a tag-ensure failed earlier).
            try:
                _st, _tags_list, _ = http_request(
                    prowlarr_url, "/api/v1/tag",
                    api_key=prowlarr_api_key,
                )
                if _st == 200 and isinstance(_tags_list, list):
                    sync_tag_id_set_for_purge = {
                        int(t["id"]) for t in _tags_list
                        if str(t.get("label", "")).startswith("sync-")
                        and t.get("id") is not None
                    }
            except Exception:
                sync_tag_id_set_for_purge = set(tag_ids.values())
            stripped = [t for t in current_tags if t not in sync_tag_id_set_for_purge]
            if sorted(stripped) != sorted(current_tags):
                body = dict(idx)
                body["tags"] = stripped
                _st, _, _ = http_request(
                    prowlarr_url, f"/api/v1/indexer/{iid}",
                    api_key=prowlarr_api_key,
                    method="PUT", payload=body,
                )
                if _st in (200, 201, 202):
                    log(
                        f"[OK] indexer-app-match: untagged usenet "
                        f"indexer '{idx.get('name','?')}' (SAB not "
                        f"reachable)"
                    )
                else:
                    log(
                        f"[WARN] indexer-app-match: failed to untag "
                        f"usenet indexer '{idx.get('name','?')}' "
                        f"(HTTP {_st})"
                    )
            else:
                log(
                    f"[INFO] indexer-app-match: skipping usenet "
                    f"indexer '{idx.get('name','?')}' (SAB not "
                    f"reachable — no usenet download client)"
                )
            continue
        cache_key_prefix = f"{iid}:"
        had_cache = any(
            k.startswith(cache_key_prefix) for k in cache["indexers"]
        )
        matched_apps = _resolve_per_indexer_apps(
            prowlarr_url=prowlarr_url,
            api_key=prowlarr_api_key,
            http_request=http_request,
            indexer=idx,
            cache=cache,
            log=log,
        )
        if had_cache:
            cache_hits += 1
        classified += 1

        desired_tag_ids = sorted({
            tag_ids[app] for app in matched_apps if app in tag_ids
        })
        # Preserve any non-sync-* tags the operator added manually.
        current_tags = list(idx.get("tags") or [])
        sync_tag_id_set = set(tag_ids.values())
        non_sync_tags = [t for t in current_tags if t not in sync_tag_id_set]
        new_tags = sorted(set(non_sync_tags) | set(desired_tag_ids))
        if sorted(current_tags) == new_tags:
            continue
        body = dict(idx)
        body["tags"] = new_tags
        st, _, _ = http_request(
            prowlarr_url, f"/api/v1/indexer/{iid}",
            api_key=prowlarr_api_key,
            method="PUT", payload=body,
        )
        if st in (200, 201, 202):
            log(
                f"[OK] indexer-app-match: tagged {idx.get('name','?')} "
                f"with {desired_tag_ids}"
            )
        else:
            log(
                f"[WARN] indexer-app-match: failed to tag "
                f"{idx.get('name','?')} (HTTP {st})"
            )

    _save_cache(cache_path, cache)

    # Step 3 — set each Prowlarr application's tags field.
    status, apps, _ = http_request(
        prowlarr_url, "/api/v1/applications", api_key=prowlarr_api_key,
    )
    app_tag_set: dict[str, bool] = {}
    if status == 200 and isinstance(apps, list):
        for prow_app in apps:
            impl = str(prow_app.get("implementation", "")).lower()
            target_tag = tag_ids.get(impl)
            if target_tag is None:
                continue
            current = list(prow_app.get("tags") or [])
            sync_tag_id_set = set(tag_ids.values())
            other_tags = [t for t in current if t not in sync_tag_id_set]
            desired = sorted(set(other_tags) | {target_tag})
            if sorted(current) == desired:
                app_tag_set[impl] = True
                continue
            body = dict(prow_app)
            body["tags"] = desired
            st, _, _ = http_request(
                prowlarr_url, f"/api/v1/applications/{prow_app.get('id')}",
                api_key=prowlarr_api_key,
                method="PUT", payload=body,
            )
            if st in (200, 201, 202):
                app_tag_set[impl] = True
                log(
                    f"[OK] indexer-app-match: {prow_app.get('name','?')} "
                    f"now filters by tag '{APP_TAGS[impl]}'"
                )
            else:
                app_tag_set[impl] = False
                log(
                    f"[WARN] indexer-app-match: failed to set tag on "
                    f"{prow_app.get('name','?')} (HTTP {st})"
                )

    return {
        "tags": tag_ids,
        "indexers_classified": classified,
        "cache_hits": cache_hits,
        "app_tag_set": app_tag_set,
    }
