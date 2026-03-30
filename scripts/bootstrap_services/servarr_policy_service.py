"""Shared Servarr policy operations (media/download/quality)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

HttpRequestFn = Callable[..., tuple[int, Any, str]]
BoolCfgFn = Callable[[dict[str, Any], str, bool], bool]
CoerceListFn = Callable[[Any], list[Any]]
NormalizeTokenFn = Callable[[Any], str]
ToIntFn = Callable[[Any, int | None], int | None]
ResolveQualityPrefsFn = Callable[[dict[str, Any], dict[str, Any]], tuple[Any, list[str]]]
GetQualityProfileFn = Callable[..., dict[str, Any]]
LogFn = Callable[[str], None]


@dataclass
class ServarrPolicyService:
    http_request: HttpRequestFn
    bool_cfg: BoolCfgFn
    coerce_list: CoerceListFn
    normalize_token: NormalizeTokenFn
    to_int: ToIntFn
    resolve_arr_quality_preferences: ResolveQualityPrefsFn
    get_arr_quality_profile: GetQualityProfileFn
    log: LogFn

    @staticmethod
    def resolve_overrides_by_app(cfg_section: dict[str, Any], app_cfg: dict[str, Any]) -> dict[str, Any]:
        by_app = (cfg_section or {}).get("by_app") or {}
        app_name = str(app_cfg.get("name") or "")
        app_impl = str(app_cfg.get("implementation") or "")
        return (
            by_app.get(app_name)
            or by_app.get(app_impl)
            or by_app.get(app_name.lower())
            or by_app.get(app_impl.lower())
            or {}
        )

    def fetch_download_client_config(
        self,
        app_name: str,
        app_url: str,
        api_base: str,
        api_key: str,
    ) -> tuple[str | None, dict[str, Any] | None]:
        candidate_paths = (
            f"{api_base}/config/downloadclient",
            f"{api_base}/config/downloadClient",
        )
        last_status: int | None = None
        last_body = ""

        for path in candidate_paths:
            status, data, body = self.http_request(app_url, path, api_key=api_key)
            last_status = status
            last_body = body
            if status == 200 and isinstance(data, dict):
                return path, data
            if status not in (404, 405):
                raise RuntimeError(
                    f"{app_name}: failed reading download client config (HTTP {status}): {body}"
                )

        self.log(
            f"[WARN] {app_name}: download client config endpoint not found; "
            f"skipping CDH reconcile (last_status={last_status}, last_body={last_body})"
        )
        return None, None

    def ensure_download_handling(
        self,
        app_name: str,
        app_url: str,
        api_base: str,
        api_key: str,
        handling_cfg: dict[str, Any],
    ) -> None:
        endpoint, current = self.fetch_download_client_config(app_name, app_url, api_base, api_key)
        if not endpoint or not isinstance(current, dict):
            return

        desired_enable = self.bool_cfg(handling_cfg, "enable_completed_download_handling", True)
        desired_remove_completed = self.bool_cfg(handling_cfg, "remove_completed_downloads", False)
        desired_remove_failed = self.bool_cfg(handling_cfg, "remove_failed_downloads", False)
        desired_redownload_failed = self.bool_cfg(handling_cfg, "auto_redownload_failed", False)

        payload = dict(current)
        payload["enableCompletedDownloadHandling"] = desired_enable
        payload["removeCompletedDownloads"] = desired_remove_completed
        payload["removeFailedDownloads"] = desired_remove_failed
        payload["autoRedownloadFailed"] = desired_redownload_failed

        changed = False
        for key in (
            "enableCompletedDownloadHandling",
            "removeCompletedDownloads",
            "removeFailedDownloads",
            "autoRedownloadFailed",
        ):
            if bool(current.get(key)) != bool(payload.get(key)):
                changed = True
                break

        if not changed:
            self.log(
                f"[OK] {app_name}: download handling already set "
                f"(CDH={desired_enable}, removeCompleted={desired_remove_completed}, "
                f"removeFailed={desired_remove_failed}, autoRedownloadFailed={desired_redownload_failed})"
            )
            return

        status, _, body = self.http_request(
            app_url,
            endpoint,
            api_key=api_key,
            method="PUT",
            payload=payload,
        )
        if status in (200, 201, 202):
            self.log(
                f"[OK] {app_name}: updated download handling "
                f"(CDH={desired_enable}, removeCompleted={desired_remove_completed}, "
                f"removeFailed={desired_remove_failed}, autoRedownloadFailed={desired_redownload_failed})"
            )
            return

        raise RuntimeError(f"{app_name}: failed updating download handling (HTTP {status}): {body}")

    def ensure_media_management(
        self,
        app_cfg: dict[str, Any],
        app_url: str,
        api_base: str,
        api_key: str,
        media_cfg: dict[str, Any],
    ) -> None:
        if not self.bool_cfg(media_cfg, "enabled", True):
            return

        app_name = str(app_cfg.get("name") or app_cfg.get("implementation") or "Arr")
        app_impl = str(app_cfg.get("implementation") or "")
        app_overrides = self.resolve_overrides_by_app(media_cfg, app_cfg)

        status, current, body = self.http_request(
            app_url, f"{api_base}/config/mediamanagement", api_key=api_key
        )
        if status != 200 or not isinstance(current, dict):
            raise RuntimeError(
                f"{app_name}: failed reading media management config (HTTP {status}): {body}"
            )

        desired = dict(current)
        changed = False

        if "copy_using_hardlinks" in app_overrides:
            desired_hardlinks = bool(app_overrides.get("copy_using_hardlinks"))
        else:
            desired_hardlinks = self.bool_cfg(media_cfg, "copy_using_hardlinks", True)
        if "copyUsingHardlinks" in desired and bool(desired.get("copyUsingHardlinks")) != bool(
            desired_hardlinks
        ):
            desired["copyUsingHardlinks"] = bool(desired_hardlinks)
            changed = True

        if app_impl == "Sonarr":
            if "create_empty_series_folders" in app_overrides:
                desired_season_folders = bool(app_overrides.get("create_empty_series_folders"))
            else:
                desired_season_folders = self.bool_cfg(media_cfg, "create_empty_series_folders", True)
            if "createEmptySeriesFolders" in desired and bool(
                desired.get("createEmptySeriesFolders")
            ) != bool(desired_season_folders):
                desired["createEmptySeriesFolders"] = bool(desired_season_folders)
                changed = True

        if not changed:
            self.log(
                f"[OK] {app_name}: media management already set "
                f"(hardlinks={bool(desired.get('copyUsingHardlinks', False))})"
            )
            return

        status, _, body = self.http_request(
            app_url,
            f"{api_base}/config/mediamanagement",
            api_key=api_key,
            method="PUT",
            payload=desired,
        )
        if status in (200, 201, 202):
            self.log(
                f"[OK] {app_name}: updated media management "
                f"(hardlinks={bool(desired.get('copyUsingHardlinks', False))})"
            )
            return

        raise RuntimeError(
            f"{app_name}: failed updating media management config (HTTP {status}): {body}"
        )

    def ensure_quality_upgrade_policy(
        self,
        cfg: dict[str, Any],
        app_cfg: dict[str, Any],
        app_url: str,
        api_base: str,
        api_key: str,
        quality_upgrade_cfg: dict[str, Any],
    ) -> None:
        if not self.bool_cfg(quality_upgrade_cfg, "enabled", False):
            return

        app_name = str(app_cfg.get("name") or app_cfg.get("implementation") or "Arr")
        app_overrides = self.resolve_overrides_by_app(quality_upgrade_cfg, app_cfg)
        if "enabled" in app_overrides and not bool(app_overrides.get("enabled")):
            return

        allow_upgrades = self.bool_cfg(
            app_overrides,
            "allow_upgrades",
            self.bool_cfg(quality_upgrade_cfg, "allow_upgrades", True),
        )
        disallow_tokens = [
            self.normalize_token(x)
            for x in self.coerce_list(
                app_overrides.get("disallow_quality_name_tokens")
                or quality_upgrade_cfg.get("disallow_quality_name_tokens")
                or ["2160", "4k", "uhd"]
            )
            if self.normalize_token(x)
        ]
        cutoff_tokens = [
            self.normalize_token(x)
            for x in self.coerce_list(
                app_overrides.get("cutoff_preferred_name_tokens")
                or quality_upgrade_cfg.get("cutoff_preferred_name_tokens")
                or ["1080"]
            )
            if self.normalize_token(x)
        ]

        preferred_id, preferred_names = self.resolve_arr_quality_preferences(cfg, app_cfg)
        selected = self.get_arr_quality_profile(
            app_name,
            app_url,
            api_base,
            api_key,
            preferred_id=preferred_id,
            preferred_names=preferred_names,
        )
        profile_id = selected.get("id")
        if profile_id is None:
            raise RuntimeError(
                f"{app_name}: quality upgrade policy could not resolve quality profile id"
            )

        desired = json.loads(json.dumps(selected))
        changed = False

        for key in ("upgradeAllowed", "upgradesAllowed"):
            if key in desired and bool(desired.get(key)) != bool(allow_upgrades):
                desired[key] = bool(allow_upgrades)
                changed = True

        def entry_quality_name(entry: Any) -> str:
            if not isinstance(entry, dict):
                return ""
            quality = entry.get("quality")
            if isinstance(quality, dict):
                name = str(quality.get("name") or "").strip()
                if name:
                    return name
            return str(entry.get("name") or "").strip()

        def entry_quality_id(entry: Any) -> int | None:
            if not isinstance(entry, dict):
                return None
            quality = entry.get("quality")
            if isinstance(quality, dict):
                qid = self.to_int(quality.get("id"))
                if qid:
                    return qid
            return self.to_int(entry.get("qualityId"))

        cutoff_id = None
        items = desired.get("items")
        if isinstance(items, list):
            rewritten = []
            for entry in items:
                if not isinstance(entry, dict):
                    rewritten.append(entry)
                    continue
                current = dict(entry)
                qname = entry_quality_name(current)
                qtoken = self.normalize_token(qname)

                if disallow_tokens and any(token in qtoken for token in disallow_tokens):
                    if "allowed" in current and bool(current.get("allowed")):
                        current["allowed"] = False
                        changed = True

                if cutoff_id is None and cutoff_tokens:
                    is_allowed = bool(current.get("allowed", True))
                    if is_allowed and any(token in qtoken for token in cutoff_tokens):
                        qid = entry_quality_id(current)
                        if qid:
                            cutoff_id = int(qid)

                rewritten.append(current)

            if rewritten != items:
                desired["items"] = rewritten
                changed = True

        if cutoff_id and "cutoff" in desired and self.to_int(desired.get("cutoff")) != int(cutoff_id):
            desired["cutoff"] = int(cutoff_id)
            changed = True

        if not changed:
            self.log(
                f"[OK] {app_name}: quality-upgrade policy already set "
                f"(allowUpgrades={allow_upgrades}, cutoff={desired.get('cutoff')})"
            )
            return

        status, _, body = self.http_request(
            app_url,
            f"{api_base}/qualityprofile/{profile_id}",
            api_key=api_key,
            method="PUT",
            payload=desired,
        )
        if status in (200, 201, 202):
            self.log(
                f"[OK] {app_name}: updated quality-upgrade policy "
                f"(allowUpgrades={allow_upgrades}, cutoff={desired.get('cutoff')})"
            )
            return

        raise RuntimeError(
            f"{app_name}: failed updating quality-upgrade policy (HTTP {status}): {body}"
        )
