"""API key format readers and writers — shared by registry.py and admin.py.

Each format (xml, ini, yaml, json, sqlite) has a read and optional write
function. To support a new format, add functions here and register them
in READERS / WRITERS.
"""

from __future__ import annotations

import json
import re
from pathlib import Path


class KeyFormatService:
    """Read and write API keys in various config file formats."""

    # -----------------------------------------------------------------------
    # Readers — return key string or "" on failure
    # -----------------------------------------------------------------------

    def read_xml(self, path: Path) -> str:
        if not path.is_file():
            return ""
        m = re.search(r"<ApiKey>([^<]+)</ApiKey>", path.read_text(encoding="utf-8"))
        return m.group(1).strip() if m else ""

    def read_ini(self, path: Path) -> str:
        if not path.is_file():
            return ""
        m = re.search(r"^\s*api_key\s*=\s*(\S+)", path.read_text(encoding="utf-8"), re.MULTILINE)
        return m.group(1).strip() if m else ""

    def read_yaml(self, path: Path) -> str:
        if not path.is_file():
            return ""
        m = re.search(r"^\s*apikey:\s*['\"]?(\S+?)['\"]?\s*$", path.read_text(encoding="utf-8"), re.MULTILINE)
        return m.group(1).strip() if m else ""

    def read_json(self, path: Path) -> str:
        if not path.is_file():
            return ""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return str((data.get("main") or {}).get("apiKey", "")).strip()
        except Exception:
            return ""

    def read_sqlite(self, path: Path) -> str:
        if not path.is_file():
            return ""
        import sqlite3
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            cur = conn.cursor()
            cur.execute("SELECT AccessToken FROM ApiKeys ORDER BY Id DESC LIMIT 1")
            row = cur.fetchone()
            conn.close()
            return str(row[0]).strip() if row and row[0] else ""
        except Exception:
            return ""

    # -----------------------------------------------------------------------
    # Writers — modify key in-place
    # -----------------------------------------------------------------------

    def write_xml(self, path: Path, new_key: str) -> None:
        content = path.read_text(encoding="utf-8")
        content = re.sub(r"<ApiKey>[^<]*</ApiKey>", f"<ApiKey>{new_key}</ApiKey>", content)
        path.write_text(content, encoding="utf-8")

    def write_ini(self, path: Path, new_key: str) -> None:
        content = path.read_text(encoding="utf-8")
        content = re.sub(r"^api_key\s*=\s*.*$", f"api_key = {new_key}", content, count=1, flags=re.MULTILINE)
        path.write_text(content, encoding="utf-8")

    def write_yaml(self, path: Path, new_key: str) -> None:
        import yaml
        with open(path) as f:
            cfg = yaml.safe_load(f) or {}
        cfg.setdefault("auth", {})["apikey"] = new_key
        with open(path, "w") as f:
            yaml.dump(cfg, f, default_flow_style=False)

    def write_json(self, path: Path, new_key: str) -> None:
        data = json.loads(path.read_text(encoding="utf-8"))
        data.setdefault("main", {})["apiKey"] = new_key
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")


_instance = KeyFormatService()

# Backward compat — callers use module-level functions
read_xml = _instance.read_xml
read_ini = _instance.read_ini
read_yaml = _instance.read_yaml
read_json = _instance.read_json
read_sqlite = _instance.read_sqlite
write_xml = _instance.write_xml
write_ini = _instance.write_ini
write_yaml = _instance.write_yaml
write_json = _instance.write_json

# ---------------------------------------------------------------------------
# Format registries
# ---------------------------------------------------------------------------

READERS = {
    "xml": read_xml,
    "ini": read_ini,
    "yaml": read_yaml,
    "json": read_json,
    "sqlite": read_sqlite,
}

WRITERS = {
    "xml": write_xml,
    "ini": write_ini,
    "yaml": write_yaml,
    "json": write_json,
    # sqlite keys are rotated via API, not file — handled in admin.py
}
