"""Jellyfin prewarm bootstrap service."""

from __future__ import annotations

import posixpath
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
                "/srv-host-stack/media/books",
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
    def _path_has_epub(path: Path) -> bool:
        try:
            for _ in path.rglob("*.epub"):
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
            output_path = epub_path.parent / output_name
            if output_path.exists() and not replace_existing:
                skipped += 1
                continue
            try:
                cover = self._extract_epub_cover_bytes(epub_path)
                if not cover:
                    skipped += 1
                    continue
                output_path.write_bytes(cover)
                written += 1
            except Exception as exc:
                failed += 1
                d.log(
                    f"[WARN] Jellyfin prewarm: failed sidecar artwork for {epub_path} ({exc})"
                )

        d.log(
            "[OK] Jellyfin prewarm: book sidecar artwork reconcile complete "
            f"(scanned={scanned}, written={written}, skipped={skipped}, failed={failed})"
        )

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

        refresh_params = prewarm_cfg.get("library_refresh_query")
        if not isinstance(refresh_params, dict):
            refresh_params = {
                "metadataRefreshMode": "FullRefresh",
                "imageRefreshMode": "FullRefresh",
                "replaceAllMetadata": "false",
                "replaceAllImages": "false",
            }

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

        d.log("[OK] Jellyfin prewarm: reconcile complete")
