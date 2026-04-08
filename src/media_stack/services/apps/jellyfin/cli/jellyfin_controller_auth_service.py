from __future__ import annotations

import time
from urllib import parse


class JellyfinBootstrapAuthService:
    def __init__(self, *, http_request, info, warn, fail) -> None:
        self.http_request = http_request
        self.info = info
        self.warn = warn
        self.fail = fail

    def wait_for_jellyfin(self, base_url: str, timeout_seconds: int = 180):
        start = time.time()
        while time.time() - start < timeout_seconds:
            status, data, _ = self.http_request(base_url, "/System/Info/Public", timeout=8)
            if status == 200 and isinstance(data, dict):
                return data
            time.sleep(2)
        self.fail(f"Timed out waiting for Jellyfin at {base_url}/System/Info/Public")

    def can_authenticate_jellyfin(self, base_url: str, username: str, password: str) -> bool:
        headers = {
            "X-Emby-Authorization": (
                'MediaBrowser Client="media-stack-controller", Device="media-stack-controller", '
                'DeviceId="media-stack-controller", Version="1.0.0"'
            )
        }
        payload = {"Username": username, "Pw": password}
        status, data, _ = self.http_request(
            base_url,
            "/Users/AuthenticateByName",
            method="POST",
            payload=payload,
            headers=headers,
        )
        return bool(
            status == 200 and isinstance(data, dict) and str(data.get("AccessToken") or "").strip()
        )

    def authenticate_with_credentials(self, base_url: str, username: str, password: str):
        headers = {
            "X-Emby-Authorization": (
                'MediaBrowser Client="media-stack-controller", Device="media-stack-controller", '
                'DeviceId="media-stack-controller", Version="1.0.0"'
            )
        }
        payload = {"Username": username, "Pw": password}
        status, data, body = self.http_request(
            base_url,
            "/Users/AuthenticateByName",
            method="POST",
            payload=payload,
            headers=headers,
        )
        if status != 200 or not isinstance(data, dict):
            return None, status, body
        token = str(data.get("AccessToken") or "").strip()
        user = data.get("User") or {}
        user_id = str(user.get("Id") or "").strip()
        if not token:
            return None, status, "Authentication succeeded without access token."
        return {"token": token, "user_id": user_id, "username": str(username)}, status, body

    def resolve_startup_username(self, base_url: str) -> str:
        for path in ("/Startup/FirstUser", "/Startup/User"):
            status, data, _ = self.http_request(base_url, path)
            if status == 200 and isinstance(data, dict):
                name = str(data.get("Name") or "").strip()
                if name:
                    return name
        return ""

    def try_authenticate_startup_user(self, base_url: str, preferred_password: str):
        startup_username = self.resolve_startup_username(base_url)
        if not startup_username:
            return None
        attempted_passwords = []
        for candidate in (preferred_password, ""):
            if candidate in attempted_passwords:
                continue
            attempted_passwords.append(candidate)
            auth, _, _ = self.authenticate_with_credentials(base_url, startup_username, candidate)
            if auth:
                auth["password_used"] = candidate
                return auth
        return None

    def update_user_password(
        self,
        base_url: str,
        session_token: str,
        user_id: str,
        current_pw: str,
        new_pw: str,
    ) -> None:
        headers = {"X-Emby-Authorization": f'MediaBrowser Token="{session_token}"'}
        status, _, body = self.http_request(
            base_url,
            f"/Users/Password?userId={parse.quote(user_id, safe='')}",
            method="POST",
            payload={"CurrentPw": current_pw, "NewPw": new_pw},
            headers=headers,
        )
        if status not in (200, 201, 202, 204):
            raise RuntimeError(f"Jellyfin password update failed (HTTP {status}): {body}")

    def startup_wizard_if_needed(self, base_url: str, username: str, password: str) -> None:
        info_public = self.wait_for_jellyfin(base_url)
        if bool(info_public.get("StartupWizardCompleted", False)):
            self.info("Jellyfin startup wizard already completed.")
            return

        self.info("Jellyfin startup wizard not completed; applying automated first-run setup.")
        config_payload = {
            "ServerName": "media-stack",
            "UICulture": "en-US",
            "MetadataCountryCode": "US",
            "PreferredMetadataLanguage": "en",
        }
        # Retry the startup config step — Jellyfin may return 503 while its
        # internal services are still initializing even after /System/Info/Public
        # returns 200.  Give it up to ~30s to become ready.
        config_ok = False
        for attempt in range(8):
            status, _, body = self.http_request(
                base_url,
                "/Startup/Configuration",
                method="POST",
                payload=config_payload,
            )
            if status in (200, 201, 202, 204):
                config_ok = True
                break
            if status in (500, 502, 503) and attempt < 7:
                self.info(f"Jellyfin startup config returned HTTP {status}, retrying in {2 + attempt}s...")
                time.sleep(2 + attempt)
            else:
                self.warn(f"Jellyfin startup config step returned HTTP {status}: {body}")
                break

        # Retry user creation — Jellyfin may return 500 if internal DB
        # init is still settling after the configuration step.
        user_created = False
        for attempt in range(5):
            status, _, body = self.http_request(
                base_url,
                "/Startup/User",
                method="POST",
                payload={"Name": username, "Password": password},
            )
            if status in (200, 201, 202, 204):
                user_created = True
                break
            if attempt < 4:
                time.sleep(3)
        if not user_created:
            if self.can_authenticate_jellyfin(base_url, username, password):
                self.warn(
                    f"Jellyfin startup user step returned HTTP {status}, but admin login works; "
                    "continuing startup bootstrap."
                )
            else:
                self.warn(
                    f"Jellyfin startup user setup failed (HTTP {status}) and stack-admin auth did not "
                    "succeed. Continuing with API key discovery/recovery flow."
                )
                return

        status, _, body = self.http_request(
            base_url,
            "/Startup/RemoteAccess",
            method="POST",
            payload={"EnableRemoteAccess": True, "EnableAutomaticPortMapping": False},
        )
        if status not in (200, 201, 202, 204):
            self.warn(f"Jellyfin startup remote-access step returned HTTP {status}: {body}")

        status, _, body = self.http_request(base_url, "/Startup/Complete", method="POST")
        if status not in (200, 201, 202, 204):
            self.warn(f"Jellyfin startup completion step returned HTTP {status}: {body}")

        for _ in range(30):
            info_public = self.wait_for_jellyfin(base_url, timeout_seconds=15)
            if bool(info_public.get("StartupWizardCompleted", False)):
                self.info("Jellyfin startup wizard completed successfully.")
                return
            time.sleep(1)

        if self.can_authenticate_jellyfin(base_url, username, password):
            self.warn(
                "Jellyfin startup wizard flag is still false, but admin authentication works. "
                "Proceeding with API key reconciliation."
            )
            return

        self.warn(
            "Jellyfin startup wizard still not completed after automation and stack-admin auth failed. "
            "Continuing with API key discovery/recovery flow."
        )

    def _authenticate_jellyfin(self, base_url: str, username: str, password: str):
        headers = {
            "X-Emby-Authorization": (
                'MediaBrowser Client="media-stack-controller", Device="media-stack-controller", '
                'DeviceId="media-stack-controller", Version="1.0.0"'
            )
        }
        payload = {"Username": username, "Pw": password}
        status, data, body = self.http_request(
            base_url,
            "/Users/AuthenticateByName",
            method="POST",
            payload=payload,
            headers=headers,
        )
        if status != 200 or not isinstance(data, dict):
            return None, None, status, body
        token = str(data.get("AccessToken") or "").strip()
        user = data.get("User") or {}
        user_id = str(user.get("Id") or "").strip()
        if not token:
            return None, None, status, "Authentication succeeded without access token."
        return token, user_id, status, body

    def authenticate_jellyfin(self, base_url: str, username: str, password: str):
        token, user_id, status, body = self._authenticate_jellyfin(base_url, username, password)
        if not token:
            self.fail(f"Jellyfin authentication failed (HTTP {status}): {body}")
        self.info("Jellyfin authentication succeeded with stack admin credentials.")
        return token, user_id

    def try_authenticate_jellyfin(self, base_url: str, username: str, password: str):
        token, user_id, status, _ = self._authenticate_jellyfin(base_url, username, password)
        if not token:
            self.warn(
                f"Jellyfin authentication with stack admin credentials failed (HTTP {status})."
            )
            return None
        self.info("Jellyfin authentication succeeded with stack admin credentials.")
        return token, user_id
