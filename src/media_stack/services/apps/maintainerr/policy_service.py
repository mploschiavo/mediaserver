"""Maintainerr policy artifact generation service."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

BoolCfgFn = Callable[[dict[str, Any], str, bool], bool]
CoerceListFn = Callable[[Any], list[Any]]
ResolvePathFn = Callable[[str | Path, str], Path]
LogFn = Callable[[str], None]
LoadBootstrapDefaultJsonFn = Callable[[str, dict[str, Any]], dict[str, Any]]
DeepMergeObjectsFn = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]


@dataclass
class MaintainerrPolicyService:
    bool_cfg: BoolCfgFn
    coerce_list: CoerceListFn
    resolve_path: ResolvePathFn
    log: LogFn
    load_bootstrap_default_json: LoadBootstrapDefaultJsonFn
    deep_merge_objects: DeepMergeObjectsFn

    @staticmethod
    def _repo_bootstrap_defaults_dir() -> Path:
        return Path(__file__).resolve().parents[3] / "contracts"

    def _extract_rule_entry(self, source: str, payload: Any) -> tuple[bool, dict[str, Any] | None]:
        if isinstance(payload, dict) and "rule" in payload:
            enabled = bool(payload.get("enabled", True))
            rule_obj = payload.get("rule")
            wrapper = payload
        elif isinstance(payload, dict) and "payload" in payload:
            enabled = bool(payload.get("enabled", True))
            rule_obj = payload.get("payload")
            wrapper = payload
        else:
            enabled = bool(payload.get("enabled", True)) if isinstance(payload, dict) else True
            rule_obj = payload
            wrapper = payload if isinstance(payload, dict) else {}

        if not enabled:
            self.log(f"[INFO] Maintainerr policy rules: skipped disabled entry from {source}")
            return False, None

        if not isinstance(rule_obj, dict):
            raise RuntimeError(
                f"Maintainerr policy rules: {source} must define an object rule payload."
            )

        out = json.loads(json.dumps(rule_obj))
        if isinstance(wrapper, dict):
            for field in ("description", "dataType", "libraryId", "library_titles"):
                if field in wrapper and field not in out:
                    out[field] = json.loads(json.dumps(wrapper[field]))
        if not str(out.get("name") or "").strip():
            stem = Path(source).stem
            out["name"] = stem
        return True, out

    def _load_rule_entries_from_document(self, source: str, payload: Any) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        if isinstance(payload, list):
            for idx, item in enumerate(payload):
                _, extracted = self._extract_rule_entry(f"{source}[{idx}]", item)
                if extracted is not None:
                    entries.append(extracted)
            return entries

        _, extracted = self._extract_rule_entry(source, payload)
        if extracted is not None:
            entries.append(extracted)
        return entries

    def _load_rules_from_directory(
        self,
        directory: Path,
        *,
        required: bool,
        source_label: str,
        enabled_files: set[str],
    ) -> list[dict[str, Any]]:
        if not directory.exists():
            if required:
                raise RuntimeError(
                    f"Maintainerr policy rules: {source_label} directory not found: {directory}"
                )
            self.log(
                f"[INFO] Maintainerr policy rules: {source_label} directory missing: {directory}"
            )
            return []
        if not directory.is_dir():
            raise RuntimeError(
                f"Maintainerr policy rules: {source_label} path is not a directory: {directory}"
            )

        loaded: list[dict[str, Any]] = []
        candidate_files = sorted(
            [
                *directory.rglob("*.json"),
                *directory.rglob("*.yaml"),
                *directory.rglob("*.yml"),
            ]
        )
        for path in candidate_files:
            filename = path.name
            relpath = path.relative_to(directory).as_posix()
            if enabled_files and filename not in enabled_files and relpath not in enabled_files:
                continue
            try:
                raw = path.read_text(encoding="utf-8")
                if path.suffix.lower() == ".json":
                    payload = json.loads(raw)
                else:
                    payload = {
                        "name": path.stem,
                        "yaml": raw,
                    }
            except Exception as exc:
                raise RuntimeError(
                    f"Maintainerr policy rules: failed parsing {path}: {exc}"
                ) from exc
            loaded.extend(self._load_rule_entries_from_document(str(path), payload))
        return self._merge_rules_by_name([], loaded)

    @staticmethod
    def _merge_rules_by_name(
        base_rules: list[dict[str, Any]], override_rules: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        index: dict[str, int] = {}
        for rule in base_rules:
            name = str(rule.get("name") or "").strip().lower()
            merged.append(json.loads(json.dumps(rule)))
            if name:
                index[name] = len(merged) - 1
        for rule in override_rules:
            name = str(rule.get("name") or "").strip().lower()
            copied = json.loads(json.dumps(rule))
            if name and name in index:
                merged[index[name]] = copied
                continue
            merged.append(copied)
            if name:
                index[name] = len(merged) - 1
        return merged

    def ensure_policy(self, cfg: dict[str, Any], config_root: str) -> None:
        maintainerr_cfg = cfg.get("maintainerr") or {}
        if not self.bool_cfg(maintainerr_cfg, "enabled", False):
            return

        default_policy = self.load_bootstrap_default_json(
            "maintainerr_policy.json",
            {
                "version": 1,
                "retention": {},
                "rules": [],
            },
        )
        inline_policy = maintainerr_cfg.get("policy") or {}
        if not isinstance(inline_policy, dict):
            raise RuntimeError("Maintainerr policy: maintainerr.policy must be an object.")

        rules_library_cfg = maintainerr_cfg.get("rules_library") or {}
        if not isinstance(rules_library_cfg, dict):
            raise RuntimeError("Maintainerr policy: maintainerr.rules_library must be an object.")

        include_defaults = self.bool_cfg(rules_library_cfg, "include_defaults", True)
        library_enabled = self.bool_cfg(rules_library_cfg, "enabled", True)
        merge_mode = str(rules_library_cfg.get("merge_mode") or "append").strip().lower()
        if merge_mode not in {"append", "replace"}:
            raise RuntimeError(
                "Maintainerr policy: rules_library.merge_mode must be 'append' or 'replace'."
            )

        enabled_files = {
            str(name).strip()
            for name in self.coerce_list(rules_library_cfg.get("enabled_files"))
            if str(name).strip()
        }

        default_rules: list[dict[str, Any]] = []
        if include_defaults:
            default_rules = self._load_rules_from_directory(
                self._repo_bootstrap_defaults_dir() / "maintainerr_rules",
                required=False,
                source_label="default",
                enabled_files=enabled_files,
            )

        custom_rules: list[dict[str, Any]] = []
        if library_enabled:
            custom_rules = self._load_rules_from_directory(
                self.resolve_path(
                    config_root,
                    str(rules_library_cfg.get("relative_path") or "maintainerr/rules"),
                ),
                required=False,
                source_label="custom",
                enabled_files=enabled_files,
            )

        if merge_mode == "replace":
            seeded_rules = custom_rules if custom_rules else default_rules
        else:
            seeded_rules = self._merge_rules_by_name(default_rules, custom_rules)

        inline_rules = self._load_rule_entries_from_document(
            "maintainerr.policy.rules", inline_policy.get("rules") or []
        )
        if seeded_rules:
            final_rules = self._merge_rules_by_name(seeded_rules, inline_rules)
        else:
            fallback_rules = self._load_rule_entries_from_document(
                "defaults.maintainerr_policy.rules", default_policy.get("rules") or []
            )
            final_rules = self._merge_rules_by_name(fallback_rules, inline_rules)

        inline_policy_without_rules = {
            k: json.loads(json.dumps(v)) for k, v in inline_policy.items() if k != "rules"
        }
        desired = self.deep_merge_objects(default_policy, inline_policy_without_rules)
        desired["rules"] = final_rules

        relative_path = str(
            maintainerr_cfg.get("policy_relative_path") or "maintainerr/policy.json"
        ).strip()
        policy_path = self.resolve_path(config_root, relative_path)
        policy_path.parent.mkdir(parents=True, exist_ok=True)
        rendered = json.dumps(desired, ensure_ascii=True, indent=2, sort_keys=True) + "\n"

        if policy_path.exists():
            current = policy_path.read_text(encoding="utf-8", errors="replace")
            if current == rendered:
                self.log(f"[OK] Maintainerr policy: already up-to-date at {policy_path}")
                return

        policy_path.write_text(rendered, encoding="utf-8")
        self.log(
            f"[OK] Maintainerr policy: wrote {policy_path} "
            f"(rules={len(final_rules)}, default_rules={len(default_rules)}, "
            f"custom_rules={len(custom_rules)}, inline_rules={len(inline_rules)})"
        )
