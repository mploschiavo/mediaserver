"""L33 — meta-ratchet for the post-install promises registry.

The registry (``contracts/promises.yaml``) is the source of truth for
"what works out-of-the-box after a fresh install." This ratchet keeps
the registry consistent with the rest of the codebase:

  - Every promise's ``ensured_by`` MUST resolve to a real contract job.
  - Every contract job's handler MUST be importable.
  - Every ``ensure-*`` job in contracts/services/*.yaml SHOULD be
    referenced by at least one promise (orphan adapters = a quiet
    suggestion something stopped being needed; warning, not failure).

This is a CATEGORY-level ratchet — it doesn't verify any single
feature, it verifies that the FRAMEWORK around features is intact.
The fresh-install acceptance script (bin/verify-fresh-install.sh)
verifies the actual runtime promises by hitting each probe.

Why this exists
---------------
Without this, a developer can ship a new ensure-* adapter, forget to
register the contract job, and the bootstrap silently never fires the
ensure step. Tests pass (the function exists), the controller boots
clean, and the feature only "works" until ``compose down -v`` wipes
the state — exactly the failure pattern that produced the registry.
"""

from __future__ import annotations

import importlib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load_promises() -> dict:
    import yaml
    path = ROOT / "contracts" / "promises.yaml"
    if not path.is_file():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _load_contract_jobs() -> dict[str, dict]:
    """Return ``{job_name: {handler, phase, ...}}`` from every
    contracts/services/*.yaml file.

    Includes both ``plugin.jobs.<name>`` entries AND the singular
    ``plugin.post_setup_handler`` / ``plugin.preflight_handler``
    entries — those run through the same bootstrap pipeline as jobs
    do, just via the ``container_post_setup_handlers`` /
    ``container_preflight_handlers`` lists rather than the job DAG.
    From the promises-registry's point of view they ARE jobs:
    something-with-a-handler that bootstrap fires to make a promise
    true. Treating them as second-class would force every promise
    backed by a preflight to fake an ``ensured_by`` pointing at an
    unrelated job, which defeats the traceability the registry
    exists for.
    """
    import yaml
    out: dict[str, dict] = {}
    svc_dir = ROOT / "contracts" / "services"
    for yml in sorted(svc_dir.glob("*.yaml")):
        if yml.name.startswith("_"):
            continue
        try:
            doc = yaml.safe_load(yml.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        plugin = doc.get("plugin") or {}
        for job_name, job_def in (plugin.get("jobs") or {}).items():
            if isinstance(job_def, dict):
                out[job_name] = job_def
        for key in ("post_setup_handler", "preflight_handler"):
            entry = plugin.get(key)
            if isinstance(entry, dict) and entry.get("handler"):
                synthetic = f"{entry.get('name') or yml.stem}-{key.split('_')[0]}"
                out[synthetic] = entry
    return out


def _resolve_handler(handler_path: str):
    """Mirror of cli/commands/job_framework.py:_resolve_handler so this
    test runs without spinning up the JobRunner."""
    if not handler_path:
        return None
    if ":" in handler_path:
        mod_path, func_name = handler_path.rsplit(":", 1)
    elif "." in handler_path:
        mod_path, func_name = handler_path.rsplit(".", 1)
    else:
        return None
    try:
        mod = importlib.import_module(mod_path)
        return getattr(mod, func_name, None)
    except Exception:
        return None


class PromisesRegistryConsistent(unittest.TestCase):

    def setUp(self):
        try:
            import yaml  # noqa: F401
        except ImportError:
            self.skipTest("PyYAML not installed")
        self.promises = _load_promises()
        self.jobs = _load_contract_jobs()

    def test_registry_exists_and_has_promises(self):
        self.assertTrue(
            self.promises,
            "contracts/promises.yaml is missing or empty. Without it "
            "the fresh-install acceptance script has nothing to "
            "verify and the meta-ratchet can't enforce convention.",
        )
        promises = self.promises.get("promises") or []
        self.assertGreater(
            len(promises), 0,
            "promises.yaml has no ``promises:`` entries.",
        )

    def test_every_promise_has_required_fields(self):
        promises = self.promises.get("promises") or []
        required = {"id", "description", "ensured_by", "probe"}
        for p in promises:
            self.assertIsInstance(p, dict, f"promise must be a mapping: {p!r}")
            missing = required - set(p.keys())
            self.assertFalse(
                missing,
                f"promise {p.get('id', '<no id>')!r} missing fields: {missing}",
            )
            probe = p.get("probe") or {}
            self.assertIn(
                probe.get("type"),
                {"http_json", "http_text", "http_status",
                 "file_json", "file_text"},
                f"promise {p['id']!r}: unknown probe type "
                f"{probe.get('type')!r}",
            )
            self.assertIn(
                "assert", probe,
                f"promise {p['id']!r}: probe missing ``assert`` expr",
            )

    def test_every_promise_references_a_real_job(self):
        promises = self.promises.get("promises") or []
        bad: list[str] = []
        for p in promises:
            job_name = p.get("ensured_by")
            if job_name not in self.jobs:
                bad.append(
                    f"  {p.get('id')}: ensured_by={job_name!r} not "
                    f"found in any contracts/services/*.yaml"
                )
        self.assertFalse(
            bad,
            "Promises point at jobs that don't exist:\n" + "\n".join(bad),
        )

    def test_every_referenced_handler_is_importable(self):
        promises = self.promises.get("promises") or []
        bad: list[str] = []
        for p in promises:
            job = self.jobs.get(p.get("ensured_by")) or {}
            handler_path = str(job.get("handler") or "")
            if not handler_path:
                bad.append(f"  {p.get('id')}: job has no handler")
                continue
            fn = _resolve_handler(handler_path)
            if fn is None:
                bad.append(
                    f"  {p.get('id')}: handler {handler_path!r} not "
                    "importable (module or function missing)"
                )
        self.assertFalse(
            bad,
            "Promise handlers don't resolve:\n" + "\n".join(bad),
        )

    def test_no_orphan_ensure_jobs(self):
        """Every ``ensure-*`` job in contracts SHOULD appear as an
        ``ensured_by`` somewhere. Orphans aren't fatal but they
        likely indicate the registry drifted away from the real
        bootstrap surface."""
        promises = self.promises.get("promises") or []
        referenced = {p.get("ensured_by") for p in promises}
        orphans = [
            name for name in self.jobs
            if name.startswith("ensure-") and name not in referenced
        ]
        self.assertFalse(
            orphans,
            "ensure-* jobs not referenced by any promise — either "
            "add a promise that probes them or delete the orphan job:\n"
            + "\n".join(f"  - {o}" for o in sorted(orphans)),
        )

    def test_promise_ids_are_unique(self):
        promises = self.promises.get("promises") or []
        ids = [p.get("id") for p in promises if p.get("id")]
        dupes = [i for i in set(ids) if ids.count(i) > 1]
        self.assertFalse(
            dupes,
            f"duplicate promise ids in registry: {dupes}",
        )


if __name__ == "__main__":
    unittest.main()
