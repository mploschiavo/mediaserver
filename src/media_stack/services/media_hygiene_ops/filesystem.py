"""FilesystemHygieneService cleanup helpers for media hygiene operations."""

from __future__ import annotations


from media_stack.core.logging_utils import log_swallowed
import os
import time
from collections import defaultdict
from pathlib import Path
from typing import Any
import logging



class FilesystemHygieneService:
    def walk_existing_files(self, paths: list[Path]):
        for root in paths:
            if not root.exists():
                continue
            for dirpath, _, filenames in os.walk(root):
                base = Path(dirpath)
                for name in filenames:
                    yield base / name
    
    
    def run_filesystem_hygiene(self, ops, hygiene_cfg: dict[str, Any]) -> dict[str, int]:
        fs_cfg = hygiene_cfg.get("filesystem") or {}
        if not ops.bool_cfg(fs_cfg, "enabled", True):
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
        raw_roots = ops.coerce_list(fs_cfg.get("roots")) or default_roots
        roots = [Path(str(p)).resolve() for p in raw_roots if str(p).strip()]
        min_age_hours = ops.to_float(fs_cfg.get("min_file_age_hours"), 24.0)
        if min_age_hours is None:
            min_age_hours = 24.0
        now_ts = time.time()
    
        remove_zero = ops.bool_cfg(fs_cfg, "remove_zero_byte_files", True)
        temp_extensions = {
            str(x).strip().lower()
            for x in ops.coerce_list(fs_cfg.get("temp_extensions"))
            if str(x).strip()
        } or {".part", ".tmp", ".temp", ".nzb", ".!qb"}
        remove_empty_dirs = ops.bool_cfg(fs_cfg, "remove_empty_dirs", True)
        default_preserve_empty_dirs = [
            "/srv-stack/data/torrents/incomplete",
            "/srv-stack/data/torrents/completed/tv",
            "/srv-stack/data/torrents/completed/movies",
            "/srv-stack/data/torrents/completed/music",
            "/srv-stack/data/torrents/completed/books",
            "/srv-stack/data/usenet/incomplete",
            "/srv-stack/data/usenet/completed/tv",
            "/srv-stack/data/usenet/completed/movies",
            "/srv-stack/data/usenet/completed/music",
            "/srv-stack/data/usenet/completed/books",
        ]
        raw_preserve_empty_dirs = (
            ops.coerce_list(fs_cfg.get("preserve_empty_dirs")) or default_preserve_empty_dirs
        )
        preserve_empty_dirs = {
            Path(str(path)).resolve() for path in raw_preserve_empty_dirs if str(path).strip()
        }
    
        dedupe_cfg = fs_cfg.get("dedupe") or {}
        dedupe_enabled = ops.bool_cfg(dedupe_cfg, "enabled", True)
        dedupe_dry_run = ops.bool_cfg(dedupe_cfg, "dry_run", False)
        dedupe_max_deletes = ops.to_int(dedupe_cfg.get("max_delete_per_run"), 20) or 20
        dedupe_min_size = ops.to_int(dedupe_cfg.get("min_size_bytes"), 100 * 1024 * 1024) or (
            100 * 1024 * 1024
        )
    
        removed_temp = 0
        removed_zero = 0
        removed_dupes = 0
        removed_empty = 0
        dedupe_map: dict[tuple[str, int], list[tuple[Path, float]]] = defaultdict(list)
    
        for file_path in walk_existing_files(roots):
            try:
                st = file_path.stat()
            except FileNotFoundError:
                continue
            except Exception as exc:
                log_swallowed(exc)
                continue
    
            age_hours = max(0.0, (now_ts - float(st.st_mtime)) / 3600.0)
            suffix = file_path.suffix.lower()
            if age_hours >= min_age_hours:
                if remove_zero and int(st.st_size) <= 0:
                    try:
                        file_path.unlink()
                        removed_zero += 1
                    except Exception as exc:
                        log_swallowed(exc)
                    continue
                if suffix in temp_extensions:
                    try:
                        file_path.unlink()
                        removed_temp += 1
                    except Exception as exc:
                        log_swallowed(exc)
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
                        ops.log(f"[INFO] Media hygiene: dedupe candidate (dry-run): {dup_path}")
                        continue
                    try:
                        dup_path.unlink()
                        removed_dupes += 1
                        deletions_left -= 1
                        ops.log(f"[OK] Media hygiene: removed duplicate file {dup_path}")
                    except Exception as exc:
                        log_swallowed(exc)
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
                    if p.resolve() in preserve_empty_dirs:
                        continue
                    try:
                        p.rmdir()
                        removed_empty += 1
                    except Exception as exc:
                        log_swallowed(exc)
                        continue
    
        summary = {
            "removed_temp": removed_temp,
            "removed_zero": removed_zero,
            "removed_dupes": removed_dupes,
            "removed_empty_dirs": removed_empty,
        }
        ops.log(
            "[OK] Media hygiene filesystem cleanup: "
            f"temp={removed_temp}, zero_byte={removed_zero}, duplicates={removed_dupes}, empty_dirs={removed_empty}"
        )
        return summary


_instance = FilesystemHygieneService()
walk_existing_files = _instance.walk_existing_files
run_filesystem_hygiene = _instance.run_filesystem_hygiene
