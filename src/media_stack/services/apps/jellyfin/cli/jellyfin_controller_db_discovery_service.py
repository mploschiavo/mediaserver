from __future__ import annotations


from media_stack.core.logging_utils import log_swallowed
import os
import sqlite3
import tempfile
from typing import Callable

from .jellyfin_controller_kube_service import run_cmd
import logging


class JellyfinControllerDbDiscoveryService:

    def discover_api_key_from_jellyfin_db(self, 
        kubectl: list[str],
        namespace: str,
        service_name: str,
        preferred_app_names: list[str],
        preferred_username: str,
        *,
        warn: Callable[[str], None],
    ) -> tuple[str, str]:
        pod_proc = run_cmd(
            kubectl
            + [
                "-n",
                namespace,
                "get",
                "pods",
                "-l",
                f"app={service_name}",
                "-o",
                "jsonpath={.items[0].metadata.name}",
            ],
            check=False,
        )
        pod_name = str(pod_proc.stdout or "").strip()
        if pod_proc.returncode != 0 or not pod_name:
            warn("Could not resolve Jellyfin pod for DB key discovery.")
            return "", ""

        fd, local_db = tempfile.mkstemp(prefix="jellyfin-db-", suffix=".sqlite")
        os.close(fd)
        try:
            cp_proc = run_cmd(
                kubectl
                + [
                    "-n",
                    namespace,
                    "cp",
                    f"{pod_name}:/config/data/jellyfin.db",
                    local_db,
                ],
                check=False,
            )
            if cp_proc.returncode != 0:
                warn("Failed copying jellyfin.db from pod for key discovery.")
                return "", ""

            con = sqlite3.connect(local_db)
            cur = con.cursor()
            preferred = [str(x).strip().lower() for x in preferred_app_names if str(x).strip()]
            discovered_key = ""
            for app_name in preferred:
                cur.execute(
                    "SELECT AccessToken FROM ApiKeys WHERE lower(Name)=? ORDER BY Id DESC LIMIT 1",
                    (app_name,),
                )
                row = cur.fetchone()
                if row and str(row[0] or "").strip():
                    discovered_key = str(row[0]).strip()
                    break
            if not discovered_key:
                cur.execute(
                    "SELECT AccessToken FROM ApiKeys WHERE AccessToken IS NOT NULL AND AccessToken != '' ORDER BY Id DESC LIMIT 1"
                )
                row = cur.fetchone()
                if row and str(row[0] or "").strip():
                    discovered_key = str(row[0]).strip()

            discovered_user_id = ""
            preferred_user = str(preferred_username or "").strip().lower()
            if preferred_user:
                cur.execute(
                    "SELECT Id FROM Users WHERE lower(Username)=? ORDER BY Id LIMIT 1",
                    (preferred_user,),
                )
                row = cur.fetchone()
                if row and str(row[0] or "").strip():
                    discovered_user_id = str(row[0]).strip()
            if not discovered_user_id:
                cur.execute("SELECT Id FROM Users ORDER BY Id LIMIT 1")
                row = cur.fetchone()
                if row and str(row[0] or "").strip():
                    discovered_user_id = str(row[0]).strip()

            con.close()
            return discovered_key, discovered_user_id
        except Exception as exc:
            warn(f"Jellyfin DB key discovery failed: {exc}")
            return "", ""
        finally:
            try:
                os.remove(local_db)
            except Exception as exc:
                log_swallowed(exc)


_instance = JellyfinControllerDbDiscoveryService()
discover_api_key_from_jellyfin_db = _instance.discover_api_key_from_jellyfin_db
