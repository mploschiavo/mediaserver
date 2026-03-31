"""Jellyfin prewarm bootstrap service."""

from __future__ import annotations

import os
import posixpath
import time
from pathlib import Path
from zipfile import ZipFile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Callable

LogFn = Callable[[str], None]
BoolCfgFn = Callable[[dict[str, Any], str, bool], bool]
NormalizeUrlFn = Callable[[str], str]
WaitForServiceFn = Callable[[str, str, str, int], None]
ResolveApiKeyFn = Callable[[dict[str, Any], str], str]
JellyfinRequestFn = Callable[..., tuple[int, Any, str]]
BuildQueryPathFn = Callable[[str, dict[str, Any]], str]
TriggerLiveTvRefreshFn = Callable[[str, str, str, str], tuple[bool, str]]


@dataclass
class JellyfinPrewarmDependencies:
    log: LogFn
    bool_cfg: BoolCfgFn
    normalize_url: NormalizeUrlFn
    wait_for_service: WaitForServiceFn
    resolve_api_key: ResolveApiKeyFn
    jellyfin_request: JellyfinRequestFn
    build_query_path: BuildQueryPathFn
    trigger_livetv_refresh: TriggerLiveTvRefreshFn


@dataclass
class JellyfinPrewarmService:
    deps: JellyfinPrewarmDependencies

    @staticmethod
    def _normalize_text_list(values: Any, fallback: list[str] | None = None) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        source: list[Any]
        if isinstance(values, list):
            source = values
        elif values in (None, ""):
            source = list(fallback or [])
        else:
            source = [values]
        for raw in source:
            token = str(raw or "").strip()
            if not token:
                continue
            lowered = token.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            out.append(token)
        return out

    @staticmethod
    def _candidate_image_paths(
        directory: Path,
        preferred_names: list[str],
        allowed_extensions: set[str],
    ) -> list[Path]:
        if not directory.exists() or not directory.is_dir():
            return []
        by_name: dict[str, Path] = {}
        fallback: list[Path] = []
        for candidate in sorted(directory.iterdir()):
            if not candidate.is_file():
                continue
            suffix = candidate.suffix.lower()
            if suffix not in allowed_extensions:
                continue
            lowered_name = candidate.name.lower()
            by_name.setdefault(lowered_name, candidate)
            fallback.append(candidate)

        ordered: list[Path] = []
        seen_paths: set[str] = set()
        for raw in preferred_names:
            preferred = str(raw or "").strip().lower()
            if not preferred:
                continue
            path = by_name.get(preferred)
            if not path:
                # Allow users to specify bare names without extension.
                for ext in sorted(allowed_extensions):
                    path = by_name.get(f"{preferred}{ext}")
                    if path:
                        break
            if not path:
                continue
            key = str(path)
            if key in seen_paths:
                continue
            seen_paths.add(key)
            ordered.append(path)

        for path in fallback:
            key = str(path)
            if key in seen_paths:
                continue
            seen_paths.add(key)
            ordered.append(path)
        return ordered

    @staticmethod
    def _extract_epub_cover_bytes(epub_path: Path) -> bytes | None:
        with ZipFile(epub_path) as archive:
            names = archive.namelist()
            lower_name_map = {name.lower(): name for name in names}

            def read_member(member_name: str) -> bytes | None:
                target = lower_name_map.get(member_name.lower())
                if not target:
                    return None
                return archive.read(target)

            cover_href: str | None = None
            container_raw = read_member("META-INF/container.xml")
            if container_raw:
                try:
                    root = ET.fromstring(container_raw)
                    rootfile = root.find(".//{*}rootfile")
                    opf_path = str((rootfile.attrib.get("full-path") if rootfile is not None else "") or "")
                    if opf_path:
                        opf_raw = read_member(opf_path)
                        if opf_raw:
                            opf_root = ET.fromstring(opf_raw)
                            manifest = {
                                str(item.attrib.get("id") or "").strip(): str(
                                    item.attrib.get("href") or ""
                                ).strip()
                                for item in opf_root.findall(".//{*}manifest/{*}item")
                            }
                            for meta in opf_root.findall(".//{*}metadata/{*}meta"):
                                if str(meta.attrib.get("name") or "").strip().lower() == "cover":
                                    cover_id = str(meta.attrib.get("content") or "").strip()
                                    if cover_id and cover_id in manifest:
                                        opf_dir = posixpath.dirname(opf_path)
                                        cover_href = posixpath.normpath(
                                            posixpath.join(opf_dir, manifest[cover_id])
                                        )
                                        break
                except Exception:
                    cover_href = None

            if cover_href:
                cover_bytes = read_member(cover_href)
                if cover_bytes:
                    return cover_bytes

            for name in names:
                name_lower = name.lower()
                if not name_lower.endswith((".jpg", ".jpeg", ".png", ".webp")):
                    continue
                if "cover" in name_lower:
                    return archive.read(name)

            for name in names:
                if name.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                    return archive.read(name)
        return None

    @staticmethod
    def _iter_sidecar_books_root_candidates(sidecar_cfg: dict[str, Any]) -> list[Path]:
        raw_candidates: list[str] = []
        configured_list = sidecar_cfg.get("books_root_paths")
        if isinstance(configured_list, list):
            raw_candidates.extend(str(item or "").strip() for item in configured_list)
        elif configured_list is not None:
            raw_candidates.append(str(configured_list).strip())

        raw_candidates.append(
            str(sidecar_cfg.get("books_root_path") or "/srv-stack/media/books").strip()
        )
        raw_candidates.extend(
            [
                "/media/books",
            ]
        )

        deduped: list[Path] = []
        seen: set[str] = set()
        for raw in raw_candidates:
            text = str(raw or "").strip()
            if not text:
                continue
            if text in seen:
                continue
            seen.add(text)
            deduped.append(Path(text))
        return deduped

    @staticmethod
    def _iter_sidecar_music_root_candidates(sidecar_cfg: dict[str, Any]) -> list[Path]:
        raw_candidates: list[str] = []
        configured_list = sidecar_cfg.get("music_root_paths")
        if isinstance(configured_list, list):
            raw_candidates.extend(str(item or "").strip() for item in configured_list)
        elif configured_list is not None:
            raw_candidates.append(str(configured_list).strip())

        raw_candidates.append(
            str(sidecar_cfg.get("music_root_path") or "/srv-stack/media/music").strip()
        )
        raw_candidates.extend(
            [
                "/media/music",
            ]
        )

        deduped: list[Path] = []
        seen: set[str] = set()
        for raw in raw_candidates:
            text = str(raw or "").strip()
            if not text:
                continue
            if text in seen:
                continue
            seen.add(text)
            deduped.append(Path(text))
        return deduped

    @staticmethod
    def _path_has_epub(path: Path) -> bool:
        try:
            for _ in path.rglob("*.epub"):
                return True
        except Exception:
            return False
        return False

    @staticmethod
    def _path_has_audio(path: Path, audio_extensions: set[str]) -> bool:
        try:
            for candidate in path.rglob("*"):
                if not candidate.is_file():
                    continue
                if candidate.suffix.lower() in audio_extensions:
                    return True
        except Exception:
            return False
        return False

    def _resolve_books_root_path(self, sidecar_cfg: dict[str, Any]) -> tuple[Path | None, list[Path]]:
        candidates = self._iter_sidecar_books_root_candidates(sidecar_cfg)
        existing = [path for path in candidates if path.exists() and path.is_dir()]
        if not existing:
            return None, candidates
        with_epub = [path for path in existing if self._path_has_epub(path)]
        if with_epub:
            return with_epub[0], candidates
        return existing[0], candidates

    def _resolve_music_root_path(self, sidecar_cfg: dict[str, Any]) -> tuple[Path | None, list[Path]]:
        candidates = self._iter_sidecar_music_root_candidates(sidecar_cfg)
        existing = [path for path in candidates if path.exists() and path.is_dir()]
        if not existing:
            return None, candidates
        audio_extensions = {
            ext.lower()
            for ext in self._normalize_text_list(
                sidecar_cfg.get("audio_extensions"),
                [".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".alac"],
            )
        }
        with_audio = [path for path in existing if self._path_has_audio(path, audio_extensions)]
        if with_audio:
            return with_audio[0], candidates
        return existing[0], candidates

    def _ensure_book_sidecar_artwork(self, prewarm_cfg: dict[str, Any]) -> None:
        d = self.deps
        sidecar_cfg = prewarm_cfg.get("book_sidecar_artwork")
        if not isinstance(sidecar_cfg, dict):
            sidecar_cfg = {}
        if not d.bool_cfg(sidecar_cfg, "enabled", True):
            return

        books_root, candidates = self._resolve_books_root_path(sidecar_cfg)
        if books_root is None:
            candidate_label = ", ".join(str(path) for path in candidates)
            d.log(
                "[WARN] Jellyfin prewarm: books_root_path missing for sidecar artwork "
                f"(checked: {candidate_label})"
            )
            return
        preferred_root = str(sidecar_cfg.get("books_root_path") or "/srv-stack/media/books").strip()
        if preferred_root and str(books_root) != preferred_root:
            d.log(
                "[INFO] Jellyfin prewarm: using fallback books root for sidecar artwork "
                f"({books_root})"
            )

        output_name = str(sidecar_cfg.get("output_filename") or "folder.jpg").strip() or "folder.jpg"
        per_book_output_enabled = d.bool_cfg(sidecar_cfg, "write_per_book_sidecars", True)
        per_book_output_extension = (
            str(sidecar_cfg.get("per_book_output_extension") or ".jpg").strip() or ".jpg"
        )
        if not per_book_output_extension.startswith("."):
            per_book_output_extension = "." + per_book_output_extension
        preferred_files = self._normalize_text_list(
            sidecar_cfg.get("preferred_filenames"),
            [
                "folder.jpg",
                "cover.jpg",
                "cover.jpeg",
                "cover.png",
                "front.jpg",
                "front.jpeg",
                "front.png",
            ],
        )
        allowed_extensions = {
            ext.lower()
            for ext in self._normalize_text_list(
                sidecar_cfg.get("image_extensions"),
                [".jpg", ".jpeg", ".png", ".webp"],
            )
        }
        replace_existing = d.bool_cfg(sidecar_cfg, "replace_existing", False)
        try:
            max_books = int(sidecar_cfg.get("max_books_per_run") or 500)
        except Exception:
            max_books = 500

        scanned = 0
        written = 0
        skipped = 0
        failed = 0
        epub_paths = sorted(books_root.rglob("*.epub"))
        for epub_path in epub_paths:
            if scanned >= max_books:
                break
            scanned += 1
            folder_output_path = epub_path.parent / output_name
            per_book_output_path = (
                epub_path.with_suffix(per_book_output_extension) if per_book_output_enabled else None
            )
            pending_targets: list[Path] = []
            if replace_existing or not folder_output_path.exists():
                pending_targets.append(folder_output_path)
            if per_book_output_path is not None and (replace_existing or not per_book_output_path.exists()):
                pending_targets.append(per_book_output_path)

            if not pending_targets:
                skipped += 1
                continue
            try:
                cover = self._extract_epub_cover_bytes(epub_path)
                if not cover:
                    # Fallback: use existing cover-like image files in the same book directory.
                    fallback_candidates = self._candidate_image_paths(
                        epub_path.parent,
                        preferred_files,
                        allowed_extensions,
                    )
                    fallback_path = next(
                        (
                            path
                            for path in fallback_candidates
                            if path.name.lower() != output_name.lower()
                            and (
                                per_book_output_path is None
                                or path.resolve() != per_book_output_path.resolve()
                            )
                        ),
                        None,
                    )
                    if fallback_path is not None:
                        cover = fallback_path.read_bytes()
                    else:
                        skipped += 1
                        continue

                for target in pending_targets:
                    target.write_bytes(cover)
                    written += 1
            except Exception as exc:
                failed += 1
                d.log(
                    f"[WARN] Jellyfin prewarm: failed sidecar artwork for {epub_path} ({exc})"
                )
                continue

        d.log(
            "[OK] Jellyfin prewarm: book sidecar artwork reconcile complete "
            f"(scanned={scanned}, written={written}, skipped={skipped}, failed={failed})"
        )

    def _ensure_music_sidecar_artwork(self, prewarm_cfg: dict[str, Any]) -> None:
        d = self.deps
        sidecar_cfg = prewarm_cfg.get("music_sidecar_artwork")
        if not isinstance(sidecar_cfg, dict):
            sidecar_cfg = {}
        if not d.bool_cfg(sidecar_cfg, "enabled", True):
            return

        music_root, candidates = self._resolve_music_root_path(sidecar_cfg)
        if music_root is None:
            candidate_label = ", ".join(str(path) for path in candidates)
            d.log(
                "[WARN] Jellyfin prewarm: music_root_path missing for sidecar artwork "
                f"(checked: {candidate_label})"
            )
            return
        preferred_root = str(sidecar_cfg.get("music_root_path") or "/srv-stack/media/music").strip()
        if preferred_root and str(music_root) != preferred_root:
            d.log(
                "[INFO] Jellyfin prewarm: using fallback music root for sidecar artwork "
                f"({music_root})"
            )

        output_name = str(sidecar_cfg.get("output_filename") or "folder.jpg").strip() or "folder.jpg"
        replace_existing = d.bool_cfg(sidecar_cfg, "replace_existing", False)
        try:
            max_albums = int(sidecar_cfg.get("max_albums_per_run") or 1000)
        except Exception:
            max_albums = 1000

        preferred_files = self._normalize_text_list(
            sidecar_cfg.get("preferred_filenames"),
            [
                "folder.jpg",
                "cover.jpg",
                "cover.jpeg",
                "cover.png",
                "front.jpg",
                "front.jpeg",
                "front.png",
                "album.jpg",
                "album.jpeg",
                "album.png",
            ],
        )
        allowed_extensions = {
            ext.lower()
            for ext in self._normalize_text_list(
                sidecar_cfg.get("image_extensions"),
                [".jpg", ".jpeg", ".png", ".webp"],
            )
        }
        audio_extensions = {
            ext.lower()
            for ext in self._normalize_text_list(
                sidecar_cfg.get("audio_extensions"),
                [".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".alac"],
            )
        }

        scanned = 0
        written = 0
        skipped = 0
        failed = 0

        for root, _dirs, files in os.walk(music_root):
            if scanned >= max_albums:
                break
            if not files:
                continue
            has_audio = any(
                Path(name).suffix.lower() in audio_extensions for name in files if isinstance(name, str)
            )
            if not has_audio:
                continue
            scanned += 1
            album_dir = Path(root)
            output_path = album_dir / output_name
            if output_path.exists() and not replace_existing:
                skipped += 1
                continue
            try:
                candidates = self._candidate_image_paths(album_dir, preferred_files, allowed_extensions)
                source = next(
                    (path for path in candidates if path.name.lower() != output_name.lower()),
                    None,
                )
                if source is None:
                    skipped += 1
                    continue
                output_path.write_bytes(source.read_bytes())
                written += 1
            except Exception as exc:
                failed += 1
                d.log(
                    f"[WARN] Jellyfin prewarm: failed music sidecar artwork for {album_dir} ({exc})"
                )

        d.log(
            "[OK] Jellyfin prewarm: music sidecar artwork reconcile complete "
            f"(scanned={scanned}, written={written}, skipped={skipped}, failed={failed})"
        )

    @staticmethod
    def _item_has_artwork(item: dict[str, Any]) -> bool:
        image_tags = item.get("ImageTags")
        if isinstance(image_tags, dict):
            if any(str(value or "").strip() for value in image_tags.values()):
                return True
        if str(item.get("PrimaryImageTag") or "").strip():
            return True
        if str(item.get("AlbumPrimaryImageTag") or "").strip():
            return True
        if str(item.get("PrimaryImageItemId") or "").strip():
            return True
        backdrop_tags = item.get("BackdropImageTags")
        if isinstance(backdrop_tags, list) and backdrop_tags:
            return True
        return False

    @staticmethod
    def _item_has_overview(item: dict[str, Any]) -> bool:
        return bool(str(item.get("Overview") or "").strip())

    def _run_metadata_backfill(
        self,
        prewarm_cfg: dict[str, Any],
        jellyfin_url: str,
        jellyfin_api_key: str,
    ) -> None:
        d = self.deps
        backfill_cfg = prewarm_cfg.get("metadata_backfill")
        if not isinstance(backfill_cfg, dict):
            backfill_cfg = {}
        if not d.bool_cfg(backfill_cfg, "enabled", True):
            return

        libraries_filter = {
            token.lower()
            for token in self._normalize_text_list(
                backfill_cfg.get("libraries"),
                ["Movies", "TV Shows", "Music", "Books"],
            )
        }
        refresh_missing_primary = d.bool_cfg(backfill_cfg, "refresh_missing_primary_image", True)
        refresh_missing_overview = d.bool_cfg(backfill_cfg, "refresh_missing_overview", True)
        required = d.bool_cfg(backfill_cfg, "required", False)
        try:
            max_refresh_per_library = int(backfill_cfg.get("max_refresh_per_library") or 80)
        except Exception:
            max_refresh_per_library = 80
        try:
            sample_multiplier = int(backfill_cfg.get("sample_multiplier") or 4)
        except Exception:
            sample_multiplier = 4
        sample_limit = max(1, max_refresh_per_library * max(1, sample_multiplier))
        refresh_params = backfill_cfg.get("refresh_query")
        if not isinstance(refresh_params, dict):
            refresh_params = {
                "metadataRefreshMode": "FullRefresh",
                "imageRefreshMode": "FullRefresh",
                "replaceAllMetadata": "true",
                "replaceAllImages": "true",
            }

        status, libraries_payload, body = d.jellyfin_request(
            jellyfin_url,
            "/Library/VirtualFolders",
            jellyfin_api_key,
        )
        if status != 200 or not isinstance(libraries_payload, list):
            message = (
                "Jellyfin prewarm: metadata backfill could not list libraries "
                f"(HTTP {status}): {body}"
            )
            if required:
                raise RuntimeError(message)
            d.log(f"[WARN] {message}")
            return

        type_map = {
            "movies": ["Movie"],
            "tvshows": ["Series", "Episode"],
            "tv": ["Series", "Episode"],
            "books": ["Book"],
            "music": ["MusicAlbum", "MusicArtist", "Audio"],
        }

        total_candidates = 0
        total_requested = 0
        total_failed = 0

        for library in libraries_payload:
            if not isinstance(library, dict):
                continue
            library_name = str(library.get("Name") or "").strip()
            library_id = str(library.get("ItemId") or "").strip()
            collection_type = str(library.get("CollectionType") or "").strip().lower()
            if not library_id:
                continue
            name_key = library_name.lower()
            if libraries_filter and collection_type not in libraries_filter and name_key not in libraries_filter:
                continue

            include_types = type_map.get(collection_type) or []
            list_path = d.build_query_path(
                "/Items",
                {
                    "ParentId": library_id,
                    "Recursive": "true",
                    "IncludeItemTypes": ",".join(include_types) if include_types else None,
                    "Fields": "ImageTags,PrimaryImageTag,PrimaryImageItemId,AlbumPrimaryImageTag,BackdropImageTags,Overview",
                    "Limit": str(sample_limit),
                    "SortBy": "DateCreated",
                    "SortOrder": "Descending",
                },
            )
            status, payload, body = d.jellyfin_request(jellyfin_url, list_path, jellyfin_api_key)
            if status != 200:
                message = (
                    f"Jellyfin prewarm: metadata backfill query failed for {library_name or collection_type} "
                    f"(HTTP {status}): {body}"
                )
                if required:
                    raise RuntimeError(message)
                d.log(f"[WARN] {message}")
                continue

            if isinstance(payload, dict):
                items = payload.get("Items")
                rows = items if isinstance(items, list) else []
            elif isinstance(payload, list):
                rows = payload
            else:
                rows = []

            targets: list[str] = []
            for item in rows:
                if not isinstance(item, dict):
                    continue
                item_id = str(item.get("Id") or "").strip()
                if not item_id:
                    continue
                needs_primary = refresh_missing_primary and (not self._item_has_artwork(item))
                needs_overview = refresh_missing_overview and (not self._item_has_overview(item))
                if not (needs_primary or needs_overview):
                    continue
                targets.append(item_id)
                if len(targets) >= max_refresh_per_library:
                    break

            if not targets:
                d.log(
                    "[OK] Jellyfin prewarm: metadata backfill found no missing items "
                    f"for {library_name}"
                )
                continue

            library_requested = 0
            library_failed = 0
            for item_id in targets:
                refresh_path = d.build_query_path(f"/Items/{item_id}/Refresh", refresh_params)
                status, _, body = d.jellyfin_request(
                    jellyfin_url,
                    refresh_path,
                    jellyfin_api_key,
                    method="POST",
                )
                if status in (200, 201, 202, 204):
                    library_requested += 1
                else:
                    library_failed += 1
                    d.log(
                        "[WARN] Jellyfin prewarm: metadata backfill refresh failed "
                        f"for {library_name} item={item_id} (HTTP {status}): {body}"
                    )

            total_candidates += len(targets)
            total_requested += library_requested
            total_failed += library_failed
            d.log(
                "[OK] Jellyfin prewarm: metadata backfill refresh requested "
                f"for {library_name} (targets={len(targets)}, requested={library_requested}, failed={library_failed})"
            )

        if total_failed and required:
            raise RuntimeError(
                "Jellyfin prewarm: metadata backfill had refresh failures "
                f"(requested={total_requested}, failed={total_failed})"
            )
        d.log(
            "[OK] Jellyfin prewarm: metadata backfill complete "
            f"(candidates={total_candidates}, requested={total_requested}, failed={total_failed})"
        )

    def _run_artwork_health_check(
        self,
        prewarm_cfg: dict[str, Any],
        jellyfin_url: str,
        jellyfin_api_key: str,
    ) -> None:
        d = self.deps
        health_cfg = prewarm_cfg.get("artwork_health_check")
        if not isinstance(health_cfg, dict):
            health_cfg = {}
        if not d.bool_cfg(health_cfg, "enabled", True):
            return

        libraries_filter = {
            token.lower()
            for token in self._normalize_text_list(
                health_cfg.get("libraries"),
                ["Movies", "TV Shows", "Music", "Books", "Live TV"],
            )
        }
        try:
            max_items = int(health_cfg.get("max_items_per_library") or 400)
        except Exception:
            max_items = 400
        try:
            warn_below = float(health_cfg.get("warn_below_coverage_percent") or 70.0)
        except Exception:
            warn_below = 70.0
        try:
            fail_below = float(health_cfg.get("fail_below_coverage_percent") or 30.0)
        except Exception:
            fail_below = 30.0
        required = d.bool_cfg(health_cfg, "required", False)

        status, libraries_payload, body = d.jellyfin_request(
            jellyfin_url,
            "/Library/VirtualFolders",
            jellyfin_api_key,
        )
        if status != 200 or not isinstance(libraries_payload, list):
            message = (
                "Jellyfin prewarm: artwork health check could not list libraries "
                f"(HTTP {status}): {body}"
            )
            if required:
                raise RuntimeError(message)
            d.log(f"[WARN] {message}")
            return

        type_map = {
            "movies": ["Movie"],
            "tvshows": ["Series", "Episode"],
            "tv": ["Series", "Episode"],
            "books": ["Book"],
            "music": ["MusicAlbum", "MusicArtist", "Audio"],
        }

        for library in libraries_payload:
            if not isinstance(library, dict):
                continue
            library_name = str(library.get("Name") or "").strip()
            library_id = str(library.get("ItemId") or "").strip()
            collection_type = str(library.get("CollectionType") or "").strip().lower()
            if not library_id:
                continue
            name_key = library_name.lower()
            if libraries_filter and collection_type not in libraries_filter and name_key not in libraries_filter:
                continue

            include_types = type_map.get(collection_type) or []
            path = d.build_query_path(
                "/Items",
                {
                    "ParentId": library_id,
                    "Recursive": "true",
                    "IncludeItemTypes": ",".join(include_types) if include_types else None,
                    "Fields": "ImageTags,PrimaryImageTag,PrimaryImageItemId,AlbumPrimaryImageTag,BackdropImageTags",
                    "Limit": str(max_items),
                    "SortBy": "DateCreated",
                    "SortOrder": "Descending",
                },
            )
            status, payload, body = d.jellyfin_request(jellyfin_url, path, jellyfin_api_key)
            if status != 200:
                message = (
                    f"Jellyfin prewarm: artwork health check query failed for {library_name or collection_type} "
                    f"(HTTP {status}): {body}"
                )
                if required:
                    raise RuntimeError(message)
                d.log(f"[WARN] {message}")
                continue

            if isinstance(payload, dict):
                items = payload.get("Items")
                rows = items if isinstance(items, list) else []
            elif isinstance(payload, list):
                rows = payload
            else:
                rows = []

            valid_items = [item for item in rows if isinstance(item, dict)]
            total = len(valid_items)
            if total == 0:
                d.log(
                    f"[INFO] Jellyfin prewarm: artwork health check skipped for {library_name} "
                    "(no sampled items)"
                )
                continue
            with_art = sum(1 for item in valid_items if self._item_has_artwork(item))
            coverage = (with_art / total) * 100.0
            summary = (
                f"Jellyfin prewarm: artwork coverage for {library_name} = "
                f"{coverage:.1f}% ({with_art}/{total})"
            )
            if coverage < fail_below and required:
                raise RuntimeError(f"{summary}; below fail threshold {fail_below:.1f}%")
            if coverage < warn_below:
                d.log(f"[WARN] {summary}; below warning threshold {warn_below:.1f}%")
            else:
                d.log(f"[OK] {summary}")

        # Live TV has no virtual folder entry; evaluate artwork on current/airing programs directly.
        if {"livetv", "live tv"} & libraries_filter:
            live_tv_path = d.build_query_path(
                "/LiveTv/Programs",
                {
                    "IsAiring": "true",
                    "Limit": str(max_items),
                    "Fields": "ImageTags,PrimaryImageTag,PrimaryImageItemId,BackdropImageTags",
                },
            )
            status, payload, body = d.jellyfin_request(jellyfin_url, live_tv_path, jellyfin_api_key)
            if status != 200:
                message = (
                    "Jellyfin prewarm: artwork health check query failed for Live TV "
                    f"(HTTP {status}): {body}"
                )
                if required:
                    raise RuntimeError(message)
                d.log(f"[WARN] {message}")
            else:
                if isinstance(payload, dict):
                    items = payload.get("Items")
                    rows = items if isinstance(items, list) else []
                elif isinstance(payload, list):
                    rows = payload
                else:
                    rows = []

                valid_items = [item for item in rows if isinstance(item, dict)]
                total = len(valid_items)
                if total == 0:
                    d.log(
                        "[INFO] Jellyfin prewarm: artwork health check skipped for Live TV "
                        "(no sampled items)"
                    )
                else:
                    with_art = sum(1 for item in valid_items if self._item_has_artwork(item))
                    coverage = (with_art / total) * 100.0
                    summary = (
                        "Jellyfin prewarm: artwork coverage for Live TV = "
                        f"{coverage:.1f}% ({with_art}/{total})"
                    )
                    if coverage < fail_below and required:
                        raise RuntimeError(f"{summary}; below fail threshold {fail_below:.1f}%")
                    if coverage < warn_below:
                        d.log(f"[WARN] {summary}; below warning threshold {warn_below:.1f}%")
                    else:
                        d.log(f"[OK] {summary}")

    def ensure(self, cfg: dict[str, Any], config_root: str, wait_timeout: int) -> None:
        d = self.deps
        prewarm_cfg = cfg.get("jellyfin_prewarm") or {}
        if not d.bool_cfg(prewarm_cfg, "enabled", False):
            return

        libraries_cfg = cfg.get("jellyfin_libraries") or {}
        livetv_cfg = cfg.get("jellyfin_livetv") or {}
        api_cfg = dict(libraries_cfg)
        if not isinstance(api_cfg, dict):
            api_cfg = {}
        for key in (
            "api_key_env",
            "auto_discover_api_key_from_db",
            "api_key_db_path",
            "api_key_name_preference",
        ):
            if key in prewarm_cfg:
                api_cfg[key] = prewarm_cfg.get(key)
        api_cfg["url"] = (
            prewarm_cfg.get("url")
            or libraries_cfg.get("url")
            or livetv_cfg.get("url")
            or "http://jellyfin:8096"
        )

        jellyfin_url = d.normalize_url(api_cfg.get("url"))
        d.wait_for_service("Jellyfin", jellyfin_url, "/System/Info/Public", wait_timeout)
        jellyfin_api_key = d.resolve_api_key(api_cfg, config_root)

        self._ensure_book_sidecar_artwork(prewarm_cfg)
        self._ensure_music_sidecar_artwork(prewarm_cfg)

        refresh_params = prewarm_cfg.get("library_refresh_query")
        if not isinstance(refresh_params, dict):
            refresh_params = {
                "metadataRefreshMode": "FullRefresh",
                "imageRefreshMode": "FullRefresh",
                "replaceAllMetadata": "false",
                "replaceAllImages": "false",
            }

        refresh_requested = False
        if d.bool_cfg(prewarm_cfg, "refresh_library", True):
            refresh_path = d.build_query_path("/Library/Refresh", refresh_params)
            status, _, body = d.jellyfin_request(
                jellyfin_url,
                refresh_path,
                jellyfin_api_key,
                method="POST",
            )
            if status in (200, 201, 202, 204):
                d.log("[OK] Jellyfin prewarm: requested library metadata/artwork refresh")
                refresh_requested = True
            else:
                raise RuntimeError(
                    f"Jellyfin prewarm: failed requesting library refresh (HTTP {status}): {body}"
                )

        if d.bool_cfg(prewarm_cfg, "refresh_channels", True):
            ok, detail = d.trigger_livetv_refresh(
                jellyfin_url,
                jellyfin_api_key,
                "/LiveTv/RefreshChannels",
                "Live TV channel refresh",
            )
            if ok:
                d.log(f"[OK] Jellyfin prewarm: {detail}")
            else:
                d.log(f"[WARN] Jellyfin prewarm: {detail}")

        if d.bool_cfg(prewarm_cfg, "refresh_guide", True):
            ok, detail = d.trigger_livetv_refresh(
                jellyfin_url,
                jellyfin_api_key,
                "/LiveTv/RefreshGuide",
                "Live TV guide refresh",
            )
            if ok:
                d.log(f"[OK] Jellyfin prewarm: {detail}")
            else:
                d.log(f"[WARN] Jellyfin prewarm: {detail}")

        health_cfg = prewarm_cfg.get("artwork_health_check")
        if not isinstance(health_cfg, dict):
            health_cfg = {}
        if refresh_requested and d.bool_cfg(health_cfg, "enabled", True):
            try:
                wait_seconds = int(health_cfg.get("wait_after_refresh_seconds") or 20)
            except Exception:
                wait_seconds = 20
            if wait_seconds > 0:
                d.log(
                    "[INFO] Jellyfin prewarm: waiting for refresh settle before artwork health check "
                    f"({wait_seconds}s)"
                )
                time.sleep(wait_seconds)

        self._run_metadata_backfill(prewarm_cfg, jellyfin_url, jellyfin_api_key)
        self._run_artwork_health_check(prewarm_cfg, jellyfin_url, jellyfin_api_key)

        d.log("[OK] Jellyfin prewarm: reconcile complete")
