"""Authentication bootstrap service logic."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable

HttpRequestFn = Callable[..., tuple[int, Any, str]]
LogFn = Callable[[str], None]
BoolCfgFn = Callable[[dict[str, Any], str, bool], bool]


@dataclass
class AuthService:
    http_request: HttpRequestFn
    log: LogFn
    bool_cfg: BoolCfgFn

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
