"""Reflection helpers for convention-based adapter discovery."""

from __future__ import annotations

import importlib
import inspect
import re
from types import ModuleType
from typing import TypeVar

TBase = TypeVar("TBase", bound=type)


class AdapterReflection:
    """Convention-based adapter discovery via module and class name patterns."""

    @staticmethod
    def module_token_from_key(key: str) -> str:
        token = re.sub(r"[^a-zA-Z0-9_]+", "_", str(key or "").strip().lower())
        token = re.sub(r"_+", "_", token).strip("_")
        return token

    @staticmethod
    def class_prefix_from_key(key: str) -> str:
        parts = [part for part in re.split(r"[^a-zA-Z0-9]+", str(key or "").strip()) if part]
        if not parts:
            return ""
        return "".join(part[:1].upper() + part[1:] for part in parts)

    @staticmethod
    def _candidate_classes_from_module(module: ModuleType, base_class: type) -> list[type]:
        matches: list[type] = []
        for value in vars(module).values():
            if not inspect.isclass(value):
                continue
            if value is base_class:
                continue
            if issubclass(value, base_class):
                matches.append(value)
        return matches

    @staticmethod
    def discover_adapter_class(
        *,
        module_prefix: str,
        key: str,
        base_class: type,
        class_suffix: str,
    ) -> type | None:
        """Discover adapter class from a conventional module path.

        Strategy:
        1. import `{module_prefix}.{module_token_from_key(key)}`
        2. try conventional class name: `{class_prefix_from_key(key)}{class_suffix}`
        3. if absent, pick a unique subclass of `base_class` from the module
        """
        module_token = AdapterReflection.module_token_from_key(key)
        if not module_token:
            return None

        module_name = f"{module_prefix}.{module_token}"
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError:
            return None

        conventional_name = f"{AdapterReflection.class_prefix_from_key(key)}{class_suffix}"
        conventional = getattr(module, conventional_name, None)
        if inspect.isclass(conventional) and conventional is not base_class:
            if issubclass(conventional, base_class):
                return conventional

        candidates = AdapterReflection._candidate_classes_from_module(module, base_class)
        if not candidates:
            return None

        suffix_matches = [cls for cls in candidates if cls.__name__.endswith(class_suffix)]
        if len(suffix_matches) == 1:
            return suffix_matches[0]
        if len(candidates) == 1:
            return candidates[0]
        return None


# ---------------------------------------------------------------------------
# Singleton + backward-compat module-level references
# ---------------------------------------------------------------------------

_instance = AdapterReflection()
module_token_from_key = _instance.module_token_from_key
class_prefix_from_key = _instance.class_prefix_from_key
discover_adapter_class = _instance.discover_adapter_class
