"""Authentication bootstrap service logic."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Callable

from media_stack.core.url_utils import normalize_url_base

HttpRequestFn = Callable[..., tuple[int, Any, str]]
LogFn = Callable[[str], None]
BoolCfgFn = Callable[[dict[str, Any], str, bool], bool]


@dataclass
class AuthService:
    http_request: HttpRequestFn
    log: LogFn
    bool_cfg: BoolCfgFn

    @staticmethod
    def _tokenize(value: object) -> str:
        return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())

    @staticmethod
    def _normalize_url_base(value: object) -> str:
        return normalize_url_base(value)

    def _lookup_url_base_from_map(
        self,
        mapping: dict[str, Any],
        *,
        app_name: str,
        implementation: str,
    ) -> str:
        if not isinstance(mapping, dict):
            return ""
        lookup_keys = (
            str(app_name or "").strip(),
            str(implementation or "").strip(),
            str(app_name or "").strip().lower(),
            str(implementation or "").strip().lower(),
        )
        for key in lookup_keys:
            value = mapping.get(key)
            if value is not None:
                return self._normalize_url_base(value)

        app_token = self._tokenize(app_name)
        impl_token = self._tokenize(implementation)
        for raw_key, raw_value in mapping.items():
            key_token = self._tokenize(raw_key)
            if key_token and key_token in {app_token, impl_token}:
                return self._normalize_url_base(raw_value)
        return ""

    def _resolve_url_base(
        self,
        auth_cfg: dict[str, Any],
        *,
        app_name: str,
        implementation: str,
    ) -> str:
        for map_key in ("url_base_by_app", "path_prefix_url_base_by_app"):
            raw_map = auth_cfg.get(map_key)
            if not isinstance(raw_map, dict):
                continue
            value = self._lookup_url_base_from_map(
                raw_map,
                app_name=app_name,
                implementation=implementation,
            )
            if value or any(
                self._tokenize(k) in {self._tokenize(app_name), self._tokenize(implementation)}
                for k in raw_map
            ):
                return value
        return ""

    def auth_scope_matches(
        self, auth_cfg: dict[str, Any], app_name: str, implementation: str
    ) -> bool:
        include = [
            str(x).strip().lower() for x in (auth_cfg.get("include") or []) if str(x).strip()
        ]
        if not include:
            return True
        app_lower = str(app_name).strip().lower()
        impl_lower = str(implementation).strip().lower()
        return app_lower in include or impl_lower in include

    def ensure_app_auth_settings(
        self,
        app_name: str,
        implementation: str,
        app_url: str,
        api_base: str,
        api_key: str,
        auth_cfg: dict[str, Any],
    ) -> None:
        if not self.bool_cfg(auth_cfg, "enabled", False):
            return
        if not self.auth_scope_matches(auth_cfg, app_name, implementation):
            return

        status, current, body = self.http_request(
            app_url, f"{api_base}/config/host", api_key=api_key
        )
        if status != 200 or not isinstance(current, dict):
            raise RuntimeError(
                f"{app_name}: failed reading host config for auth bootstrap (HTTP {status}): {body}"
            )

        method = str(auth_cfg.get("method", "None"))
        required = str(auth_cfg.get("required", "DisabledForLocalAddresses"))
        username_env = auth_cfg.get("username_env", "STACK_ADMIN_USERNAME")
        password_env = auth_cfg.get("password_env", "STACK_ADMIN_PASSWORD")
        username = (os.environ.get(username_env) or "").strip()
        password = (os.environ.get(password_env) or "").strip()

        desired = dict(current)
        changed = False

        desired_url_base = self._resolve_url_base(
            auth_cfg,
            app_name=app_name,
            implementation=implementation,
        )
        # Only write a urlBase when we have a concrete value. If the
        # profile doesn't set ``url_base_by_app`` for this app, we
        # MUST NOT clobber whatever preflight already persisted —
        # the servarr preflight always sets ``/app/{app}`` so direct
        # ``prowlarr:9696/api/v1/...`` calls 307 to the prefixed
        # path. Clearing it back to empty meant Prowlarr's search
        # response put ``http://prowlarr:9696/5/download?...`` in
        # the downloadUrl field, Radarr fetched it, got a 307 with
        # empty body, and MonoTorrent threw IndexOutOfRange parsing
        # zero bytes. qBit stayed at "0 active". (v1.0.141.)
        if desired_url_base:
            for key in ("urlBase", "UrlBase"):
                current_value = self._normalize_url_base(desired.get(key))
                if current_value != desired_url_base:
                    desired[key] = desired_url_base
                    changed = True

        if str(desired.get("authenticationMethod")) != method:
            desired["authenticationMethod"] = method
            changed = True

        if str(desired.get("authenticationRequired")) != required:
            desired["authenticationRequired"] = required
            changed = True

        if method.lower() != "none":
            if not username or not password:
                raise RuntimeError(
                    f"{app_name}: auth method '{method}' requires env creds {username_env}/{password_env}"
                )
            if str(desired.get("username", "")) != username:
                desired["username"] = username
                changed = True
            desired["password"] = password
            changed = True
            # Arr/Prowlarr host config validation may require explicit password confirmation.
            desired["passwordConfirmation"] = password
            # Some versions validate PascalCase property names.
            desired["PasswordConfirmation"] = password
            # Some versions use confirmPassword style keys.
            desired["confirmPassword"] = password
            desired["ConfirmPassword"] = password
            changed = True
        else:
            if desired.get("username"):
                desired["username"] = ""
                changed = True
            if desired.get("password"):
                desired["password"] = ""
                changed = True
            if desired.get("passwordConfirmation"):
                desired["passwordConfirmation"] = ""
                changed = True
            if desired.get("PasswordConfirmation"):
                desired["PasswordConfirmation"] = ""
                changed = True
            if desired.get("confirmPassword"):
                desired["confirmPassword"] = ""
                changed = True
            if desired.get("ConfirmPassword"):
                desired["ConfirmPassword"] = ""
                changed = True

        if not changed:
            self.log(f"[OK] {app_name}: auth settings already match desired config")
            return

        status, _, body = self.http_request(
            app_url,
            f"{api_base}/config/host",
            api_key=api_key,
            method="PUT",
            payload=desired,
        )
        if status in (200, 201, 202):
            self.log(
                f"[OK] {app_name}: auth settings applied " f"(method={method}, required={required})"
            )
            return

        # Retry once for versions that validate one specific confirmation key casing.
        if status == 400 and "passwordconfirmation" in str(body or "").lower():
            retry_payload = dict(desired)
            retry_payload["passwordConfirmation"] = password
            retry_payload["PasswordConfirmation"] = password
            retry_payload["confirmPassword"] = password
            retry_payload["ConfirmPassword"] = password
            status2, _, body2 = self.http_request(
                app_url,
                f"{api_base}/config/host",
                api_key=api_key,
                method="PUT",
                payload=retry_payload,
            )
            if status2 in (200, 201, 202):
                self.log(
                    f"[OK] {app_name}: auth settings applied "
                    f"(method={method}, required={required})"
                )
                return
            status = status2
            body = body2

        raise RuntimeError(f"{app_name}: failed applying auth settings (HTTP {status}): {body}")
