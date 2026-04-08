"""Low-level normalisation and coercion helpers for bootstrap profile values."""

from __future__ import annotations

import ipaddress
import re
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from media_stack.core.controller_profile.models import ControllerProfileCatalog


# ---------------------------------------------------------------------------
# String / alias normalization
# ---------------------------------------------------------------------------


def _normalize_alias_dict(value: Any, *, field_name: str) -> dict[str, str]:
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


def _normalize_string_list(value: Any, *, field_name: str) -> tuple[str, ...]:
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


def _normalize_string_list_allow_empty(
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


def _normalize_app_token(value: Any) -> str:
    token = str(value or "").strip().lower()
    if not token:
        return ""
    return re.sub(r"[^a-z0-9]+", "", token)


def _normalize_app_name(value: Any, catalog: "ControllerProfileCatalog") -> str:
    token = _normalize_app_token(value)
    if not token:
        return ""
    return catalog.app_aliases.get(token, token)


# ---------------------------------------------------------------------------
# Boolean coercion
# ---------------------------------------------------------------------------


def _as_bool(value: Any, *, default: bool, catalog: "ControllerProfileCatalog") -> bool:
    return _as_bool_with_tokens(
        value,
        default=default,
        true_tokens=catalog.bool_true_tokens,
        false_tokens=catalog.bool_false_tokens,
    )


def _as_bool_with_tokens(
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


# ---------------------------------------------------------------------------
# Numeric / port helpers
# ---------------------------------------------------------------------------


def _to_positive_int(
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


def _normalize_optional_port(value: Any, *, field_name: str) -> str:
    if value is None or str(value).strip() == "":
        return ""
    parsed = _to_positive_int(
        value,
        default=0,
        field_name=field_name,
        minimum=1,
        maximum=65535,
    )
    return str(parsed)


# ---------------------------------------------------------------------------
# Chaos-action normalization
# ---------------------------------------------------------------------------


def _normalize_chaos_actions(
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


# ---------------------------------------------------------------------------
# Catalog-aware field normalizers
# ---------------------------------------------------------------------------


def _normalize_deployment_target(value: Any, catalog: "ControllerProfileCatalog") -> str:
    token = str(value or "").strip().lower()
    normalized = catalog.deployment_aliases.get(token, "")
    if not normalized:
        allowed = ", ".join(sorted(set(catalog.deployment_aliases.keys())))
        raise ValueError(f"metadata.platform must be one of: {allowed}")
    return normalized


def _normalize_purpose(value: Any, catalog: "ControllerProfileCatalog") -> str:
    token = str(value or "").strip().lower()
    if token not in set(catalog.purpose_values):
        allowed = ", ".join(catalog.purpose_values)
        raise ValueError(f"metadata.purpose must be one of: {allowed}")
    return token


def _normalize_route_strategy(value: Any, catalog: "ControllerProfileCatalog") -> str:
    token = str(value or "").strip().lower()
    normalized = catalog.route_strategy_aliases.get(token, "")
    if not normalized:
        allowed = ", ".join(sorted(set(catalog.route_strategy_aliases.keys())))
        raise ValueError(f"routing.strategy must be one of: {allowed}")
    return normalized


def _resolve_install_profile(value: Any, catalog: "ControllerProfileCatalog") -> str:
    token = str(value or "").strip().lower()
    if token not in catalog.install_profiles:
        allowed = ", ".join(sorted(catalog.install_profiles.keys()))
        raise ValueError(f"install_profile must be one of: {allowed}")
    return token


def _split_app_csv(value: str, catalog: "ControllerProfileCatalog") -> tuple[str, ...]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in str(value or "").split(","):
        token = _normalize_app_name(raw, catalog)
        if not token or token in seen:
            continue
        seen.add(token)
        out.append(token)
    return tuple(out)


# ---------------------------------------------------------------------------
# Storage / network / host helpers
# ---------------------------------------------------------------------------


def _parse_storage_gb(value: Any) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return int(value)
    token = str(value or "").strip().lower().replace(" ", "")
    if not token:
        raise ValueError("resources.disk_space_gb is required")
    match = re.fullmatch(r"(?P<num>\d+(?:\.\d+)?)(?P<unit>gb|g|tb|t)?", token)
    if not match:
        raise ValueError(
            "resources.disk_space_gb must be an integer GB value or a value like 500GB/1TB"
        )
    magnitude = float(match.group("num"))
    unit = str(match.group("unit") or "gb")
    if unit in {"tb", "t"}:
        magnitude *= 1000.0
    return int(round(magnitude))


def _normalize_host(value: Any) -> str:
    return str(value or "").strip().lower().strip(".")


def _join_host(*parts: str) -> str:
    tokens = [str(item).strip().strip(".") for item in parts if str(item).strip().strip(".")]
    return ".".join(tokens).lower()


def _parse_private_network_cidr(value: Any) -> str:
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


# ---------------------------------------------------------------------------
# URL coercion
# ---------------------------------------------------------------------------


def _coerce_url_list(value: Any) -> tuple[str, ...]:
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


# ---------------------------------------------------------------------------
# Install-profile app resolution
# ---------------------------------------------------------------------------


def _install_apps_for_profile(
    profile: str,
    catalog: "ControllerProfileCatalog",
) -> dict[str, bool]:
    enabled = set(catalog.install_profiles.get(profile) or ())
    return {app_name: app_name in enabled for app_name in catalog.app_keys}
