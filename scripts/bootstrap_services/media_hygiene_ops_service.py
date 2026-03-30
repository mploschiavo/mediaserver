"""Media hygiene operation helpers extracted from bootstrap-apps."""

from __future__ import annotations

import json
import os
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib import request

LogFn = Callable[[str], None]
BoolCfgFn = Callable[[dict[str, Any], str, bool], bool]
CoerceListFn = Callable[[Any], list[Any]]
ToIntFn = Callable[[Any, int | None], int | None]
ToFloatFn = Callable[[Any, float | None], float | None]
NormalizeTokenFn = Callable[[Any], str]
NormalizeUrlFn = Callable[[str], str]
QbitLoginFn = Callable[[str, str, str], Any]
QbitListCompletedFn = Callable[[Any, str], list[dict[str, Any]]]
QbitDeleteFn = Callable[[Any, str, list[str], bool], None]
QbitSetPreferencesFn = Callable[[Any, str, dict[str, Any]], None]


@dataclass
class MediaHygieneOpsService:
    log: LogFn
    bool_cfg: BoolCfgFn
    coerce_list: CoerceListFn
    to_int: ToIntFn
    to_float: ToFloatFn
    normalize_token: NormalizeTokenFn
    normalize_url: NormalizeUrlFn
    qbit_login: QbitLoginFn
    qbit_list_completed_torrents: QbitListCompletedFn
    qbit_delete_torrents: QbitDeleteFn
    qbit_set_preferences: QbitSetPreferencesFn

    def _walk_existing_files(self, paths: list[Path]):
        for root in paths:
            if not root.exists():
                continue
            for dirpath, _, filenames in os.walk(root):
                base = Path(dirpath)
                for name in filenames:
                    yield base / name

    def run_filesystem_hygiene(self, hygiene_cfg: dict[str, Any]) -> dict[str, int]:
        fs_cfg = hygiene_cfg.get("filesystem") or {}
        if not self.bool_cfg(fs_cfg, "enabled", True):
            return {
                "removed_temp": 0,
                "removed_zero": 0,
                "removed_dupes": 0,
                "removed_empty_dirs": 0,
            }

        default_roots = [
            "/srv-stack/data/torrents/incomplete",
            "/srv-stack/data/torrents/completed",
            "/srv-stack/data/usenet/incomplete",
            "/srv-stack/data/usenet/completed",
        ]
        raw_roots = self.coerce_list(fs_cfg.get("roots")) or default_roots
        roots = [Path(str(p)).resolve() for p in raw_roots if str(p).strip()]
        min_age_hours = self.to_float(fs_cfg.get("min_file_age_hours"), 24.0)
        if min_age_hours is None:
            min_age_hours = 24.0
        now_ts = time.time()

        remove_zero = self.bool_cfg(fs_cfg, "remove_zero_byte_files", True)
        temp_extensions = {
            str(x).strip().lower()
            for x in self.coerce_list(fs_cfg.get("temp_extensions"))
            if str(x).strip()
        } or {".part", ".tmp", ".temp", ".nzb", ".!qb"}
        remove_empty_dirs = self.bool_cfg(fs_cfg, "remove_empty_dirs", True)

        dedupe_cfg = fs_cfg.get("dedupe") or {}
        dedupe_enabled = self.bool_cfg(dedupe_cfg, "enabled", True)
        dedupe_dry_run = self.bool_cfg(dedupe_cfg, "dry_run", False)
        dedupe_max_deletes = self.to_int(dedupe_cfg.get("max_delete_per_run"), 20) or 20
        dedupe_min_size = self.to_int(dedupe_cfg.get("min_size_bytes"), 100 * 1024 * 1024) or (
            100 * 1024 * 1024
        )

        removed_temp = 0
        removed_zero = 0
        removed_dupes = 0
        removed_empty = 0
        dedupe_map: dict[tuple[str, int], list[tuple[Path, float]]] = defaultdict(list)

        for file_path in self._walk_existing_files(roots):
            try:
                st = file_path.stat()
            except FileNotFoundError:
                continue
            except Exception:
                continue

            age_hours = max(0.0, (now_ts - float(st.st_mtime)) / 3600.0)
            suffix = file_path.suffix.lower()
            if age_hours >= min_age_hours:
                if remove_zero and int(st.st_size) <= 0:
                    try:
                        file_path.unlink()
                        removed_zero += 1
                    except Exception:
                        pass
                    continue
                if suffix in temp_extensions:
                    try:
                        file_path.unlink()
                        removed_temp += 1
                    except Exception:
                        pass
                    continue

            if dedupe_enabled and int(st.st_size) >= dedupe_min_size:
                key = (file_path.name.lower(), int(st.st_size))
                dedupe_map[key].append((file_path, st.st_mtime))

        if dedupe_enabled and dedupe_map:
            deletions_left = dedupe_max_deletes
            for _, items in dedupe_map.items():
                if deletions_left <= 0:
                    break
                if len(items) <= 1:
                    continue
                items.sort(key=lambda t: t[1], reverse=True)
                for dup_path, _ in items[1:]:
                    if deletions_left <= 0:
                        break
                    if dedupe_dry_run:
                        self.log(f"[INFO] Media hygiene: dedupe candidate (dry-run): {dup_path}")
                        continue
                    try:
                        dup_path.unlink()
                        removed_dupes += 1
                        deletions_left -= 1
                        self.log(f"[OK] Media hygiene: removed duplicate file {dup_path}")
                    except Exception:
                        continue

        if remove_empty_dirs:
            for root in roots:
                if not root.exists():
                    continue
                for dirpath, dirnames, filenames in os.walk(root, topdown=False):
                    if dirnames or filenames:
                        continue
                    p = Path(dirpath)
                    if p == root:
                        continue
                    try:
                        p.rmdir()
                        removed_empty += 1
                    except Exception:
                        continue

        summary = {
            "removed_temp": removed_temp,
            "removed_zero": removed_zero,
            "removed_dupes": removed_dupes,
            "removed_empty_dirs": removed_empty,
        }
        self.log(
            "[OK] Media hygiene filesystem cleanup: "
            f"temp={removed_temp}, zero_byte={removed_zero}, duplicates={removed_dupes}, empty_dirs={removed_empty}"
        )
        return summary

    def run_qbit_duplicate_prune(
        self,
        hygiene_cfg: dict[str, Any],
        qbit_cfg: dict[str, Any],
        qb_username: str,
        qb_password: str,
    ) -> dict[str, Any]:
        prune_cfg = hygiene_cfg.get("qbit_duplicate_prune") or {}
        enabled = self.bool_cfg(prune_cfg, "enabled", False)
        summary: dict[str, Any] = {
            "enabled": enabled,
            "dry_run": self.bool_cfg(prune_cfg, "dry_run", False),
            "groups": 0,
            "candidates": 0,
            "deleted": 0,
        }
        if not enabled:
            return summary

        if not str(qb_username or "").strip() or not str(qb_password or "").strip():
            raise RuntimeError(
                "qB duplicate prune requires qB credentials (QBITTORRENT_USERNAME/QBITTORRENT_PASSWORD)."
            )

        qbit_url = self.normalize_url((qbit_cfg or {}).get("url", "http://qbittorrent:8080"))
        dry_run = bool(summary["dry_run"])
        delete_files = self.bool_cfg(prune_cfg, "delete_files", False)
        max_delete_per_run = self.to_int(prune_cfg.get("max_delete_per_run"), 30)
        if max_delete_per_run is None or max_delete_per_run <= 0:
            max_delete_per_run = 30
        min_completion_age_hours = self.to_float(prune_cfg.get("min_completion_age_hours"), 24.0)
        if min_completion_age_hours is None:
            min_completion_age_hours = 24.0
        keep_strategy = str(prune_cfg.get("keep", "oldest") or "oldest").strip().lower()
        if keep_strategy not in ("oldest", "newest"):
            keep_strategy = "oldest"
        include_category = self.bool_cfg(prune_cfg, "include_category_in_key", True)
        match_on_hash = self.bool_cfg(prune_cfg, "match_on_hash", True)
        match_on_name_size = self.bool_cfg(prune_cfg, "match_on_name_size", True)
        if not match_on_hash and not match_on_name_size:
            match_on_name_size = True

        raw_categories = [
            str(x).strip() for x in self.coerce_list(prune_cfg.get("categories")) if str(x).strip()
        ]
        if not raw_categories:
            raw_categories = [
                str(v).strip()
                for v in ((qbit_cfg or {}).get("categories") or {}).values()
                if str(v).strip()
            ]
        categories = sorted(set(raw_categories))

        opener = self.qbit_login(qbit_url, qb_username, qb_password)
        torrents = self.qbit_list_completed_torrents(opener, qbit_url)
        now = int(time.time())
        groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)

        for item in torrents:
            if not isinstance(item, dict):
                continue
            thash = str(item.get("hash") or "").strip()
            if not thash:
                continue
            category = str(item.get("category") or "").strip()
            if categories and category not in categories:
                continue

            completion_on = self.to_int(item.get("completion_on"), 0) or 0
            added_on = self.to_int(item.get("added_on"), 0) or 0
            reference_on = completion_on if completion_on > 0 else added_on
            age_hours = 0.0
            if reference_on > 0:
                age_hours = max(0.0, float(now - reference_on) / 3600.0)
            if age_hours < min_completion_age_hours:
                continue

            normalized_name = self.normalize_token(item.get("name") or "")
            size_bytes = self.to_int(item.get("size"), 0) or 0
            record = {
                "hash": thash,
                "name": str(item.get("name") or "").strip(),
                "normalized_name": normalized_name,
                "size": size_bytes,
                "category": category,
                "completion_on": completion_on,
                "added_on": added_on,
            }

            if match_on_hash:
                groups[("hash", thash)].append(record)
            if match_on_name_size and normalized_name and size_bytes > 0:
                if include_category:
                    groups[("name_size", category.lower(), normalized_name, size_bytes)].append(
                        record
                    )
                else:
                    groups[("name_size", normalized_name, size_bytes)].append(record)

        delete_hashes: list[str] = []
        delete_seen: set[str] = set()
        duplicate_groups = 0
        for group_items in groups.values():
            if len(group_items) <= 1:
                continue
            duplicate_groups += 1
            sorted_items = sorted(
                group_items,
                key=lambda x: (
                    x.get("completion_on") or x.get("added_on") or 0,
                    x.get("hash") or "",
                ),
                reverse=(keep_strategy == "newest"),
            )
            keep_hash = str(sorted_items[0].get("hash") or "").strip()
            for candidate in sorted_items[1:]:
                candidate_hash = str(candidate.get("hash") or "").strip()
                if not candidate_hash or candidate_hash == keep_hash or candidate_hash in delete_seen:
                    continue
                delete_hashes.append(candidate_hash)
                delete_seen.add(candidate_hash)
                if len(delete_hashes) >= max_delete_per_run:
                    break
            if len(delete_hashes) >= max_delete_per_run:
                break

        summary["groups"] = duplicate_groups
        summary["candidates"] = len(delete_hashes)
        if not delete_hashes:
            self.log(
                "[OK] Media hygiene qB duplicate prune: no duplicate completed torrents found "
                f"(groups={duplicate_groups}, categories={categories or 'all'})."
            )
            return summary

        if dry_run:
            for thash in delete_hashes:
                self.log(f"[INFO] Media hygiene qB duplicate prune candidate (dry-run): {thash}")
            self.log(
                "[OK] Media hygiene qB duplicate prune: dry-run complete "
                f"(groups={duplicate_groups}, candidates={len(delete_hashes)})."
            )
            return summary

        self.qbit_delete_torrents(opener, qbit_url, delete_hashes, delete_files=delete_files)
        summary["deleted"] = len(delete_hashes)
        self.log(
            "[OK] Media hygiene qB duplicate prune: removed duplicate torrents "
            f"(deleted={len(delete_hashes)}, groups={duplicate_groups}, delete_files={delete_files})."
        )
        return summary

    def run_qbit_ipfilter_refresh(
        self,
        hygiene_cfg: dict[str, Any],
        qbit_cfg: dict[str, Any],
        qb_username: str,
        qb_password: str,
    ) -> dict[str, Any]:
        ipf_cfg = hygiene_cfg.get("qbit_ipfilter") or {}
        enabled = self.bool_cfg(ipf_cfg, "enabled", False)
        summary: dict[str, Any] = {
            "enabled": enabled,
            "downloaded": False,
            "applied": False,
            "skipped_reason": "",
            "source_url": "",
            "target_path": "",
            "bytes": 0,
        }
        if not enabled:
            return summary

        if not str(qb_username or "").strip() or not str(qb_password or "").strip():
            raise RuntimeError(
                "qB IP filter refresh requires qB credentials (QBITTORRENT_USERNAME/QBITTORRENT_PASSWORD)."
            )

        qbit_url = self.normalize_url((qbit_cfg or {}).get("url", "http://qbittorrent:8080"))
        required = self.bool_cfg(ipf_cfg, "required", False)
        apply_existing_on_failure = self.bool_cfg(ipf_cfg, "apply_existing_on_download_failure", True)
        source_url = str(
            ipf_cfg.get("url")
            or ipf_cfg.get("source_url")
            or "https://github.com/DavidMoore/ipfilter/releases/download/lists/ipfilter.dat"
        ).strip()
        fallback_urls = [
            str(x).strip() for x in self.coerce_list(ipf_cfg.get("fallback_urls")) if str(x).strip()
        ]
        urls: list[str] = []
        if source_url:
            urls.append(source_url)
        for item in fallback_urls:
            if item not in urls:
                urls.append(item)

        target_path = str(ipf_cfg.get("target_path") or "/srv-stack/data/torrents/ipfilter.dat").strip()
        qbit_filter_path = str(ipf_cfg.get("qbit_filter_path") or "/data/torrents/ipfilter.dat").strip()
        mirror_target_paths = [
            str(x).strip()
            for x in self.coerce_list(
                ipf_cfg.get("mirror_target_paths") or ["/srv-host-stack/data/torrents/ipfilter.dat"]
            )
            if str(x).strip()
        ]
        target_candidates = [target_path]
        for mirror in mirror_target_paths:
            if mirror not in target_candidates:
                target_candidates.append(mirror)
        state_path = str(
            ipf_cfg.get("state_path") or "/srv-stack/data/torrents/.ipfilter-refresh-state.json"
        ).strip()
        timeout_seconds = self.to_int(ipf_cfg.get("download_timeout_seconds"), 30) or 30
        min_valid_bytes = self.to_int(ipf_cfg.get("min_valid_bytes"), 1024) or 1024
        min_refresh_interval_hours = self.to_float(ipf_cfg.get("min_refresh_interval_hours"), 24.0) or 24.0

        target = Path(target_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        mirror_paths = [Path(p) for p in target_candidates[1:]]
        for mirror in mirror_paths:
            try:
                mirror.parent.mkdir(parents=True, exist_ok=True)
            except Exception:
                continue
        state_file = Path(state_path)
        state_file.parent.mkdir(parents=True, exist_ok=True)
        summary["target_path"] = str(target)

        state: dict[str, Any] = {}
        if state_file.exists():
            try:
                state = json.loads(state_file.read_text(encoding="utf-8"))
                if not isinstance(state, dict):
                    state = {}
            except Exception:
                state = {}

        now_epoch = int(time.time())
        min_refresh_seconds = max(0, int(min_refresh_interval_hours * 3600))
        last_success = self.to_int(state.get("last_success_epoch"), 0) or 0
        downloaded = False

        if (
            min_refresh_seconds > 0
            and last_success > 0
            and (now_epoch - last_success) < min_refresh_seconds
        ):
            summary["skipped_reason"] = "min_refresh_interval"
            summary["source_url"] = str(state.get("source_url") or source_url)
            summary["bytes"] = self.to_int(state.get("bytes"), 0) or 0
            self.log(
                "[INFO] qB IP filter: skipping download due to min refresh interval "
                f"(last_success={last_success}, min_hours={min_refresh_interval_hours})."
            )
        else:
            errors: list[str] = []
            data = b""
            selected_url = ""
            for candidate in urls:
                selected_url = candidate
                try:
                    req = request.Request(
                        candidate,
                        method="GET",
                        headers={"User-Agent": "media-stack-ipfilter/1.0"},
                    )
                    with request.urlopen(req, timeout=timeout_seconds) as resp:
                        data = resp.read()
                    if len(data) < min_valid_bytes:
                        raise RuntimeError(
                            f"Downloaded file too small ({len(data)} bytes, expected >= {min_valid_bytes})."
                        )
                    tmp_path = target.with_name(f"{target.name}.tmp")
                    tmp_path.write_bytes(data)
                    os.replace(tmp_path, target)
                    for mirror in mirror_paths:
                        try:
                            mirror_tmp = mirror.with_name(f"{mirror.name}.tmp")
                            mirror_tmp.write_bytes(data)
                            os.replace(mirror_tmp, mirror)
                        except Exception as mirror_exc:
                            self.log(
                                f"[WARN] qB IP filter: mirror write failed for {mirror} "
                                f"({mirror_exc})"
                            )
                    downloaded = True
                    summary["downloaded"] = True
                    summary["source_url"] = selected_url
                    summary["bytes"] = len(data)
                    break
                except Exception as exc:
                    errors.append(f"{candidate}: {exc}")
                    continue

            if not downloaded:
                cached_target: Path | None = None
                for candidate in [target] + mirror_paths:
                    if candidate.exists():
                        cached_target = candidate
                        break
                if cached_target is not None and apply_existing_on_failure:
                    summary["skipped_reason"] = "source_unavailable_using_cached_filter"
                    summary["source_url"] = str(state.get("source_url") or source_url)
                    summary["bytes"] = cached_target.stat().st_size
                    summary["target_path"] = str(cached_target)
                    self.log(
                        "[WARN] qB IP filter: download source unavailable; using cached filter file "
                        f"at {cached_target}."
                    )
                    if errors:
                        self.log(f"[WARN] qB IP filter: download errors: {' | '.join(errors)}")
                else:
                    message = (
                        "qB IP filter: unable to download filter and no usable cached copy exists "
                        f"(targets={target_candidates}, urls={urls}, errors={errors})."
                    )
                    if required:
                        raise RuntimeError(message)
                    self.log(f"[WARN] {message}")
                    return summary

        opener = self.qbit_login(qbit_url, qb_username, qb_password)
        self.qbit_set_preferences(
            opener,
            qbit_url,
            {
                "ip_filter_enabled": True,
                "ip_filter_path": qbit_filter_path,
            },
        )
        summary["applied"] = True
        self.log(
            "[OK] qB IP filter: preferences applied "
            f"(enabled=True, path={qbit_filter_path}, downloaded={summary['downloaded']})."
        )

        if downloaded:
            now = datetime.now(timezone.utc)
            state.update(
                {
                    "last_success_epoch": int(now.timestamp()),
                    "last_success_iso": now.isoformat(),
                    "source_url": summary["source_url"],
                    "bytes": summary["bytes"],
                    "target_path": str(target),
                    "qbit_filter_path": qbit_filter_path,
                }
            )
            try:
                state_file.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
            except Exception as exc:
                self.log(f"[WARN] qB IP filter: failed writing state file {state_file} ({exc})")
        return summary
