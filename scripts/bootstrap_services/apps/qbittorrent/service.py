"""qBittorrent bootstrap service logic."""

from __future__ import annotations

import json
from dataclasses import dataclass
from http import cookiejar
from typing import Any, Callable
from urllib import error, parse, request

from bootstrap_services.apps.download_clients.config_models import DownloadClientConfig

LogFn = Callable[[str], None]
NormalizeUrlFn = Callable[[str], str]
BoolCfgFn = Callable[[dict[str, Any], str, bool], bool]
ToIntFn = Callable[[Any, Any], Any]
CoerceListFn = Callable[[Any], list[Any]]


@dataclass
class QBittorrentService:
    log: LogFn
    normalize_url: NormalizeUrlFn
    bool_cfg: BoolCfgFn
    to_int: ToIntFn
    coerce_list: CoerceListFn

    def login(self, base_url: str, username: str, password: str):
        jar = cookiejar.CookieJar()
        opener = request.build_opener(request.HTTPCookieProcessor(jar))
        data = parse.urlencode({"username": username, "password": password}).encode("utf-8")
        req = request.Request(
            f"{self.normalize_url(base_url)}/api/v2/auth/login",
            data=data,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with opener.open(req, timeout=20) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        if "Ok." not in body:
            raise RuntimeError("qBittorrent login rejected credentials.")
        return opener

    def create_category(self, opener, base_url: str, category: str, save_path: str) -> None:
        data = parse.urlencode({"category": category, "savePath": save_path}).encode("utf-8")
        req = request.Request(
            f"{self.normalize_url(base_url)}/api/v2/torrents/createCategory",
            data=data,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with opener.open(req, timeout=20):
                pass
            self.log(f"[OK] qBittorrent: category {category} -> {save_path}")
        except error.HTTPError as exc:
            if exc.code == 409:
                self.log(f"[OK] qBittorrent: category already exists: {category}")
                return
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"qBittorrent: failed to create category {category} (HTTP {exc.code}): {body}"
            ) from exc

    def set_preferences(self, opener, base_url: str, preferences: dict[str, Any]) -> None:
        data = parse.urlencode({"json": json.dumps(preferences)}).encode("utf-8")
        req = request.Request(
            f"{self.normalize_url(base_url)}/api/v2/app/setPreferences",
            data=data,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with opener.open(req, timeout=20):
                pass
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"qBittorrent: failed updating preferences (HTTP {exc.code}): {body}"
            ) from exc

    def list_torrents(
        self, opener, base_url: str, filter_value: str = "all"
    ) -> list[dict[str, Any]]:
        req = request.Request(
            f"{self.normalize_url(base_url)}/api/v2/torrents/info?"
            f"{parse.urlencode({'filter': str(filter_value or 'all')})}",
            method="GET",
        )
        with opener.open(req, timeout=25) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(body)
        except Exception as exc:
            raise RuntimeError(f"qBittorrent: failed parsing torrents payload: {exc}") from exc
        if isinstance(payload, list):
            return payload
        raise RuntimeError("qBittorrent: torrents payload was not a list.")

    def list_completed_torrents(self, opener, base_url: str) -> list[dict[str, Any]]:
        return self.list_torrents(opener, base_url, filter_value="completed")

    def delete_torrents(
        self,
        opener,
        base_url: str,
        hashes: list[str],
        delete_files: bool = True,
    ) -> None:
        hash_tokens = [str(value or "").strip() for value in hashes if str(value or "").strip()]
        if not hash_tokens:
            return
        data = parse.urlencode(
            {
                "hashes": "|".join(hash_tokens),
                "deleteFiles": "true" if delete_files else "false",
            }
        ).encode("utf-8")
        req = request.Request(
            f"{self.normalize_url(base_url)}/api/v2/torrents/delete",
            data=data,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with opener.open(req, timeout=30):
            pass

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
        auto_tmm_enabled = bool(config.auto_tmm_enabled)

        prefs: dict[str, Any] = {
            "save_path": save_path,
            "temp_path": temp_path,
            "temp_path_enabled": temp_path_enabled,
            "auto_tmm_enabled": auto_tmm_enabled,
            "torrent_changed_tmm_enabled": True,
        }

        auth_bypass = config.auth_bypass_typed
        bypass_local_auth = bool(auth_bypass.localhost)
        bypass_whitelist_enabled = bool(auth_bypass.whitelist_enabled)
        host_header_validation_enabled = bool(auth_bypass.host_header_validation_enabled)
        reverse_proxy_support_enabled = bool(auth_bypass.reverse_proxy_support_enabled)
        csrf_protection_enabled = bool(auth_bypass.csrf_protection_enabled)
        whitelist_subnets = self._normalize_subnet_list(auth_bypass.whitelist_subnets)
        allow_open_world = bool(auth_bypass.allow_open_world)
        world_open_tokens = {"0.0.0.0", "0.0.0.0/0", "::/0"}
        if not allow_open_world:
            filtered = []
            for subnet in whitelist_subnets:
                if subnet in world_open_tokens:
                    self.log(
                        "[WARN] qBittorrent: refusing world-open auth bypass subnet "
                        f"'{subnet}'. Set download_clients.qbittorrent.auth_bypass.allow_open_world=true "
                        "to allow it explicitly."
                    )
                    continue
                filtered.append(subnet)
            whitelist_subnets = filtered

        if bypass_whitelist_enabled and not whitelist_subnets:
            self.log(
                "[WARN] qBittorrent: auth bypass whitelist enabled but no valid subnets "
                "resolved; disabling subnet whitelist bypass."
            )
            bypass_whitelist_enabled = False

        prefs["bypass_local_auth"] = bypass_local_auth
        prefs["bypass_auth_subnet_whitelist_enabled"] = bypass_whitelist_enabled
        prefs["bypass_auth_subnet_whitelist"] = (
            ",".join(whitelist_subnets) if bypass_whitelist_enabled else ""
        )
        prefs["web_ui_host_header_validation_enabled"] = host_header_validation_enabled
        prefs["web_ui_reverse_proxy_enabled"] = reverse_proxy_support_enabled
        prefs["web_ui_csrf_protection_enabled"] = csrf_protection_enabled

        seeding_policy = config.seeding_policy_typed
        if seeding_policy.enabled:
            max_ratio_val = seeding_policy.max_ratio
            max_seed_minutes = seeding_policy.max_seeding_time_minutes
            remove_on_limit = bool(seeding_policy.remove_on_limit_reached)
            if remove_on_limit:
                self.log(
                    "[WARN] qBittorrent: seeding_policy.remove_on_limit_reached=true "
                    "conflicts with Arr completed-download handling; forcing pause-on-limit."
                )
                remove_on_limit = False

            if max_ratio_val is not None and max_ratio_val > 0:
                prefs["max_ratio_enabled"] = True
                prefs["max_ratio"] = max_ratio_val
                prefs["max_ratio_act"] = 1 if remove_on_limit else 0
            elif seeding_policy.max_ratio_enabled:
                prefs["max_ratio_enabled"] = False

            if max_seed_minutes is not None and max_seed_minutes > 0:
                prefs["max_seeding_time_enabled"] = True
                prefs["max_seeding_time"] = int(max_seed_minutes)
                prefs["max_ratio_act"] = 1 if remove_on_limit else prefs.get("max_ratio_act", 0)
            elif seeding_policy.max_seeding_time_enabled:
                prefs["max_seeding_time_enabled"] = False

        set_prefs = set_preferences_fn or self.set_preferences
        set_prefs(opener, qbit_url, prefs)
        self.log(
            "[OK] qBittorrent: storage defaults set "
            f"(save_path={save_path}, temp_path={temp_path}, "
            f"temp_path_enabled={temp_path_enabled}, auto_tmm_enabled={auto_tmm_enabled}, "
            f"bypass_local_auth={bypass_local_auth}, "
            f"bypass_auth_subnet_whitelist_enabled={bypass_whitelist_enabled}, "
            f"host_header_validation_enabled={host_header_validation_enabled}, "
            f"reverse_proxy_support_enabled={reverse_proxy_support_enabled}, "
            f"csrf_protection_enabled={csrf_protection_enabled}, "
            f"whitelist_count={len(whitelist_subnets)})"
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
        config = DownloadClientConfig.from_dict(qbit_cfg)
        qbit_url = self.normalize_url(config.url or "http://qbittorrent:8080")
        login = login_fn or self.login
        opener = login(qbit_url, qb_username, qb_password)

        setup_storage = setup_storage_defaults_fn or self.setup_storage_defaults
        setup_storage(opener, qbit_url, config.raw or qbit_cfg)

        completed_paths = dict(config.completed_paths or {})
        create_category = create_category_fn or self.create_category
        for app in arr_apps:
            category = choose_category_fn(app, config.raw or qbit_cfg)
            default_path = f"/data/torrents/completed/{category}"
            save_path = completed_paths.get(category, default_path)
            create_category(opener, qbit_url, category, save_path)

    def _normalize_subnet_list(self, values: Any) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for raw in self.coerce_list(values):
            subnet = str(raw or "").strip()
            if not subnet or subnet in seen:
                continue
            seen.add(subnet)
            normalized.append(subnet)
        return normalized
