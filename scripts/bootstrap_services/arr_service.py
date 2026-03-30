"""Arr bootstrap service logic."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .config_models import DownloadClientConfig

HttpRequestFn = Callable[..., tuple[int, Any, str]]
LogFn = Callable[[str], None]
FieldMapFn = Callable[[Any], dict[str, Any]]
FieldListFn = Callable[[dict[str, Any]], list[dict[str, Any]]]
CoerceListFn = Callable[[Any], list[Any]]
ToIntFn = Callable[[Any, Any], Any]
NormalizeMappingsFn = Callable[[Any], list[dict[str, str]]]


@dataclass
class ArrService:
    http_request: HttpRequestFn
    log: LogFn
    field_map: FieldMapFn
    field_list: FieldListFn
    coerce_list: CoerceListFn
    to_int: ToIntFn
    normalize_remote_path_mappings: NormalizeMappingsFn

    def choose_category(self, app_cfg: dict[str, Any], client_cfg: dict[str, Any]) -> str:
        if app_cfg.get("qbit_category"):
            return app_cfg["qbit_category"]

        categories = client_cfg.get("categories", {})
        if app_cfg["implementation"] in categories:
            return categories[app_cfg["implementation"]]

        default_map = {
            "Sonarr": "tv",
            "Radarr": "movies",
            "Lidarr": "music",
            "Readarr": "books",
        }
        return default_map.get(app_cfg["implementation"], "downloads")

    def normalize_mapping_path(self, path_value: Any) -> str:
        text = str(path_value or "").strip()
        if not text:
            return ""
        if text != "/":
            text = text.rstrip("/")
        return text

    def build_sab_remote_path_mappings(self, sab_cfg: dict[str, Any]) -> list[dict[str, str]]:
        raw = self.coerce_list(sab_cfg.get("remote_path_mappings"))
        host = str(sab_cfg.get("host", "sabnzbd")).strip() or "sabnzbd"
        complete_dir = self.normalize_mapping_path(
            sab_cfg.get("complete_dir", "/data/usenet/completed")
        )

        if complete_dir:
            raw.extend(
                [
                    {
                        "host": host,
                        "remote_path": "/config/Downloads/complete",
                        "local_path": complete_dir,
                    },
                    {
                        "host": host,
                        "remote_path": "Downloads/complete",
                        "local_path": complete_dir,
                    },
                ]
            )

        return self.normalize_remote_path_mappings(raw)

    def ensure_arr_remote_path_mappings(
        self,
        app_cfg: dict[str, Any],
        app_url: str,
        api_base: str,
        api_key: str,
        mappings: list[dict[str, str]],
    ) -> None:
        desired_mappings = self.normalize_remote_path_mappings(mappings)
        if not desired_mappings:
            return

        app_name = app_cfg.get("name", app_cfg.get("implementation", "Arr"))
        status, existing, body = self.http_request(
            app_url, f"{api_base}/remotepathmapping", api_key=api_key
        )
        if status != 200 or not isinstance(existing, list):
            raise RuntimeError(
                f"{app_name}: failed listing remote path mappings (HTTP {status}): {body}"
            )

        existing_by_key = {}
        for item in existing:
            if not isinstance(item, dict):
                continue
            host = str(item.get("host", "")).strip()
            remote = self.normalize_mapping_path(item.get("remotePath"))
            if not host or not remote:
                continue
            key = (host.lower(), remote)
            if key not in existing_by_key:
                existing_by_key[key] = item

        for mapping in desired_mappings:
            host = str(mapping.get("host", "")).strip()
            remote = self.normalize_mapping_path(mapping.get("remotePath"))
            local = self.normalize_mapping_path(mapping.get("localPath"))
            if not host or not remote or not local:
                continue

            key = (host.lower(), remote)
            current = existing_by_key.get(key)
            if current:
                current_local = self.normalize_mapping_path(current.get("localPath"))
                if current_local == local:
                    self.log(
                        f"[OK] {app_name}: remote path mapping already set "
                        f"({host}: {remote} -> {local})"
                    )
                    continue

                payload = {
                    "id": current.get("id"),
                    "host": host,
                    "remotePath": remote,
                    "localPath": local,
                }
                status, _, body = self.http_request(
                    app_url,
                    f"{api_base}/remotepathmapping/{current.get('id')}",
                    api_key=api_key,
                    method="PUT",
                    payload=payload,
                )
                if status in (200, 201, 202):
                    self.log(
                        f"[OK] {app_name}: updated remote path mapping "
                        f"({host}: {remote} -> {local})"
                    )
                    continue
                raise RuntimeError(
                    f"{app_name}: failed updating remote path mapping "
                    f"({host}: {remote} -> {local}) (HTTP {status}): {body}"
                )

            payload = {"host": host, "remotePath": remote, "localPath": local}
            status, _, body = self.http_request(
                app_url,
                f"{api_base}/remotepathmapping",
                api_key=api_key,
                method="POST",
                payload=payload,
            )
            if status in (200, 201, 202):
                self.log(
                    f"[OK] {app_name}: created remote path mapping "
                    f"({host}: {remote} -> {local})"
                )
                continue
            raise RuntimeError(
                f"{app_name}: failed creating remote path mapping "
                f"({host}: {remote} -> {local}) (HTTP {status}): {body}"
            )

    def ensure_arr_download_client(
        self,
        app_cfg: dict[str, Any],
        app_url: str,
        api_base: str,
        api_key: str,
        client_cfg: dict[str, Any],
        client_auth: dict[str, Any],
    ) -> None:
        status, schemas, body = self.http_request(
            app_url, f"{api_base}/downloadclient/schema", api_key=api_key
        )
        if status != 200 or not isinstance(schemas, list):
            raise RuntimeError(
                f"{app_cfg['name']}: failed to read download client schema (HTTP {status}): {body}"
            )

        client = DownloadClientConfig.from_dict(client_cfg)
        impl_raw = str(client.implementation or "QBittorrent").strip()
        impl_target = impl_raw.lower()
        client_label = str(client.name or impl_raw or "download client")
        client_host = str(client.host).strip()
        client_port = self.to_int(client.port)
        client_use_ssl = bool(client.use_ssl)
        client_url_base = str(client.url_base).strip()
        client_priority = self.to_int(client.priority, 1)
        if client_priority is None:
            client_priority = 1
        if client_priority < 1:
            client_priority = 1
        if client_priority > 50:
            client_priority = 50

        auth_username = str((client_auth or {}).get("username", "")).strip()
        auth_password = str((client_auth or {}).get("password", "")).strip()
        auth_api_key = str(
            (client_auth or {}).get("api_key") or (client_auth or {}).get("apikey") or ""
        ).strip()

        schema = None
        for entry in schemas:
            if str(entry.get("implementation", "")).lower() == impl_target:
                schema = entry
                break
        if not schema:
            raise RuntimeError(
                f"{app_cfg['name']}: schema '{impl_raw}' not found in downloadclient/schema"
            )

        values = self.field_map(schema.get("fields"))
        if "host" in values:
            values["host"] = client_host
        if "hostname" in values:
            values["hostname"] = client_host
        if client_port is not None and "port" in values:
            values["port"] = int(client_port)

        if "useSsl" in values:
            values["useSsl"] = client_use_ssl
        if "ssl" in values:
            values["ssl"] = client_use_ssl
        if "urlBase" in values:
            values["urlBase"] = client_url_base
        if "baseUrl" in values:
            values["baseUrl"] = client_url_base
        if "username" in values:
            values["username"] = auth_username
        if "password" in values:
            values["password"] = auth_password
        if "apiKey" in values:
            values["apiKey"] = auth_api_key
        if "apikey" in values:
            values["apikey"] = auth_api_key
        app_impl_lower = str(app_cfg.get("implementation") or "").strip().lower()
        enforce_dual_priority_fields = app_impl_lower == "readarr"
        for priority_key in list(values.keys()):
            key_lower = str(priority_key).strip().lower()
            if key_lower in ("priority", "torrentpriority", "nzbpriority") or key_lower.endswith(
                "priority"
            ):
                values[priority_key] = client_priority
        has_priority_field = any(str(k).strip().lower() == "priority" for k in values.keys())
        if not has_priority_field:
            values["priority"] = client_priority
            if enforce_dual_priority_fields:
                values["Priority"] = client_priority
        elif enforce_dual_priority_fields:
            values["priority"] = client_priority
            values["Priority"] = client_priority

        category = self.choose_category(app_cfg, client_cfg)
        for key in (
            "category",
            "tvCategory",
            "movieCategory",
            "musicCategory",
            "bookCategory",
            "animeCategory",
        ):
            if key in values:
                values[key] = category

        payload = {
            "name": client_label,
            "implementation": schema.get("implementation", impl_raw),
            "configContract": schema.get("configContract", "QBittorrentSettings"),
            "enable": True,
            "priority": client_priority,
            "tags": [],
            "fields": self.field_list(values),
        }
        if enforce_dual_priority_fields:
            payload["Priority"] = client_priority

        status, clients, body = self.http_request(
            app_url, f"{api_base}/downloadclient", api_key=api_key
        )
        if status != 200 or not isinstance(clients, list):
            raise RuntimeError(
                f"{app_cfg['name']}: failed to list download clients (HTTP {status}): {body}"
            )

        existing = None
        existing_by_name = None
        named_matches = []
        desired_name = client_label
        for item in clients:
            if str(item.get("implementation", "")).lower() != impl_target:
                continue
            if str(item.get("name", "")).strip().lower() == desired_name.strip().lower():
                existing_by_name = item
                named_matches.append(item)
            fields = self.field_map(item.get("fields"))
            field_host = str(fields.get("host", "") or fields.get("hostname", "")).strip()
            field_port = self.to_int(fields.get("port"))
            host_match = bool(client_host and field_host == client_host)
            port_match = bool(client_port is None or field_port == client_port)
            if host_match and port_match:
                existing = item
                break

        def delete_client(client_id):
            status, _, body = self.http_request(
                app_url, f"{api_base}/downloadclient/{client_id}", api_key=api_key, method="DELETE"
            )
            if status not in (200, 202, 204):
                raise RuntimeError(
                    f"{app_cfg['name']}: failed deleting duplicate {client_label} client id={client_id} "
                    f"(HTTP {status}): {body}"
                )

        if len(named_matches) > 1:
            keep = existing.get("id") if existing else named_matches[0].get("id")
            for item in named_matches:
                item_id = item.get("id")
                if item_id is None or item_id == keep:
                    continue
                delete_client(item_id)
                self.log(
                    f"[OK] {app_cfg['name']}: removed duplicate named {client_label} client id={item_id}"
                )
            status, clients, body = self.http_request(
                app_url, f"{api_base}/downloadclient", api_key=api_key
            )
            if status != 200 or not isinstance(clients, list):
                raise RuntimeError(
                    f"{app_cfg['name']}: failed to refresh download clients after duplicate cleanup "
                    f"(HTTP {status}): {body}"
                )
            existing = None
            existing_by_name = None
            for item in clients:
                if str(item.get("implementation", "")).lower() != impl_target:
                    continue
                if str(item.get("name", "")).strip().lower() == desired_name.strip().lower():
                    existing_by_name = item
                fields = self.field_map(item.get("fields"))
                field_host = str(fields.get("host", "") or fields.get("hostname", "")).strip()
                field_port = self.to_int(fields.get("port"))
                host_match = bool(client_host and field_host == client_host)
                port_match = bool(client_port is None or field_port == client_port)
                if host_match and port_match:
                    existing = item

        def save_client(method, path, request_payload):
            status, _, response_body = self.http_request(
                app_url, path, api_key=api_key, method=method, payload=request_payload
            )
            if status in (200, 201, 202):
                return True, status, response_body

            body_lower = str(response_body or "").lower()
            priority_hints = (
                "additional properties",
                "not allowed",
                "unknown",
                "unrecognized",
                "deserialize",
                "invalid property",
            )
            priority_validation_hints = ("inclusivebetweenvalidator", "between 1 and 50")

            if "priority" in body_lower and any(
                hint in body_lower for hint in priority_validation_hints
            ):
                fallback = dict(request_payload)
                fallback["priority"] = client_priority
                if enforce_dual_priority_fields:
                    fallback["Priority"] = client_priority
                normalized_fields = []
                has_priority_field = False
                has_priority_upper = False
                for field in self.coerce_list(fallback.get("fields")):
                    if not isinstance(field, dict):
                        normalized_fields.append(field)
                        continue
                    original_name = str(field.get("name") or "").strip()
                    field_name = str(field.get("name") or "").strip().lower()
                    if field_name in (
                        "priority",
                        "torrentpriority",
                        "nzbpriority",
                    ) or field_name.endswith("priority"):
                        fixed = dict(field)
                        fixed["value"] = client_priority
                        normalized_fields.append(fixed)
                        if field_name == "priority":
                            has_priority_field = True
                        if original_name == "Priority":
                            has_priority_upper = True
                    else:
                        normalized_fields.append(field)
                if not has_priority_field:
                    normalized_fields.append({"name": "priority", "value": client_priority})
                if enforce_dual_priority_fields and not has_priority_upper:
                    normalized_fields.append({"name": "Priority", "value": client_priority})
                fallback["fields"] = normalized_fields
                status2, _, response_body2 = self.http_request(
                    app_url, path, api_key=api_key, method=method, payload=fallback
                )
                if status2 in (200, 201, 202):
                    return True, status2, response_body2
                status = status2
                response_body = response_body2
                body_lower = str(response_body or "").lower()

            if "priority" not in body_lower or not any(
                hint in body_lower for hint in priority_hints
            ):
                return False, status, response_body

            fallback = dict(request_payload)
            fallback.pop("priority", None)
            fallback.pop("Priority", None)
            status2, _, response_body2 = self.http_request(
                app_url, path, api_key=api_key, method=method, payload=fallback
            )
            if status2 in (200, 201, 202):
                return True, status2, response_body2
            return False, status2, response_body2

        def reconcile_existing_by_name():
            status_list, clients_list, body_list = self.http_request(
                app_url, f"{api_base}/downloadclient", api_key=api_key
            )
            if status_list != 200 or not isinstance(clients_list, list):
                raise RuntimeError(
                    f"{app_cfg['name']}: failed refreshing download clients after duplicate-name response (HTTP {status_list}): {body_list}"
                )

            target = None
            for item in clients_list:
                if str(item.get("implementation", "")).lower() != impl_target:
                    continue
                if str(item.get("name", "")).strip().lower() == desired_name.strip().lower():
                    target = item
                    break
            if not target:
                raise RuntimeError(
                    f"{app_cfg['name']}: duplicate '{client_label}' client name detected but no matching existing client was found to reconcile"
                )

            payload["id"] = target.get("id")
            ok3, status3, body3 = save_client(
                "PUT", f"{api_base}/downloadclient/{target.get('id')}", payload
            )
            if ok3:
                self.log(
                    f"[OK] {app_cfg['name']}: reconciled existing named {client_label} download client"
                )
                return
            raise RuntimeError(
                f"{app_cfg['name']}: failed reconciling existing {client_label} client by name (HTTP {status3}): {body3}"
            )

        if existing:
            payload["id"] = existing.get("id")
            ok, status, body = save_client(
                "PUT", f"{api_base}/downloadclient/{existing.get('id')}", payload
            )
            if ok:
                self.log(f"[OK] {app_cfg['name']}: updated {client_label} download client")
                return
            body_lower = str(body or "").lower()
            if status == 400 and "should be unique" in body_lower and "name" in body_lower:
                reconcile_existing_by_name()
                return
            raise RuntimeError(
                f"{app_cfg['name']}: failed updating {client_label} client (HTTP {status}): {body}"
            )

        ok, status, body = save_client("POST", f"{api_base}/downloadclient", payload)
        if ok:
            self.log(f"[OK] {app_cfg['name']}: created {client_label} download client")
            return

        body_lower = str(body or "").lower()
        if status == 400 and "should be unique" in body_lower and "name" in body_lower:
            if existing_by_name is not None:
                payload["id"] = existing_by_name.get("id")
                ok2, status2, body2 = save_client(
                    "PUT", f"{api_base}/downloadclient/{existing_by_name.get('id')}", payload
                )
                if ok2:
                    self.log(
                        f"[OK] {app_cfg['name']}: reconciled existing named {client_label} download client"
                    )
                    return
                raise RuntimeError(
                    f"{app_cfg['name']}: failed reconciling existing {client_label} client by name (HTTP {status2}): {body2}"
                )
            reconcile_existing_by_name()
            return

        raise RuntimeError(
            f"{app_cfg['name']}: failed creating {client_label} client (HTTP {status}): {body}"
        )
