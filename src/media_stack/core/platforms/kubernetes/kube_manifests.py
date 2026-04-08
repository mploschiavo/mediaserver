"""Manifest operations mixin for the Kubernetes client adapter.

Handles apply, create, replace of YAML manifests, ConfigMap creation,
and namespace creation. Mixed into KubernetesClient by kube_client.py.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from media_stack.core.exceptions import ConfigError
from media_stack.core.platforms.kubernetes.kube_helpers import _format_api_error
from media_stack.core.subprocess_utils import CommandResult


class ManifestsMixin:
    """Mixin providing manifest CRUD operations for KubernetesClient."""

    # These attributes are provided by the base KubernetesClient class.
    _k8s_client: Any
    _core_v1: Any
    _dynamic_client: Any

    def _result(self, args: list[str], **kwargs: Any) -> CommandResult:  # type: ignore[empty-body]
        ...

    def _error_result(self, args: list[str], message: str, **kwargs: Any) -> CommandResult:  # type: ignore[empty-body]
        ...

    def _dynamic_get_resource(self, api_version: str, kind: str) -> Any:
        return self._dynamic_client.resources.get(api_version=api_version, kind=kind)

    def _iter_yaml_objects(self, manifest_path: str) -> list[dict[str, Any]]:
        path = Path(manifest_path)
        if not path.exists():
            raise ConfigError(f"Manifest path not found: {path}")
        raw = path.read_text(encoding="utf-8")
        return self._iter_yaml_objects_from_text(raw, source=str(path))

    def _iter_yaml_objects_from_text(self, raw: str, *, source: str) -> list[dict[str, Any]]:
        objects = [item for item in yaml.safe_load_all(raw) if isinstance(item, dict)]
        if not objects:
            raise ConfigError(f"No Kubernetes objects found in manifest: {source}")
        return objects

    def _normalize_object_namespace(self, obj: dict[str, Any], namespace: str) -> dict[str, Any]:
        metadata = obj.setdefault("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
            obj["metadata"] = metadata
        if namespace:
            metadata.setdefault("namespace", namespace)
        return obj

    def _apply_object(self, obj: dict[str, Any], *, namespace: str) -> str:
        api_version = str(obj.get("apiVersion") or "").strip()
        kind = str(obj.get("kind") or "").strip()
        if not api_version or not kind:
            raise ConfigError("Manifest object missing required apiVersion/kind.")
        body = self._normalize_object_namespace(dict(obj), namespace)
        metadata = body.get("metadata") or {}
        name = str((metadata or {}).get("name") or "").strip()
        if not name:
            raise ConfigError("Manifest object missing metadata.name.")

        resource = self._dynamic_get_resource(api_version, kind)
        kwargs: dict[str, Any] = {"name": name, "body": body}
        if getattr(resource, "namespaced", False):
            kwargs["namespace"] = str((metadata or {}).get("namespace") or namespace or "default")
        resource.patch(
            content_type="application/apply-patch+yaml",
            field_manager="media-stack",
            force=True,
            **kwargs,
        )
        return f"{kind.lower()}/{name} configured"

    def _replace_object(self, obj: dict[str, Any], *, namespace: str) -> str:
        api_version = str(obj.get("apiVersion") or "").strip()
        kind = str(obj.get("kind") or "").strip()
        body = self._normalize_object_namespace(dict(obj), namespace)
        metadata = body.get("metadata") or {}
        name = str((metadata or {}).get("name") or "").strip()
        if not api_version or not kind or not name:
            raise ConfigError("Manifest object missing required apiVersion/kind/metadata.name.")

        resource = self._dynamic_get_resource(api_version, kind)
        kwargs: dict[str, Any] = {"name": name, "body": body}
        if getattr(resource, "namespaced", False):
            kwargs["namespace"] = str((metadata or {}).get("namespace") or namespace or "default")
        resource.replace(**kwargs)
        return f"{kind.lower()}/{name} replaced"

    def _create_object(self, obj: dict[str, Any], *, namespace: str) -> str:
        api_version = str(obj.get("apiVersion") or "").strip()
        kind = str(obj.get("kind") or "").strip()
        body = self._normalize_object_namespace(dict(obj), namespace)
        metadata = body.get("metadata") or {}
        name = str((metadata or {}).get("name") or "").strip()
        if not api_version or not kind:
            raise ConfigError("Manifest object missing required apiVersion/kind.")

        resource = self._dynamic_get_resource(api_version, kind)
        kwargs: dict[str, Any] = {"body": body}
        if getattr(resource, "namespaced", False):
            kwargs["namespace"] = str((metadata or {}).get("namespace") or namespace or "default")
        resource.create(**kwargs)
        if name:
            return f"{kind.lower()}/{name} created"
        return f"{kind.lower()} created"

    def _run_manifest_command(
        self,
        args: list[str],
        namespace: str,
        command: str,
        remainder: list[str],
        *,
        input_text: str | None,
    ) -> CommandResult:
        if command == "create" and remainder and remainder[0] == "configmap":
            return self._run_create_configmap(args, namespace, remainder[1:])

        if command == "create" and remainder and remainder[0] == "namespace":
            ns_name = remainder[1] if len(remainder) > 1 else ""
            if ns_name:
                return self._run_create_namespace(args, ns_name)

        if len(remainder) < 2 or remainder[0] != "-f":
            return self._error_result(
                args,
                f"Unsupported manifest command: {' '.join(args)}",
                returncode=2,
            )

        manifest_path = remainder[1]
        if manifest_path == "-":
            if input_text is None:
                return self._error_result(
                    args,
                    "Manifest stdin payload required for '-f -'.",
                    returncode=2,
                )
            objects = self._iter_yaml_objects_from_text(input_text, source="stdin")
        else:
            objects = self._iter_yaml_objects(manifest_path)
        messages: list[str] = []
        try:
            for obj in objects:
                if command == "apply":
                    messages.append(self._apply_object(obj, namespace=namespace))
                elif command == "replace":
                    messages.append(self._replace_object(obj, namespace=namespace))
                elif command == "create":
                    messages.append(self._create_object(obj, namespace=namespace))
        except Exception as exc:
            status, message = _format_api_error(exc)
            return self._error_result(args, message, returncode=status or 1)
        out = "\n".join(messages).rstrip()
        return self._result(args, stdout=(out + "\n") if out else "")

    def _run_create_namespace(
        self,
        args: list[str],
        ns_name: str,
    ) -> CommandResult:
        body = self._k8s_client.V1Namespace(
            metadata=self._k8s_client.V1ObjectMeta(name=ns_name)
        )
        try:
            self._core_v1.create_namespace(body=body)
            return self._result(args, stdout=f"namespace/{ns_name} created\n")
        except Exception as exc:
            status, message = _format_api_error(exc)
            if "already exists" in str(message).lower():
                return self._result(
                    args,
                    stdout=f"namespace/{ns_name} already exists\n",
                    stderr=f"{message}\n",
                    returncode=0,
                )
            return self._error_result(args, message, returncode=status or 1)

    def _run_create_configmap(
        self,
        args: list[str],
        namespace: str,
        remainder: list[str],
    ) -> CommandResult:
        if not remainder:
            return self._error_result(args, "Missing ConfigMap name.", returncode=2)
        name = str(remainder[0] or "").strip()
        from_files: list[tuple[str, str]] = []
        output = ""
        dry_run = False

        cursor = 1
        while cursor < len(remainder):
            token = remainder[cursor]
            if token.startswith("--from-file="):
                pair = token.split("=", 1)[1]
                key, _, path = pair.partition("=")
                if not path:
                    path = key
                    key = Path(path).name
                from_files.append((key, path))
                cursor += 1
                continue
            if token == "--from-file" and cursor + 1 < len(remainder):
                pair = remainder[cursor + 1]
                key, _, path = pair.partition("=")
                if not path:
                    path = key
                    key = Path(path).name
                from_files.append((key, path))
                cursor += 2
                continue
            if token.startswith("--dry-run="):
                dry_run = token.split("=", 1)[1].strip().lower() == "client"
                cursor += 1
                continue
            if token == "--dry-run":
                dry_run = True
                cursor += 1
                continue
            if token == "-o" and cursor + 1 < len(remainder):
                output = str(remainder[cursor + 1] or "")
                cursor += 2
                continue
            if token.startswith("-o="):
                output = token.split("=", 1)[1]
                cursor += 1
                continue
            cursor += 1

        data: dict[str, str] = {}
        for key, file_path in from_files:
            fpath = Path(file_path)
            if not fpath.exists():
                return self._error_result(
                    args, f"ConfigMap source file not found: {fpath}", returncode=1
                )
            data[str(key)] = fpath.read_text(encoding="utf-8")

        body = {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": name, "namespace": namespace},
            "data": data,
        }

        if dry_run and output == "yaml":
            return self._result(args, stdout=yaml.safe_dump(body, sort_keys=False))

        try:
            self._core_v1.create_namespaced_config_map(namespace=namespace, body=body)
        except Exception as exc:
            status, message = _format_api_error(exc)
            return self._error_result(args, message, returncode=status or 1)
        return self._result(args, stdout=f"configmap/{name} created\n")
