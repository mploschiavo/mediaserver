"""Jellyfin plugin bootstrap service."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable
from urllib import parse
from media_stack.api.services.registry import service_internal_url

LogFn = Callable[[str], None]
BoolCfgFn = Callable[[dict[str, Any], str, bool], bool]
CoerceListFn = Callable[[Any], list[Any]]
NormalizeUrlFn = Callable[[str], str]
WaitForServiceFn = Callable[[str, str, str, int], None]
ResolveApiKeyFn = Callable[[dict[str, Any], str], str]
JellyfinRequestFn = Callable[..., tuple[int, Any, str]]


@dataclass
class JellyfinPluginsDependencies:
    log: LogFn
    bool_cfg: BoolCfgFn
    coerce_list: CoerceListFn
    normalize_url: NormalizeUrlFn
    wait_for_service: WaitForServiceFn
    resolve_api_key: ResolveApiKeyFn
    jellyfin_request: JellyfinRequestFn


@dataclass
class JellyfinPluginsService:
    deps: JellyfinPluginsDependencies

    @staticmethod
    def normalize_plugin_name(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())

    def ensure_plugin_repositories(
        self,
        jellyfin_url: str,
        jellyfin_api_key: str,
        repositories: Any,
    ) -> None:
        d = self.deps
        desired = [repo for repo in d.coerce_list(repositories) if isinstance(repo, dict)]
        if not desired:
            return

        status, current, body = d.jellyfin_request(jellyfin_url, "/Repositories", jellyfin_api_key)
        if status != 200 or not isinstance(current, list):
            raise RuntimeError(
                f"Jellyfin plugins: failed listing repositories (HTTP {status}): {body}"
            )

        merged = []
        by_url: dict[str, dict[str, Any]] = {}
        for repo in current:
            if not isinstance(repo, dict):
                continue
            repo_url = str(repo.get("Url") or repo.get("url") or "").strip()
            if not repo_url:
                continue
            normalized = repo_url.lower()
            canonical = {
                "Name": str(repo.get("Name") or repo.get("name") or repo_url).strip(),
                "Url": repo_url,
                "Enabled": bool(repo.get("Enabled", repo.get("enabled", True))),
            }
            by_url[normalized] = canonical
            merged.append(canonical)

        changed = False
        for repo in desired:
            repo_url = str(repo.get("url") or repo.get("Url") or "").strip()
            if not repo_url:
                continue
            normalized = repo_url.lower()
            desired_name = str(repo.get("name") or repo.get("Name") or repo_url).strip()
            desired_enabled = bool(repo.get("enabled", repo.get("Enabled", True)))

            if normalized in by_url:
                existing = by_url[normalized]
                if (
                    existing.get("Name") != desired_name
                    or bool(existing.get("Enabled", True)) != desired_enabled
                ):
                    existing["Name"] = desired_name
                    existing["Enabled"] = desired_enabled
                    changed = True
            else:
                entry = {"Name": desired_name, "Url": repo_url, "Enabled": desired_enabled}
                merged.append(entry)
                by_url[normalized] = entry
                changed = True

        if not changed:
            d.log("[OK] Jellyfin plugins: repositories already match desired config")
            return

        status, _, body = d.jellyfin_request(
            jellyfin_url,
            "/Repositories",
            jellyfin_api_key,
            method="POST",
            payload=merged,
        )
        if status in (200, 201, 202, 204):
            d.log("[OK] Jellyfin plugins: repositories updated")
            return

        raise RuntimeError(
            f"Jellyfin plugins: failed updating repositories (HTTP {status}): {body}"
        )

    def find_package(
        self,
        packages: list[dict[str, Any]],
        target_name: str,
        repository_url: str | None = None,
    ) -> dict[str, Any] | None:
        target_norm = self.normalize_plugin_name(target_name)
        repo_norm = str(repository_url or "").strip().lower()
        exact_name = str(target_name or "").strip().lower()

        def package_repo_match(pkg: dict[str, Any]) -> bool:
            if not repo_norm:
                return True
            versions = self.deps.coerce_list(pkg.get("versions") or pkg.get("Versions"))
            for version in versions:
                if not isinstance(version, dict):
                    continue
                candidate = (
                    str(version.get("repositoryUrl") or version.get("RepositoryUrl") or "")
                    .strip()
                    .lower()
                )
                if candidate == repo_norm:
                    return True
            return False

        for package in packages:
            name = str(package.get("name") or package.get("Name") or "").strip()
            if not name:
                continue
            if name.lower() == exact_name and package_repo_match(package):
                return package

        for package in packages:
            name = str(package.get("name") or package.get("Name") or "").strip()
            if not name:
                continue
            if self.normalize_plugin_name(name) == target_norm and package_repo_match(package):
                return package

        return None

    def ensure(self, cfg: dict[str, Any], config_root: str, wait_timeout: int) -> None:
        d = self.deps
        plugins_cfg = cfg.get("jellyfin_plugins") or {}
        if not d.bool_cfg(plugins_cfg, "enabled", False):
            return

        jellyfin_url = d.normalize_url(plugins_cfg.get("url", service_internal_url("jellyfin")))
        d.wait_for_service("Jellyfin", jellyfin_url, "/System/Info/Public", wait_timeout)

        jellyfin_api_key = d.resolve_api_key(plugins_cfg, config_root)
        if not jellyfin_api_key:
            raise RuntimeError(
                "Jellyfin plugins: API key unavailable. Set JELLYFIN_API_KEY or keep "
                "jellyfin_plugins.auto_discover_api_key_from_db=true."
            )

        self.ensure_plugin_repositories(
            jellyfin_url,
            jellyfin_api_key,
            plugins_cfg.get("repositories"),
        )

        status, installed, body = d.jellyfin_request(jellyfin_url, "/Plugins", jellyfin_api_key)
        if status != 200 or not isinstance(installed, list):
            raise RuntimeError(
                f"Jellyfin plugins: failed listing installed plugins (HTTP {status}): {body}"
            )
        installed_names = {
            self.normalize_plugin_name(item.get("Name") or item.get("name") or "")
            for item in installed
            if isinstance(item, dict)
        }

        status, packages, body = d.jellyfin_request(jellyfin_url, "/Packages", jellyfin_api_key)
        if status != 200 or not isinstance(packages, list):
            raise RuntimeError(
                f"Jellyfin plugins: failed listing available packages (HTTP {status}): {body}"
            )

        installs = d.coerce_list(plugins_cfg.get("install"))
        if not installs:
            d.log("[WARN] Jellyfin plugins: enabled but install list is empty.")
            return

        requested = 0
        already = 0
        for entry in installs:
            if isinstance(entry, dict):
                plugin_name = str(entry.get("name") or "").strip()
                repository_url = str(entry.get("repository_url") or "").strip()
                required = bool(entry.get("required", False))
                version = str(entry.get("version") or "").strip()
            else:
                plugin_name = str(entry).strip()
                repository_url = ""
                required = False
                version = ""

            if not plugin_name:
                continue

            normalized_name = self.normalize_plugin_name(plugin_name)
            if normalized_name in installed_names:
                already += 1
                d.log(f"[OK] Jellyfin plugins: already installed: {plugin_name}")
                continue

            package = self.find_package(packages, plugin_name, repository_url or None)
            if not package:
                message = f"Jellyfin plugins: package not found for '{plugin_name}'" + (
                    f" in repo {repository_url}" if repository_url else ""
                )
                if required:
                    raise RuntimeError(message)
                d.log(f"[WARN] {message}")
                continue

            pkg_name = str(package.get("name") or package.get("Name") or plugin_name).strip()
            pkg_guid = str(package.get("guid") or package.get("Guid") or "").strip()
            query: list[tuple[str, str]] = []
            if pkg_guid:
                query.append(("assemblyGuid", pkg_guid))
            if version:
                query.append(("version", version))
            if repository_url:
                query.append(("repositoryUrl", repository_url))
            path = f"/Packages/Installed/{parse.quote(pkg_name, safe='')}"
            if query:
                path = f"{path}?{parse.urlencode(query)}"

            status, _, body = d.jellyfin_request(
                jellyfin_url,
                path,
                jellyfin_api_key,
                method="POST",
            )
            if status in (200, 201, 202, 204):
                requested += 1
                d.log(f"[OK] Jellyfin plugins: install requested for {pkg_name}")
                continue

            message = f"Jellyfin plugins: failed to install {pkg_name} (HTTP {status}): {body}"
            if required:
                raise RuntimeError(message)
            d.log(f"[WARN] {message}")

        d.log(
            "[OK] Jellyfin plugins: reconcile complete "
            f"(install_requested={requested}, already_installed={already})"
        )
