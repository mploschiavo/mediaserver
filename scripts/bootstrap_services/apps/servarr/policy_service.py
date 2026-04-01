"""Shared Servarr policy operations (media/download/quality)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from ...config_models import (
    ArrDownloadHandlingPolicy,
    ArrDownloadHandlingResolvedPolicy,
    ArrMediaManagementPolicy,
    ArrMediaManagementResolvedPolicy,
    ArrQualityUpgradePolicy,
    ArrQualityUpgradeResolvedPolicy,
    ServarrAppConfig,
)

HttpRequestFn = Callable[..., tuple[int, Any, str]]
BoolCfgFn = Callable[[dict[str, Any], str, bool], bool]
CoerceListFn = Callable[[Any], list[Any]]
NormalizeTokenFn = Callable[[Any], str]
ToIntFn = Callable[[Any, int | None], int | None]
ResolveQualityPrefsFn = Callable[[dict[str, Any], dict[str, Any]], tuple[Any, list[str]]]
GetQualityProfileFn = Callable[..., dict[str, Any]]
LogFn = Callable[[str], None]
AppRef = ServarrAppConfig | dict[str, Any] | str


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
    def _coerce_app_ref(app_cfg: AppRef) -> ServarrAppConfig:
        if isinstance(app_cfg, ServarrAppConfig):
            return app_cfg
        if isinstance(app_cfg, dict):
            return ServarrAppConfig.from_dict(dict(app_cfg))
        token = str(app_cfg or "").strip()
        return ServarrAppConfig.from_dict({"name": token, "implementation": token})

    @staticmethod
    def _coerce_download_handling_policy(
        cfg: ArrDownloadHandlingPolicy | dict[str, Any],
    ) -> ArrDownloadHandlingPolicy:
        if isinstance(cfg, ArrDownloadHandlingPolicy):
            return cfg
        return ArrDownloadHandlingPolicy.from_dict(cfg or {})

    @staticmethod
    def _coerce_media_management_policy(
        cfg: ArrMediaManagementPolicy | dict[str, Any],
    ) -> ArrMediaManagementPolicy:
        if isinstance(cfg, ArrMediaManagementPolicy):
            return cfg
        return ArrMediaManagementPolicy.from_dict(cfg or {})

    @staticmethod
    def _coerce_quality_upgrade_policy(
        cfg: ArrQualityUpgradePolicy | dict[str, Any],
    ) -> ArrQualityUpgradePolicy:
        if isinstance(cfg, ArrQualityUpgradePolicy):
            return cfg
        return ArrQualityUpgradePolicy.from_dict(cfg or {})

    @staticmethod
    def resolve_overrides_by_app(
        cfg_section: (
            dict[str, Any]
            | ArrMediaManagementPolicy
            | ArrDownloadHandlingPolicy
            | ArrQualityUpgradePolicy
        ),
        app_cfg: AppRef,
    ) -> dict[str, Any]:
        app_model = ServarrPolicyService._coerce_app_ref(app_cfg)

        if isinstance(cfg_section, ArrMediaManagementPolicy):
            override = cfg_section.override_for(app_model)
            payload: dict[str, Any] = {}
            if override.enabled is not None:
                payload["enabled"] = bool(override.enabled)
            if override.copy_using_hardlinks is not None:
                payload["copy_using_hardlinks"] = bool(override.copy_using_hardlinks)
            if override.create_empty_series_folders is not None:
                payload["create_empty_series_folders"] = bool(override.create_empty_series_folders)
            return payload

        if isinstance(cfg_section, ArrDownloadHandlingPolicy):
            override = cfg_section.override_for(app_model)
            payload = {}
            if override.enabled is not None:
                payload["enabled"] = bool(override.enabled)
            if override.enable_completed_download_handling is not None:
                payload["enable_completed_download_handling"] = bool(
                    override.enable_completed_download_handling
                )
            if override.remove_completed_downloads is not None:
                payload["remove_completed_downloads"] = bool(override.remove_completed_downloads)
            if override.remove_failed_downloads is not None:
                payload["remove_failed_downloads"] = bool(override.remove_failed_downloads)
            if override.auto_redownload_failed is not None:
                payload["auto_redownload_failed"] = bool(override.auto_redownload_failed)
            return payload

        if isinstance(cfg_section, ArrQualityUpgradePolicy):
            override = cfg_section.override_for(app_model)
            payload = {}
            if override.enabled is not None:
                payload["enabled"] = bool(override.enabled)
            if override.allow_upgrades is not None:
                payload["allow_upgrades"] = bool(override.allow_upgrades)
            if override.disallow_quality_name_tokens is not None:
                payload["disallow_quality_name_tokens"] = list(
                    override.disallow_quality_name_tokens
                )
            if override.cutoff_preferred_name_tokens is not None:
                payload["cutoff_preferred_name_tokens"] = list(
                    override.cutoff_preferred_name_tokens
                )
            return payload

        by_app = (cfg_section or {}).get("by_app") or {}
        app_name = str(app_model.name or "")
        app_impl = str(app_model.implementation or "").strip().lower()
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
        app_cfg: AppRef,
        app_url: str,
        api_base: str,
        api_key: str,
        handling_cfg: ArrDownloadHandlingPolicy | dict[str, Any],
    ) -> None:
        app_model = self._coerce_app_ref(app_cfg)
        app_name = str(app_model.name or app_model.implementation or "Arr")
        policy_cfg = self._coerce_download_handling_policy(handling_cfg)
        policy: ArrDownloadHandlingResolvedPolicy = policy_cfg.resolved_for(app_model)
        if not policy.enabled:
            return

        endpoint, current = self.fetch_download_client_config(app_name, app_url, api_base, api_key)
        if not endpoint or not isinstance(current, dict):
            return

        desired_enable = policy.enable_completed_download_handling
        desired_remove_completed = policy.remove_completed_downloads
        desired_remove_failed = policy.remove_failed_downloads
        desired_redownload_failed = policy.auto_redownload_failed

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
        app_cfg: AppRef,
        app_url: str,
        api_base: str,
        api_key: str,
        media_cfg: ArrMediaManagementPolicy | dict[str, Any],
    ) -> None:
        app_model = self._coerce_app_ref(app_cfg)
        app_name = str(app_model.name or app_model.implementation or "Arr")
        app_caps = app_model.capabilities
        policy_cfg = self._coerce_media_management_policy(media_cfg)
        policy: ArrMediaManagementResolvedPolicy = policy_cfg.resolved_for(app_model)
        if not policy.enabled:
            return

        status, current, body = self.http_request(
            app_url, f"{api_base}/config/mediamanagement", api_key=api_key
        )
        if status != 200 or not isinstance(current, dict):
            raise RuntimeError(
                f"{app_name}: failed reading media management config (HTTP {status}): {body}"
            )

        desired = dict(current)
        changed = False

        desired_hardlinks = policy.copy_using_hardlinks
        if "copyUsingHardlinks" in desired and bool(desired.get("copyUsingHardlinks")) != bool(
            desired_hardlinks
        ):
            desired["copyUsingHardlinks"] = bool(desired_hardlinks)
            changed = True

        if app_caps.supports_series_folder_management:
            desired_season_folders = policy.create_empty_series_folders
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
        app_cfg: AppRef,
        app_url: str,
        api_base: str,
        api_key: str,
        quality_upgrade_cfg: ArrQualityUpgradePolicy | dict[str, Any],
    ) -> None:
        app_model = self._coerce_app_ref(app_cfg)
        app_name = str(app_model.name or app_model.implementation or "Arr")
        policy_cfg = self._coerce_quality_upgrade_policy(quality_upgrade_cfg)
        policy: ArrQualityUpgradeResolvedPolicy = policy_cfg.resolved_for(app_model)
        if not policy.enabled:
            return

        allow_upgrades = policy.allow_upgrades
        disallow_tokens = [
            self.normalize_token(x)
            for x in self.coerce_list(policy.disallow_quality_name_tokens)
            if self.normalize_token(x)
        ]
        cutoff_tokens = [
            self.normalize_token(x)
            for x in self.coerce_list(policy.cutoff_preferred_name_tokens)
            if self.normalize_token(x)
        ]

        preferred_id, preferred_names = self.resolve_arr_quality_preferences(cfg, app_model.raw)
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

        if (
            cutoff_id
            and "cutoff" in desired
            and self.to_int(desired.get("cutoff")) != int(cutoff_id)
        ):
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
