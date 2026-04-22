"""Transmission bootstrap service logic."""

from __future__ import annotations


from media_stack.core.logging_utils import log_swallowed
import base64
import json
from dataclasses import dataclass
from typing import Any, Callable
from urllib import error, request

from media_stack.services.apps.download_clients.config_models import DownloadClientConfig
from media_stack.api.services.registry import service_internal_url
import logging

LogFn = Callable[[str], None]
NormalizeUrlFn = Callable[[str], str]
BoolCfgFn = Callable[[dict[str, Any], str, bool], bool]
ToIntFn = Callable[[Any, Any], Any]
CoerceListFn = Callable[[Any], list[Any]]


@dataclass
class _TransmissionSession:
    base_url: str
    username: str
    password: str
    session_id: str = ""


@dataclass
class TransmissionService:
    log: LogFn
    normalize_url: NormalizeUrlFn
    bool_cfg: BoolCfgFn
    to_int: ToIntFn
    coerce_list: CoerceListFn

    def login(self, base_url: str, username: str, password: str):
        session = _TransmissionSession(
            base_url=self.normalize_url(base_url),
            username=str(username or "").strip(),
            password=str(password or "").strip(),
        )
        self._rpc_call(session, "session-get")
        return session

    def create_category(self, opener, base_url: str, category: str, save_path: str) -> None:
        del opener, base_url, save_path
        # Transmission has no first-class categories; labels are per-torrent.
        self.log(
            f"[INFO] Transmission: category bootstrap '{category}' skipped (labels are applied per torrent)."
        )

    def set_preferences(self, opener, base_url: str, preferences: dict[str, Any]) -> None:
        del base_url
        args: dict[str, Any] = {}
        if "save_path" in preferences:
            args["download-dir"] = str(preferences.get("save_path") or "").strip()
        if "temp_path_enabled" in preferences:
            args["incomplete-dir-enabled"] = bool(preferences.get("temp_path_enabled"))
        if "temp_path" in preferences:
            args["incomplete-dir"] = str(preferences.get("temp_path") or "").strip()
        if "max_ratio_enabled" in preferences:
            args["seedRatioLimited"] = bool(preferences.get("max_ratio_enabled"))
        if "max_ratio" in preferences:
            try:
                args["seedRatioLimit"] = float(preferences.get("max_ratio"))
            except Exception as exc:
                log_swallowed(exc)
        if "max_seeding_time_enabled" in preferences:
            args["seedIdleLimited"] = bool(preferences.get("max_seeding_time_enabled"))
        if "max_seeding_time" in preferences:
            parsed = self.to_int(preferences.get("max_seeding_time"), None)
            if parsed is not None and parsed >= 0:
                # Transmission expects minutes.
                args["seedIdleLimit"] = int(parsed)
        if not args:
            self.log("[INFO] Transmission: no compatible preference keys to apply; skipping.")
            return
        self._rpc_call(opener, "session-set", args)

    def setup_storage_defaults(
        self,
        opener,
        qbit_url: str,
        qbit_cfg: dict[str, Any],
        set_preferences_fn: Callable[[Any, str, dict[str, Any]], None] | None = None,
    ) -> None:
        config = DownloadClientConfig.from_dict(qbit_cfg)
        save_path = str(config.default_save_path).rstrip("/")
        temp_path = str(config.temp_path).rstrip("/")
        temp_path_enabled = bool(config.temp_path_enabled)

        prefs: dict[str, Any] = {
            "save_path": save_path,
            "temp_path": temp_path,
            "temp_path_enabled": temp_path_enabled,
        }
        set_prefs = set_preferences_fn or self.set_preferences
        set_prefs(opener, qbit_url, prefs)
        self.log(
            "[OK] Transmission: storage defaults set "
            f"(download_dir={save_path}, incomplete_dir={temp_path}, "
            f"incomplete_enabled={temp_path_enabled})"
        )

    def setup_categories(
        self,
        arr_apps: list[dict[str, Any]],
        qbit_cfg: dict[str, Any],
        qb_username: str,
        qb_password: str,
        choose_category_fn: Callable[[dict[str, Any], dict[str, Any]], str],
        setup_storage_defaults_fn: Callable[[Any, str, dict[str, Any]], None] | None = None,
        create_category_fn: Callable[[Any, str, str, str], None] | None = None,
        login_fn: Callable[[str, str, str], Any] | None = None,
    ) -> None:
        del create_category_fn
        config = DownloadClientConfig.from_dict(qbit_cfg)
        transmission_url = self.normalize_url(config.url or service_internal_url("transmission"))
        login = login_fn or self.login
        opener = login(transmission_url, qb_username, qb_password)
        setup_storage = setup_storage_defaults_fn or self.setup_storage_defaults
        setup_storage(opener, transmission_url, config.raw or qbit_cfg)
        categories = sorted(
            {
                choose_category_fn(app, config.raw or qbit_cfg)
                for app in arr_apps
                if isinstance(app, dict)
            }
        )
        self.log(
            "[INFO] Transmission: category bootstrap completed via Arr category fields "
            f"(categories={categories or ['downloads']})."
        )

    def list_torrents(
        self, opener, base_url: str, filter_value: str = "all"
    ) -> list[dict[str, Any]]:
        del base_url
        payload = self._rpc_call(
            opener,
            "torrent-get",
            {
                "fields": [
                    "hashString",
                    "name",
                    "status",
                    "percentDone",
                    "addedDate",
                    "doneDate",
                    "activityDate",
                    "rateDownload",
                    "totalSize",
                    "eta",
                    "labels",
                    "isFinished",
                ]
            },
        )
        torrents = payload.get("torrents")
        if not isinstance(torrents, list):
            return []
        mapped = [self._map_torrent_record(item) for item in torrents if isinstance(item, dict)]
        mode = str(filter_value or "all").strip().lower()
        if mode == "completed":
            return [item for item in mapped if float(item.get("progress") or 0.0) >= 1.0]
        return mapped

    def list_completed_torrents(self, opener, base_url: str) -> list[dict[str, Any]]:
        return self.list_torrents(opener, base_url, filter_value="completed")

    def delete_torrents(
        self,
        opener,
        base_url: str,
        hashes: list[str],
        delete_files: bool = True,
    ) -> None:
        del base_url
        ids = [str(token or "").strip() for token in hashes if str(token or "").strip()]
        if not ids:
            return
        self._rpc_call(
            opener,
            "torrent-remove",
            {
                "ids": ids,
                "delete-local-data": bool(delete_files),
            },
        )

    def _auth_header(self, session: _TransmissionSession) -> dict[str, str]:
        if not session.username and not session.password:
            return {}
        token = base64.b64encode(f"{session.username}:{session.password}".encode("utf-8")).decode(
            "ascii"
        )
        return {"Authorization": f"Basic {token}"}

    def _rpc_call(
        self,
        session: _TransmissionSession,
        method: str,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        rpc_url = f"{self.normalize_url(session.base_url)}/transmission/rpc"
        body = json.dumps(
            {
                "method": str(method),
                "arguments": dict(arguments or {}),
            }
        ).encode("utf-8")

        for _attempt in range(2):
            headers = {
                "Content-Type": "application/json",
                **self._auth_header(session),
            }
            if session.session_id:
                headers["X-Transmission-Session-Id"] = session.session_id
            req = request.Request(
                rpc_url,
                data=body,
                method="POST",
                headers=headers,
            )
            try:
                with request.urlopen(req, timeout=25) as resp:
                    raw = resp.read().decode("utf-8", errors="replace")
                payload = json.loads(raw)
                if not isinstance(payload, dict):
                    raise RuntimeError("Transmission RPC payload is not an object.")
                result = str(payload.get("result") or "").strip().lower()
                if result != "success":
                    raise RuntimeError(
                        f"Transmission RPC {method} failed with result='{payload.get('result')}'."
                    )
                args = payload.get("arguments")
                if isinstance(args, dict):
                    return args
                return {}
            except error.HTTPError as exc:
                if exc.code == 409:
                    session_id = str(exc.headers.get("X-Transmission-Session-Id") or "").strip()
                    if session_id:
                        session.session_id = session_id
                        continue
                body_text = exc.read().decode("utf-8", errors="replace")
                raise RuntimeError(
                    f"Transmission RPC {method} failed (HTTP {exc.code}): {body_text}"
                ) from exc
        raise RuntimeError(f"Transmission RPC {method} failed after session negotiation retries.")

    def _map_torrent_record(self, item: dict[str, Any]) -> dict[str, Any]:
        status = int(self.to_int(item.get("status"), 0) or 0)
        status_map = {
            0: "pausedDL",
            1: "checkingDL",
            2: "checkingDL",
            3: "queuedDL",
            4: "downloading",
            5: "queuedUP",
            6: "uploading",
        }
        labels = item.get("labels")
        category = "uncategorized"
        if isinstance(labels, list):
            label = next((str(x).strip() for x in labels if str(x).strip()), "")
            if label:
                category = label
        progress = float(item.get("percentDone") or 0.0)
        return {
            "hash": str(item.get("hashString") or "").strip(),
            "name": str(item.get("name") or "").strip(),
            "category": category,
            "state": status_map.get(status, "unknown"),
            "size": int(self.to_int(item.get("totalSize"), 0) or 0),
            "progress": progress,
            "added_on": int(self.to_int(item.get("addedDate"), 0) or 0),
            "completion_on": int(self.to_int(item.get("doneDate"), 0) or 0),
            "last_activity": int(self.to_int(item.get("activityDate"), 0) or 0),
            "age_hours": 0.0,
            "stalled_hours": 0.0,
            "dlspeed": int(self.to_int(item.get("rateDownload"), 0) or 0),
            "eta": int(self.to_int(item.get("eta"), -1) or -1),
        }
