"""Shared helper functions and constants for the Kubernetes client adapter.

These are extracted from kube_client.py to keep individual modules focused.
All public names are re-exported from kube_client.py for backward compatibility.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from media_stack.core.exceptions import ConfigError

KUBECTL_RETRY_ATTEMPTS = max(1, int(os.environ.get("MEDIA_STACK_KUBECTL_RETRY_ATTEMPTS", "3")))
KUBECTL_RETRY_DELAY_SECONDS = float(
    os.environ.get("MEDIA_STACK_KUBECTL_RETRY_DELAY_SECONDS", "0.5")
)
KUBECTL_RETRY_MAX_DELAY_SECONDS = float(
    os.environ.get("MEDIA_STACK_KUBECTL_RETRY_MAX_DELAY_SECONDS", "3")
)
KUBECTL_RETRY_BACKOFF = float(os.environ.get("MEDIA_STACK_KUBECTL_RETRY_BACKOFF", "2"))



class KubeHelpersService:
    @staticmethod
    def _env_truthy(name: str, *, default: bool) -> bool:
        raw = os.environ.get(name)
        if raw is None:
            return default
        return str(raw).strip().lower() in {"1", "true", "yes", "on"}
    
    
    @staticmethod
    def _is_retryable_kubectl_error(exc: Exception) -> bool:
        text = str(exc).lower()
        retryable_markers = (
            "i/o timeout",
            "timed out",
            "connection refused",
            "connection reset by peer",
            "tls handshake timeout",
            "context deadline exceeded",
            "service unavailable",
            "temporarily unavailable",
            "unable to connect to the server",
            "net/http: request canceled",
        )
        return any(marker in text for marker in retryable_markers)
    
    
    def resolve_kubectl_binary(self) -> list[str]:
        if shutil.which("microk8s"):
            return ["microk8s", "kubectl"]
        if shutil.which("kubectl"):
            return ["kubectl"]
        raise ConfigError("Neither microk8s nor kubectl is available in PATH.")
    
    
    @staticmethod
    def _format_api_error(exc: Exception) -> tuple[int, str]:
        status = int(getattr(exc, "status", 1) or 1)
        body = str(getattr(exc, "body", "") or "").strip()
        reason = str(getattr(exc, "reason", "") or "").strip()
        message = body or reason or str(exc)
        return status, message
    
    
    @staticmethod
    def _selector_from_match_labels(labels: dict[str, str] | None) -> str:
        if not isinstance(labels, dict) or not labels:
            return ""
        parts = [f"{k}={v}" for k, v in labels.items() if str(k).strip() and str(v).strip()]
        return ",".join(parts)
    
    
    @staticmethod
    def _parse_timeout_seconds(value: str | None, default: int = 60) -> int:
        token = str(value or "").strip().lower()
        if not token:
            return default
        if token.endswith("s"):
            token = token[:-1]
        try:
            return max(1, int(token))
        except Exception:
            return default
    
    
    @staticmethod
    def _parse_jsonpath_key(expr: str) -> str:
        token = str(expr or "").strip()
        if token.startswith("jsonpath="):
            token = token[len("jsonpath=") :]
        if token.startswith("{") and token.endswith("}"):
            token = token[1:-1]
        marker = ".data."
        idx = token.find(marker)
        if idx < 0:
            return ""
        return token[idx + len(marker) :].strip()
    
    
    @staticmethod
    def _extract_path_value(payload: dict[str, Any], dotted_path: str) -> Any:
        current: Any = payload
        path = str(dotted_path or "").lstrip(".")
        if not path:
            return ""
        for token in path.split("."):
            token = token.strip()
            if not token:
                continue
            index = None
            if "[" in token and token.endswith("]"):
                name, _, tail = token.partition("[")
                token = name
                raw_idx = tail[:-1]
                try:
                    index = int(raw_idx)
                except Exception:
                    index = None
            if token:
                if not isinstance(current, dict):
                    return ""
                current = current.get(token)
            if index is not None:
                if not isinstance(current, list) or index >= len(current):
                    return ""
                current = current[index]
            if current is None:
                return ""
        return current
    
    
    @staticmethod
    def _render_custom_columns(
        rows: list[dict[str, Any]],
        spec: str,
        *,
        no_headers: bool,
    ) -> str:
        parts = [item.strip() for item in str(spec or "").split(",") if item.strip()]
        columns: list[tuple[str, str]] = []
        for part in parts:
            if ":" not in part:
                continue
            header, _, path = part.partition(":")
            columns.append((header.strip(), path.strip()))
        if not columns:
            return ""
    
        lines: list[str] = []
        if not no_headers:
            lines.append(" ".join(header for header, _ in columns))
        for row in rows:
            values: list[str] = []
            for _, path in columns:
                value = _extract_path_value(row, path)
                if isinstance(value, bool):
                    values.append("true" if value else "false")
                elif value is None:
                    values.append("")
                else:
                    values.append(str(value))
            lines.append(" ".join(values).rstrip())
        return "\n".join(line for line in lines if line is not None).rstrip() + ("\n" if lines else "")


_instance = KubeHelpersService()
resolve_kubectl_binary = _instance.resolve_kubectl_binary
_env_truthy = _instance._env_truthy
_extract_path_value = _instance._extract_path_value
_format_api_error = _instance._format_api_error
_is_retryable_kubectl_error = _instance._is_retryable_kubectl_error
_selector_from_match_labels = _instance._selector_from_match_labels
_parse_timeout_seconds = _instance._parse_timeout_seconds
_parse_jsonpath_key = _instance._parse_jsonpath_key
_render_custom_columns = _instance._render_custom_columns
