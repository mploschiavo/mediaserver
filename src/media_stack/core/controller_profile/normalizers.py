"""Low-level normalisation and coercion helpers for bootstrap profile values."""

from __future__ import annotations

import ipaddress
import re
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from media_stack.core.controller_profile.models import ControllerProfileCatalog


class _PrimitiveNormalizer:
    """Catalog-free string / bool / int / list / host coercion helpers."""

    _APP_TOKEN_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
    _STORAGE_GB_RE = re.compile(r"(?P<num>\d+(?:\.\d+)?)(?P<unit>gb|g|tb|t)?")

    def normalize_string_list(self, value: Any, *, field_name: str) -> tuple[str, ...]:
        if not isinstance(value, list) or not value:
            raise ValueError(f"{field_name} must be a non-empty array")
        out: list[str] = []
        seen: set[str] = set()
        for raw in value:
            token = str(raw or "").strip().lower()
            if not token or token in seen:
                continue
            seen.add(token)
            out.append(token)
        if not out:
            raise ValueError(f"{field_name} must contain at least one value")
        return tuple(out)

    def normalize_string_list_allow_empty(
        self,
        value: Any,
        *,
        field_name: str,
        default: tuple[str, ...],
    ) -> tuple[str, ...]:
        if value is None:
            return tuple(default)
        if not isinstance(value, list):
            raise ValueError(f"{field_name} must be an array when provided")
        out: list[str] = []
        seen: set[str] = set()
        for raw in value:
            token = str(raw or "").strip().lower()
            if not token or token in seen:
                continue
            seen.add(token)
            out.append(token)
        return tuple(out) if out else tuple(default)

    def normalize_app_token(self, value: Any) -> str:
        token = str(value or "").strip().lower()
        if not token:
            return ""
        return self._APP_TOKEN_NON_ALNUM_RE.sub("", token)

    def as_bool_with_tokens(
        self,
        value: Any,
        *,
        default: bool,
        true_tokens: tuple[str, ...],
        false_tokens: tuple[str, ...],
    ) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return bool(value)
        token = str(value).strip().lower()
        if not token:
            return default
        if token in set(true_tokens):
            return True
        if token in set(false_tokens):
            return False
        raise ValueError(f"Invalid boolean value '{value}'")

    def to_positive_int(
        self,
        value: Any,
        *,
        default: int,
        field_name: str,
        minimum: int,
        maximum: int,
    ) -> int:
        if value is None or str(value).strip() == "":
            return int(default)
        try:
            parsed = int(str(value).strip())
        except Exception as exc:
            raise ValueError(f"{field_name} must be an integer.") from exc
        if parsed < minimum or parsed > maximum:
            raise ValueError(f"{field_name} must be between {minimum} and {maximum}.")
        return parsed

    def normalize_optional_port(self, value: Any, *, field_name: str) -> str:
        if value is None or str(value).strip() == "":
            return ""
        parsed = self.to_positive_int(
            value,
            default=0,
            field_name=field_name,
            minimum=1,
            maximum=65535,
        )
        return str(parsed)

    def normalize_host(self, value: Any) -> str:
        return str(value or "").strip().lower().strip(".")

    def join_host(self, *parts: str) -> str:
        tokens = [str(item).strip().strip(".") for item in parts if str(item).strip().strip(".")]
        return ".".join(tokens).lower()

    def parse_storage_gb(self, value: Any) -> int:
        if isinstance(value, int) and not isinstance(value, bool):
            return int(value)
        token = str(value or "").strip().lower().replace(" ", "")
        if not token:
            raise ValueError("resources.disk_space_gb is required")
        match = self._STORAGE_GB_RE.fullmatch(token)
        if not match:
            raise ValueError(
                "resources.disk_space_gb must be an integer GB value or a value like 500GB/1TB"
            )
        magnitude = float(match.group("num"))
        unit = str(match.group("unit") or "gb")
        if unit in {"tb", "t"}:
            magnitude *= 1000.0
        return int(round(magnitude))

    def parse_private_network_cidr(self, value: Any) -> str:
        token = str(value or "").strip()
        if not token:
            raise ValueError("resources.network_cidr is required")
        try:
            network = ipaddress.ip_network(token, strict=False)
        except ValueError as exc:
            raise ValueError(f"Invalid resources.network_cidr '{token}'") from exc
        if not network.is_private:
            raise ValueError(
                f"Network CIDR '{token}' is not private. Use RFC1918 ranges (10/8, 172.16/12, 192.168/16)."
            )
        return str(network)

    def coerce_url_list(self, value: Any) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            token = value.strip()
            return (token,) if token else ()
        if not isinstance(value, list):
            return ()
        out: list[str] = []
        for item in value:
            token = str(item or "").strip()
            if token:
                out.append(token)
        return tuple(out)


class _AppNormalizer:
    """Catalog-aware app / alias / dict normalizers."""

    def __init__(self, primitives: _PrimitiveNormalizer) -> None:
        self._primitives = primitives

    def normalize_alias_dict(self, value: Any, *, field_name: str) -> dict[str, str]:
        if not isinstance(value, dict) or not value:
            raise ValueError(f"{field_name} must be a non-empty object")
        out: dict[str, str] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key or "").strip().lower()
            normalized = str(raw_value or "").strip().lower()
            if key and normalized:
                out[key] = normalized
        if not out:
            raise ValueError(f"{field_name} must contain at least one mapping")
        return out

    def normalize_app_name(self, value: Any, catalog: "ControllerProfileCatalog") -> str:
        token = self._primitives.normalize_app_token(value)
        if not token:
            return ""
        return catalog.app_aliases.get(token, token)

    def as_bool(self, value: Any, *, default: bool, catalog: "ControllerProfileCatalog") -> bool:
        return self._primitives.as_bool_with_tokens(
            value,
            default=default,
            true_tokens=catalog.bool_true_tokens,
            false_tokens=catalog.bool_false_tokens,
        )

    def normalize_chaos_actions(
        self,
        value: Any,
        *,
        allowed: tuple[str, ...],
        default: tuple[str, ...],
    ) -> tuple[str, ...]:
        allowed_set = set(allowed)
        if not allowed_set:
            raise ValueError("chaos allowed-actions cannot be empty")
        if value is None:
            return tuple(default)
        if not isinstance(value, list):
            raise ValueError("chaos.actions must be an array when provided")
        out: list[str] = []
        seen: set[str] = set()
        for raw in value:
            token = str(raw or "").strip().lower()
            if not token or token in seen:
                continue
            if token not in allowed_set:
                allowed_text = ", ".join(sorted(allowed_set))
                raise ValueError(
                    f"chaos.actions contains unsupported value '{token}'. Allowed: {allowed_text}"
                )
            seen.add(token)
            out.append(token)
        if not out:
            return tuple(default)
        return tuple(out)

    def normalize_deployment_target(
        self, value: Any, catalog: "ControllerProfileCatalog"
    ) -> str:
        token = str(value or "").strip().lower()
        normalized = catalog.deployment_aliases.get(token, "")
        if not normalized:
            allowed = ", ".join(sorted(set(catalog.deployment_aliases.keys())))
            raise ValueError(f"metadata.platform must be one of: {allowed}")
        return normalized

    def normalize_purpose(self, value: Any, catalog: "ControllerProfileCatalog") -> str:
        token = str(value or "").strip().lower()
        if token not in set(catalog.purpose_values):
            allowed = ", ".join(catalog.purpose_values)
            raise ValueError(f"metadata.purpose must be one of: {allowed}")
        return token

    def normalize_route_strategy(
        self, value: Any, catalog: "ControllerProfileCatalog"
    ) -> str:
        token = str(value or "").strip().lower()
        normalized = catalog.route_strategy_aliases.get(token, "")
        if not normalized:
            allowed = ", ".join(sorted(set(catalog.route_strategy_aliases.keys())))
            raise ValueError(f"routing.strategy must be one of: {allowed}")
        return normalized

    def resolve_install_profile(
        self, value: Any, catalog: "ControllerProfileCatalog"
    ) -> str:
        token = str(value or "").strip().lower()
        if token not in catalog.install_profiles:
            allowed = ", ".join(sorted(catalog.install_profiles.keys()))
            raise ValueError(f"install_profile must be one of: {allowed}")
        return token

    def split_app_csv(
        self, value: str, catalog: "ControllerProfileCatalog"
    ) -> tuple[str, ...]:
        out: list[str] = []
        seen: set[str] = set()
        for raw in str(value or "").split(","):
            token = self.normalize_app_name(raw, catalog)
            if not token or token in seen:
                continue
            seen.add(token)
            out.append(token)
        return tuple(out)

    def install_apps_for_profile(
        self,
        profile: str,
        catalog: "ControllerProfileCatalog",
    ) -> dict[str, bool]:
        enabled = set(catalog.install_profiles.get(profile) or ())
        return {app_name: app_name in enabled for app_name in catalog.app_keys}


# ---------------------------------------------------------------------------
# Module-level singletons + underscore-prefixed aliases preserving the
# public import API used by __init__.py / models.py / parser.py /
# catalog_loader.py / tests/unit/api/test_controller_profile.py.
# ---------------------------------------------------------------------------

_PRIMITIVES = _PrimitiveNormalizer()
_APPS = _AppNormalizer(_PRIMITIVES)

_normalize_string_list = _PRIMITIVES.normalize_string_list
_normalize_string_list_allow_empty = _PRIMITIVES.normalize_string_list_allow_empty
_normalize_app_token = _PRIMITIVES.normalize_app_token
_as_bool_with_tokens = _PRIMITIVES.as_bool_with_tokens
_to_positive_int = _PRIMITIVES.to_positive_int
_normalize_optional_port = _PRIMITIVES.normalize_optional_port
_normalize_host = _PRIMITIVES.normalize_host
_join_host = _PRIMITIVES.join_host
_parse_storage_gb = _PRIMITIVES.parse_storage_gb
_parse_private_network_cidr = _PRIMITIVES.parse_private_network_cidr
_coerce_url_list = _PRIMITIVES.coerce_url_list

_normalize_alias_dict = _APPS.normalize_alias_dict
_normalize_app_name = _APPS.normalize_app_name
_as_bool = _APPS.as_bool
_normalize_chaos_actions = _APPS.normalize_chaos_actions
_normalize_deployment_target = _APPS.normalize_deployment_target
_normalize_purpose = _APPS.normalize_purpose
_normalize_route_strategy = _APPS.normalize_route_strategy
_resolve_install_profile = _APPS.resolve_install_profile
_split_app_csv = _APPS.split_app_csv
_install_apps_for_profile = _APPS.install_apps_for_profile
