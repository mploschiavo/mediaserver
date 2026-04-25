"""Sidecar artwork helpers for Jellyfin prewarm."""

from __future__ import annotations

import os
import posixpath
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any
from zipfile import ZipFile


class JellyfinSidecarOps:

    def normalize_text_list(self, values: Any, fallback: list[str] | None = None) -> list[str]:
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

    def candidate_image_paths(self, 
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

    def extract_epub_cover_bytes(self, epub_path: Path) -> bytes | None:
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
                    opf_path = str(
                        (rootfile.attrib.get("full-path") if rootfile is not None else "") or ""
                    )
                    if opf_path:
                        opf_raw = read_member(opf_path)
                        if opf_raw:
                            opf_root = ET.fromstring(opf_raw)
                            manifest = {
                                str(item.attrib.get("id") or "")
                                .strip(): str(item.attrib.get("href") or "")
                                .strip()
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

    def iter_sidecar_books_root_candidates(self, sidecar_cfg: dict[str, Any]) -> list[Path]:
        raw_candidates: list[str] = []
        configured_list = sidecar_cfg.get("books_root_paths")
        if isinstance(configured_list, list):
            raw_candidates.extend(str(item or "").strip() for item in configured_list)
        elif configured_list is not None:
            raw_candidates.append(str(configured_list).strip())

        raw_candidates.append(
            str(sidecar_cfg.get("books_root_path") or "/srv-stack/media/books").strip()
        )
        raw_candidates.extend(["/media/books"])

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

    def iter_sidecar_music_root_candidates(self, sidecar_cfg: dict[str, Any]) -> list[Path]:
        raw_candidates: list[str] = []
        configured_list = sidecar_cfg.get("music_root_paths")
        if isinstance(configured_list, list):
            raw_candidates.extend(str(item or "").strip() for item in configured_list)
        elif configured_list is not None:
            raw_candidates.append(str(configured_list).strip())

        raw_candidates.append(
            str(sidecar_cfg.get("music_root_path") or "/srv-stack/media/music").strip()
        )
        raw_candidates.extend(["/media/music"])

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

    def path_has_epub(self, path: Path) -> bool:
        try:
            for _ in path.rglob("*.epub"):
                return True
        except Exception:
            return False
        return False

    def path_has_audio(self, path: Path, audio_extensions: set[str]) -> bool:
        try:
            for candidate in path.rglob("*"):
                if not candidate.is_file():
                    continue
                if candidate.suffix.lower() in audio_extensions:
                    return True
        except Exception:
            return False
        return False

    def resolve_books_root_path(self, service, sidecar_cfg: dict[str, Any]) -> tuple[Path | None, list[Path]]:
        candidates = iter_sidecar_books_root_candidates(sidecar_cfg)
        existing = [path for path in candidates if path.exists() and path.is_dir()]
        if not existing:
            return None, candidates
        with_epub = [path for path in existing if path_has_epub(path)]
        if with_epub:
            return with_epub[0], candidates
        return existing[0], candidates

    def resolve_music_root_path(self, service, sidecar_cfg: dict[str, Any]) -> tuple[Path | None, list[Path]]:
        candidates = iter_sidecar_music_root_candidates(sidecar_cfg)
        existing = [path for path in candidates if path.exists() and path.is_dir()]
        if not existing:
            return None, candidates
        audio_extensions = {
            ext.lower()
            for ext in normalize_text_list(
                sidecar_cfg.get("audio_extensions"),
                [".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".alac"],
            )
        }
        with_audio = [path for path in existing if path_has_audio(path, audio_extensions)]
        if with_audio:
            return with_audio[0], candidates
        return existing[0], candidates

    def ensure_book_sidecar_artwork(self, service, prewarm_cfg: dict[str, Any]) -> None:
        d = service.deps
        sidecar_cfg = prewarm_cfg.get("book_sidecar_artwork")
        if not isinstance(sidecar_cfg, dict):
            sidecar_cfg = {}
        if not d.bool_cfg(sidecar_cfg, "enabled", True):
            return

        books_root, candidates = resolve_books_root_path(service, sidecar_cfg)
        if books_root is None:
            # Books library not present → silently skip. The prewarm
            # sidecar feature is opt-in: if there's no books/ root,
            # the user just doesn't have a books library, which is
            # the common case. Was [WARN] which read like an error
            # on every fresh install without books content.
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
        preferred_files = normalize_text_list(
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
            for ext in normalize_text_list(
                sidecar_cfg.get("image_extensions"), [".jpg", ".jpeg", ".png", ".webp"]
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
            if per_book_output_path is not None and (
                replace_existing or not per_book_output_path.exists()
            ):
                pending_targets.append(per_book_output_path)

            if not pending_targets:
                skipped += 1
                continue
            try:
                cover = extract_epub_cover_bytes(epub_path)
                if not cover:
                    fallback_candidates = candidate_image_paths(
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
                d.log(f"[WARN] Jellyfin prewarm: failed sidecar artwork for {epub_path} ({exc})")
                continue

        d.log(
            "[OK] Jellyfin prewarm: book sidecar artwork reconcile complete "
            f"(scanned={scanned}, written={written}, skipped={skipped}, failed={failed})"
        )

    def ensure_music_sidecar_artwork(self, service, prewarm_cfg: dict[str, Any]) -> None:
        d = service.deps
        sidecar_cfg = prewarm_cfg.get("music_sidecar_artwork")
        if not isinstance(sidecar_cfg, dict):
            sidecar_cfg = {}
        if not d.bool_cfg(sidecar_cfg, "enabled", True):
            return

        music_root, candidates = resolve_music_root_path(service, sidecar_cfg)
        if music_root is None:
            # Music library not present → silently skip. Same logic
            # as books above: opt-in feature, missing root just means
            # the user doesn't have a music library.
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

        preferred_files = normalize_text_list(
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
            for ext in normalize_text_list(
                sidecar_cfg.get("image_extensions"), [".jpg", ".jpeg", ".png", ".webp"]
            )
        }
        audio_extensions = {
            ext.lower()
            for ext in normalize_text_list(
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
                candidates = candidate_image_paths(album_dir, preferred_files, allowed_extensions)
                source = next(
                    (path for path in candidates if path.name.lower() != output_name.lower()), None
                )
                if source is None:
                    skipped += 1
                    continue
                output_path.write_bytes(source.read_bytes())
                written += 1
            except Exception as exc:
                failed += 1
                d.log(f"[WARN] Jellyfin prewarm: failed music sidecar artwork for {album_dir} ({exc})")

        d.log(
            "[OK] Jellyfin prewarm: music sidecar artwork reconcile complete "
            f"(scanned={scanned}, written={written}, skipped={skipped}, failed={failed})"
        )


_instance = JellyfinSidecarOps()
normalize_text_list = _instance.normalize_text_list
candidate_image_paths = _instance.candidate_image_paths
extract_epub_cover_bytes = _instance.extract_epub_cover_bytes
iter_sidecar_books_root_candidates = _instance.iter_sidecar_books_root_candidates
iter_sidecar_music_root_candidates = _instance.iter_sidecar_music_root_candidates
path_has_epub = _instance.path_has_epub
path_has_audio = _instance.path_has_audio
resolve_books_root_path = _instance.resolve_books_root_path
resolve_music_root_path = _instance.resolve_music_root_path
ensure_book_sidecar_artwork = _instance.ensure_book_sidecar_artwork
ensure_music_sidecar_artwork = _instance.ensure_music_sidecar_artwork
