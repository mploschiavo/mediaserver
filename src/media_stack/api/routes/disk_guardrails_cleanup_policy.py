"""Cleanup-policy validator + writer (ADR-0008 Phase 4 helpers).

Lifted out of ``api/routes/disk_guardrails.py`` so the route module
doesn't grow further past the file-size ratchet. Both classes own a
single seam each — ``validate(body) -> (cleaned, error)`` and
``write(overrides) -> persisted_dict`` — so the route handler stays
declarative.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Callable

from media_stack.services.cleanup_policy_file import CLEANUP_POLICY_FILE


# ADR-0008 Phase 4 cleanup-policy bounds. Centralised so the validator
# class + the OpenAPI spec stay in lockstep.
CLEANUP_POLICY_VALID_STRATEGIES: frozenset[str] = frozenset({
    "oldest_first", "largest_first", "poor_ratio_first", "watched_first",
})
CLEANUP_POLICY_MAX_DELETE_CAP: int = 100 * 10


class CleanupPolicyValidator:
    """Strategy that validates a cleanup-policy POST body and returns
    the cleaned dict of overrides (or an error string).

    Class-shaped so the no-loose-functions ratchet stays clean and so
    a test can inject a custom strategy set / max-delete cap.
    """

    def __init__(
        self,
        *,
        valid_strategies: frozenset[str] = CLEANUP_POLICY_VALID_STRATEGIES,
        max_delete_cap: int = CLEANUP_POLICY_MAX_DELETE_CAP,
    ) -> None:
        self._valid_strategies = valid_strategies
        self._max_delete_cap = int(max_delete_cap)

    def validate(self, body: dict[str, Any]) -> tuple[dict[str, Any], str]:
        """Return ``(cleaned_overrides, error)``.

        ``error`` non-empty signals a 400 response. Every override is
        optional; a body of ``{}`` is a valid no-op.
        """
        if not isinstance(body, dict):
            return {}, "request body must be a JSON object"
        out: dict[str, Any] = {}
        cat_err = self._validate_categories(body, out)
        if cat_err:
            return {}, cat_err
        num_err = self._validate_numeric_floors(body, out)
        if num_err:
            return {}, num_err
        max_err = self._validate_max_delete(body, out)
        if max_err:
            return {}, max_err
        strat_err = self._validate_strategy(body, out)
        if strat_err:
            return {}, strat_err
        return out, ""

    def _validate_categories(
        self, body: dict[str, Any], out: dict[str, Any],
    ) -> str:
        if "categories" not in body:
            return ""
        categories = body.get("categories")
        if not isinstance(categories, list):
            return "categories must be a list of strings"
        cleaned: list[str] = []
        for raw in categories:
            value = str(raw).strip()
            if value:
                cleaned.append(value)
        out["categories"] = cleaned
        return ""

    def _validate_numeric_floors(
        self, body: dict[str, Any], out: dict[str, Any],
    ) -> str:
        for numeric_key in (
            "min_completion_age_hours",
            "min_seeding_time_minutes",
            "min_ratio",
        ):
            if numeric_key not in body:
                continue
            raw = body.get(numeric_key)
            try:
                value = float(raw) if numeric_key == "min_ratio" else int(raw)
            except (TypeError, ValueError):
                return f"{numeric_key} must be a positive number"
            if value < 0:
                return f"{numeric_key} must be a positive number"
            out[numeric_key] = value
        return ""

    def _validate_max_delete(
        self, body: dict[str, Any], out: dict[str, Any],
    ) -> str:
        if "max_delete_per_run" not in body:
            return ""
        raw = body.get("max_delete_per_run")
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return "max_delete_per_run must be a positive integer"
        if value <= 0:
            return "max_delete_per_run must be a positive integer"
        if value > self._max_delete_cap:
            value = self._max_delete_cap
        out["max_delete_per_run"] = value
        return ""

    def _validate_strategy(
        self, body: dict[str, Any], out: dict[str, Any],
    ) -> str:
        if "order_strategy" not in body:
            return ""
        raw = body.get("order_strategy")
        value = str(raw or "").strip().lower()
        if value not in self._valid_strategies:
            allowed = ", ".join(sorted(self._valid_strategies))
            return f"order_strategy must be one of: {allowed}"
        out["order_strategy"] = value
        return ""


class CleanupPolicyWriter:
    """Adapter that persists the cleanup-policy override JSON file.

    Constructor-injects the path resolver so tests can swap in a
    tmp-path lambda. Atomic save via ``tempfile.NamedTemporaryFile`` +
    ``os.replace`` — same shape ``DownloadLockdownService`` uses for
    its state file. Write failures are surfaced to the caller so the
    route can return 500.
    """

    def __init__(
        self,
        *,
        path_fn: Callable[[], Path] | None = None,
    ) -> None:
        self._path_fn = path_fn

    def _resolve_path(self) -> Path:
        if self._path_fn is not None:
            return self._path_fn()
        return CLEANUP_POLICY_FILE.default_path()

    def write(self, overrides: dict[str, Any]) -> dict[str, Any]:
        path = self._resolve_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(dict(overrides), indent=2, sort_keys=True)
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8",
            dir=str(path.parent),
            prefix=path.name + ".",
            suffix=".tmp",
            delete=False,
        ) as tmp:
            tmp.write(payload + "\n")
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp_path = tmp.name
        os.replace(tmp_path, path)
        return dict(overrides)


__all__ = [
    "CleanupPolicyValidator",
    "CleanupPolicyWriter",
    "CLEANUP_POLICY_VALID_STRATEGIES",
    "CLEANUP_POLICY_MAX_DELETE_CAP",
]
