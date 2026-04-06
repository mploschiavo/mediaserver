"""Maintainerr policy-rule synchronization service."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from .rule_translation_service import (
    MaintainerrRuleTranslationDependencies,
    MaintainerrRuleTranslationService,
)

LogFn = Callable[[str], None]
RequestFn = Callable[..., tuple[int, Any, str]]
ResolvePathFn = Callable[[str, str], Any]


@dataclass
class MaintainerrRuleSyncDependencies:
    log: LogFn
    request: RequestFn
    resolve_path: ResolvePathFn


@dataclass
class MaintainerrRuleSyncService:
    deps: MaintainerrRuleSyncDependencies

    def _translator(self) -> MaintainerrRuleTranslationService:
        return MaintainerrRuleTranslationService(
            deps=MaintainerrRuleTranslationDependencies(
                log=self.deps.log,
                request=self.deps.request,
                resolve_path=self.deps.resolve_path,
            )
        )

    def sync_policy_rules(
        self,
        *,
        maintainerr_url: str,
        maintainerr_cfg: dict[str, Any],
        config_root: str,
    ) -> None:
        translator = self._translator()
        policy_rel_path = translator._text(
            maintainerr_cfg.get("policy_relative_path") or "maintainerr/policy.json"
        )
        policy_path = self.deps.resolve_path(config_root, policy_rel_path)
        if not policy_path.exists():
            self.deps.log(
                f"[WARN] Maintainerr: policy file not found at {policy_path}; skipping rule sync."
            )
            return

        policy_doc = json.loads(policy_path.read_text(encoding="utf-8", errors="replace") or "{}")
        policy_rules = policy_doc.get("rules") or []
        if not isinstance(policy_rules, list):
            raise RuntimeError("Maintainerr: policy rules must be a list.")
        if not policy_rules:
            self.deps.log("[INFO] Maintainerr: no policy rules to sync.")
            return

        libraries = translator._resolve_libraries(maintainerr_url)
        if not libraries:
            raise RuntimeError("Maintainerr: no compatible media-server libraries available.")

        desired_rules = translator._desired_rule_payloads(
            maintainerr_url=maintainerr_url,
            policy_rules=policy_rules,
            libraries=libraries,
        )
        if not desired_rules:
            self.deps.log("[WARN] Maintainerr: no translatable rules were produced from policy.")
            return

        status, existing_data, body = self.deps.request(
            maintainerr_url, "/api/rules?activeOnly=false"
        )
        if status != 200 or not isinstance(existing_data, list):
            raise RuntimeError(
                f"Maintainerr: failed reading existing rules (HTTP {status}): {body}"
            )

        existing_by_name: dict[str, dict[str, Any]] = {}
        for item in existing_data:
            if not isinstance(item, dict):
                continue
            name = translator._text(item.get("name"))
            if not name:
                continue
            existing_by_name[name] = item

        created = 0
        updated = 0
        for desired in desired_rules:
            existing = existing_by_name.get(translator._text(desired.get("name")))
            method = "POST"
            payload = dict(desired)
            if isinstance(existing, dict):
                existing_id = existing.get("id")
                if existing_id is not None:
                    payload["id"] = existing_id
                    method = "PUT"

            status, data, body = self.deps.request(
                maintainerr_url, "/api/rules", method=method, payload=payload
            )
            if status < 200 or status >= 300:
                raise RuntimeError(
                    f"Maintainerr: failed syncing rule '{payload.get('name')}' "
                    f"(HTTP {status}): {body}"
                )
            if isinstance(data, dict) and data.get("code") == 0:
                raise RuntimeError(
                    f"Maintainerr: rule sync failed for '{payload.get('name')}': {data.get('result')}"
                )
            if method == "POST":
                created += 1
            else:
                updated += 1

        self.deps.log(
            f"[OK] Maintainerr: synced policy rules (created={created}, updated={updated}, "
            f"total_desired={len(desired_rules)})"
        )
