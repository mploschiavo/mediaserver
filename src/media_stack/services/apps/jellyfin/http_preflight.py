"""Jellyfin preflight: startup wizard, auth, and API key provisioning.

Uses the same JellyfinBootstrapAuthService as the compose preflight
for exact parity. Runs inside the bootstrap runner container with
HTTP access to jellyfin:8096 and file access to /srv-config/jellyfin.
"""

from __future__ import annotations

import json
import time
from typing import Any
from urllib import error, request

from media_stack.api.services.registry import service_internal_url


class JellyfinHttpPreflight:

    @staticmethod
    def _http(
        base_url: str,
        path: str,
        *,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: int = 15,
    ) -> tuple[int, Any, str]:
        """HTTP request returning (status, parsed_data, body_str) 3-tuple."""
        url = f"{base_url.rstrip('/')}{path}"
        data = json.dumps(payload).encode("utf-8") if payload else None
        hdrs = {"Content-Type": "application/json", **(headers or {})}
        req = request.Request(url, data=data, headers=hdrs, method=method)
        try:
            with request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                try:
                    return resp.status, json.loads(body), body
                except (json.JSONDecodeError, ValueError):
                    return resp.status, body, body
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            return exc.code, None, body
        except Exception as exc:
            import logging
            logging.getLogger("media_stack").debug("[DEBUG] Jellyfin preflight HTTP %s %s → error: %s", method, url, exc)
            return 0, None, ""

    @staticmethod
    def _wait_ready(base_url: str, timeout_seconds: int = 120) -> bool:
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            status, _, _ = _http(base_url, "/System/Info/Public")
            if status == 200:
                return True
            time.sleep(3)
        return False

    def run_preflight(self,
        *,
        jellyfin_url: str | None = None,
        admin_username: str = "admin",
        admin_password: str = "media-dev",
        api_key_name: str = "media-stack-controller",
        wait_timeout: int = 120,
        log: Any = None,
        **kwargs: Any,
    ) -> dict[str, str]:
        """Run Jellyfin startup wizard + API key provisioning.

        Uses JellyfinBootstrapAuthService for exact parity with compose preflight.
        Returns dict with JELLYFIN_API_KEY and JELLYFIN_USER_ID if successful.
        """
        if jellyfin_url is None:
            jellyfin_url = service_internal_url("jellyfin")

        def info(msg: str) -> None:
            if log:
                log(msg)

        info(f"Jellyfin preflight: waiting for {jellyfin_url}")
        if not _wait_ready(jellyfin_url, timeout_seconds=wait_timeout):
            raise RuntimeError(f"Jellyfin not reachable at {jellyfin_url} within {wait_timeout}s")

        # Use the SAME auth service as the compose preflight.
        from .cli.jellyfin_controller_auth_service import JellyfinBootstrapAuthService

        auth_service = JellyfinBootstrapAuthService(
            http_request=_http,
            info=info,
            warn=info,
            fail=lambda msg: (_ for _ in ()).throw(RuntimeError(msg)),
        )

        # Step 1: Complete wizard if needed.
        auth_service.startup_wizard_if_needed(
            jellyfin_url,
            username=admin_username,
            password=admin_password,
        )

        # Step 2: Authenticate — try stack admin credentials first (the user we
        # just created in the wizard), then fall back to startup-user resolution
        # which uses /Startup/FirstUser endpoints that are unreliable post-wizard.
        auth_result, _, _ = auth_service.authenticate_with_credentials(
            jellyfin_url, admin_username, admin_password,
        )
        if auth_result is None:
            # Fallback: try empty password (Jellyfin default before password set).
            info("Jellyfin: stack admin auth failed, trying empty password fallback")
            auth_result, _, _ = auth_service.authenticate_with_credentials(
                jellyfin_url, admin_username, "",
            )
            if auth_result:
                auth_result["password_used"] = ""

        if auth_result is None:
            # Last resort: try startup-user endpoint resolution.
            info("Jellyfin: direct auth failed, trying startup-user resolution")
            auth_result = auth_service.try_authenticate_startup_user(
                jellyfin_url, admin_password,
            )

        if auth_result is None:
            raise RuntimeError("Jellyfin: could not authenticate after wizard completion")

        access_token = str(auth_result.get("token", ""))
        user_id = str(auth_result.get("user_id", ""))
        password_used = str(auth_result.get("password_used", admin_password))
        authenticated_username = str(auth_result.get("username", admin_username))

        # Ensure wizard is marked complete — if /Startup/User returned 500,
        # the wizard never reached /Startup/Complete even though we can auth.
        status_check, info_data, _ = _http(jellyfin_url, "/System/Info/Public")
        if status_check == 200 and isinstance(info_data, dict):
            if not info_data.get("StartupWizardCompleted"):
                info("Jellyfin: wizard not marked complete, completing now")
                _http(jellyfin_url, "/Startup/Configuration", method="POST", payload={
                    "ServerName": "media-stack", "UICulture": "en-US",
                    "MetadataCountryCode": "US", "PreferredMetadataLanguage": "en",
                })
                _http(jellyfin_url, "/Startup/RemoteAccess", method="POST", payload={
                    "EnableRemoteAccess": True, "EnableAutomaticPortMapping": False,
                })
                _http(jellyfin_url, "/Startup/Complete", method="POST")
                # Verify it took effect.
                for _ in range(10):
                    s, d, _ = _http(jellyfin_url, "/System/Info/Public")
                    if s == 200 and isinstance(d, dict) and d.get("StartupWizardCompleted"):
                        info("Jellyfin: wizard now marked complete")
                        break
                    time.sleep(2)

        # Step 3: Rename user if authenticated as a different username (e.g.
        # Jellyfin auto-created "MyJellyfinUser" when /Startup/User failed).
        if authenticated_username != admin_username:
            info(f"Jellyfin: renaming user '{authenticated_username}' to '{admin_username}'")
            auth_service.rename_user(jellyfin_url, access_token, user_id, admin_username)

        # Step 4: Rotate password if authenticated with empty/different password.
        if password_used != admin_password:
            info("Jellyfin: rotating password to stack admin credentials")
            try:
                auth_service.update_user_password(
                    jellyfin_url, access_token, user_id, password_used, admin_password,
                )
            except RuntimeError as exc:
                info(f"Jellyfin: password rotation returned error ({exc}), verifying auth")
            # Re-authenticate with the target credentials.
            auth_result, _, _ = auth_service.authenticate_with_credentials(
                jellyfin_url, admin_username, admin_password,
            )
            if auth_result is None:
                # Password rotation failed and admin password doesn't work.
                # Continue with the original token we already have.
                info("Jellyfin: password rotation did not take effect, continuing with current session")
            else:
                access_token = str(auth_result.get("token", ""))
                user_id = str(auth_result.get("user_id", ""))

        info(f"Jellyfin: authenticated as user_id={user_id}")

        # Step 4: Create or find API key.
        auth_header = {"X-Emby-Token": access_token}
        _, keys_data, _ = _http(jellyfin_url, "/Auth/Keys", headers=auth_header)
        existing_key = ""
        if isinstance(keys_data, dict):
            for item in keys_data.get("Items", []):
                if isinstance(item, dict) and item.get("AppName") == api_key_name:
                    existing_key = item.get("AccessToken", "")
                    break

        if existing_key:
            info(f"Jellyfin: API key already exists for '{api_key_name}'")
            return {"JELLYFIN_API_KEY": existing_key, "JELLYFIN_USER_ID": user_id}

        # Create new API key.
        _http(jellyfin_url, f"/Auth/Keys?app={api_key_name}", method="POST", headers=auth_header)

        # Re-fetch to get the key value.
        _, keys_data, _ = _http(jellyfin_url, "/Auth/Keys", headers=auth_header)
        api_key = ""
        if isinstance(keys_data, dict):
            for item in keys_data.get("Items", []):
                if isinstance(item, dict) and item.get("AppName") == api_key_name:
                    api_key = item.get("AccessToken", "")
                    break

        if not api_key:
            raise RuntimeError("Jellyfin: API key created but could not be retrieved")

        info(f"Jellyfin: API key created for '{api_key_name}'")
        return {"JELLYFIN_API_KEY": api_key, "JELLYFIN_USER_ID": user_id}


_instance = JellyfinHttpPreflight()
run_preflight = _instance.run_preflight
_http = _instance._http
_wait_ready = _instance._wait_ready
