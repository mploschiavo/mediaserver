"""Compose preflight hooks for stack-level filesystem priming."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

InfoFn = Callable[[str], None]


class StackComposePreflight:

    @staticmethod
    def _text(value: Any) -> str:
        return str(value or "").strip()

    @staticmethod
    def _required_paths(media_root: Path, data_root: Path) -> tuple[Path, ...]:
        media_base = media_root / "media"
        torrents_base = data_root / "torrents"
        usenet_base = data_root / "usenet"
        return (
            media_base,
            media_base / "tv",
            media_base / "movies",
            media_base / "music",
            media_base / "books",
            torrents_base,
            torrents_base / "incomplete",
            torrents_base / "completed",
            torrents_base / "completed" / "tv",
            torrents_base / "completed" / "movies",
            torrents_base / "completed" / "music",
            torrents_base / "completed" / "books",
            usenet_base,
            usenet_base / "incomplete",
            usenet_base / "completed",
            usenet_base / "completed" / "tv",
            usenet_base / "completed" / "movies",
            usenet_base / "completed" / "music",
            usenet_base / "completed" / "books",
        )

    @staticmethod
    def _required_config_paths(config_root: Path) -> tuple[Path, ...]:
        return (config_root / "maintainerr",)

    @staticmethod
    def _to_uid(value: Any, *, default: int) -> int:
        token = _text(value)
        if token.isdigit():
            return int(token)
        return default

    @staticmethod
    def _reconcile_permissions_with_helper(
        *,
        target_path: Path,
        uid: int,
        gid: int,
        docker: Any,
        info: InfoFn,
    ) -> bool:
        raw_client = getattr(docker, "client", None) if docker is not None else None
        if raw_client is None:
            return False
        helper_image = "busybox:1.36.1"
        command = (
            f"chown -R {uid}:{gid} /target && " "chmod -R u+rwX,g+rwX /target && " "chmod 775 /target"
        )
        try:
            raw_client.images.pull(helper_image)
        except Exception as exc:
            # Continue when the helper image is already present/offline.
            import logging; logging.getLogger("media_stack").debug("[DEBUG] Swallowed: %s", exc)
            pass
        try:
            raw_client.containers.run(
                image=helper_image,
                command=["sh", "-lc", command],
                remove=True,
                volumes={str(target_path.resolve()): {"bind": "/target", "mode": "rw"}},
            )
        except Exception as exc:
            info("Compose filesystem preflight: permission helper failed for " f"{target_path}: {exc}")
            return False
        info(
            "Compose filesystem preflight: reconciled config path ownership via helper "
            f"(path={target_path}, uid={uid}, gid={gid})."
        )
        return True

    @staticmethod
    def _ensure_paths_writable(paths: tuple[Path, ...], *, mode: int = 0o777) -> tuple[int, int]:
        created = 0
        total = 0
        for directory in paths:
            total += 1
            existed = directory.exists()
            directory.mkdir(parents=True, exist_ok=True)
            if not existed:
                created += 1
            try:
                directory.chmod(mode)
            except Exception as exc:
                # Keep preflight non-fatal when chmod is restricted by host FS policy.
                import logging; logging.getLogger("media_stack").debug("[DEBUG] Swallowed: %s", exc)
                pass
        return total, created

    @staticmethod
    def _ensure_config_paths_writable(
        *,
        paths: tuple[Path, ...],
        uid: int,
        gid: int,
        docker: Any,
        info: InfoFn,
    ) -> tuple[int, int, int]:
        created = 0
        total = 0
        reconciled = 0
        for directory in paths:
            total += 1
            existed = directory.exists()
            directory.mkdir(parents=True, exist_ok=True)
            if not existed:
                created += 1
            try:
                directory.chmod(0o775)
            except Exception as exc:
                import logging; logging.getLogger("media_stack").debug("[DEBUG] Swallowed: %s", exc)
                pass

            try:
                st = directory.stat()
                owner_mismatch = st.st_uid != uid or st.st_gid != gid
            except Exception:
                owner_mismatch = True

            if not owner_mismatch:
                continue
            if _reconcile_permissions_with_helper(
                target_path=directory,
                uid=uid,
                gid=gid,
                docker=docker,
                info=info,
            ):
                reconciled += 1
        return total, created, reconciled

    def ensure_compose_stack_filesystem_paths(self, 
        *,
        compose_env: dict[str, str],
        docker: Any = None,
        info: InfoFn,
        **_: object,
    ) -> dict[str, str]:
        media_root_token = _text(compose_env.get("MEDIA_ROOT"))
        data_root_token = _text(compose_env.get("DATA_ROOT"))
        if not media_root_token or not data_root_token:
            info(
                "Compose filesystem preflight: MEDIA_ROOT or DATA_ROOT missing; "
                "skipping directory priming."
            )
            return {}

        media_root = Path(media_root_token).expanduser()
        data_root = Path(data_root_token).expanduser()

        total, created = _ensure_paths_writable(_required_paths(media_root, data_root))

        config_root_token = _text(
            compose_env.get("COMPOSE_CONFIG_ROOT") or compose_env.get("CONFIG_ROOT")
        )
        config_total = 0
        config_created = 0
        config_reconciled = 0
        config_root = None
        if config_root_token:
            config_root = Path(config_root_token).expanduser()
            config_uid = _to_uid(compose_env.get("PUID"), default=1000)
            config_gid = _to_uid(compose_env.get("PGID"), default=config_uid)
            config_total, config_created, config_reconciled = _ensure_config_paths_writable(
                paths=_required_config_paths(config_root),
                uid=config_uid,
                gid=config_gid,
                docker=docker,
                info=info,
            )

        info(
            "Compose filesystem preflight: ensured media/data directory tree "
            f"(paths={total}, created={created}, media_root={media_root}, data_root={data_root}, "
            f"config_paths={config_total}, config_created={config_created}, "
            f"config_reconciled={config_reconciled}, "
            f"config_root={config_root})."
        )
        return {}


_instance = StackComposePreflight()
ensure_compose_stack_filesystem_paths = _instance.ensure_compose_stack_filesystem_paths


__all__ = ["ensure_compose_stack_filesystem_paths"]
_ensure_config_paths_writable = _instance._ensure_config_paths_writable
_ensure_paths_writable = _instance._ensure_paths_writable
_reconcile_permissions_with_helper = _instance._reconcile_permissions_with_helper
_required_config_paths = _instance._required_config_paths
_required_paths = _instance._required_paths
_text = _instance._text
_to_uid = _instance._to_uid
