"""Ratchet: Envoy base template stays in sync between the compose
and Kubernetes deploy paths.

Two parallel sources of truth — that's the bug class. The compose
deploy reads ``config/defaults/compose/envoy.runtime.base.yaml``
directly. The K8s deploy reads from a ConfigMap that's *inlined*
inside ``deploy/k8s/base/edge/envoy.yaml``. Both files have a comment
saying "keep in sync"; nothing actually enforces it. Drift means a
config change (XFF, access logs, http filters, anything) that lands
in only one path silently breaks the other.

Concrete prior incident (v1.0.252):
  * The compose template gained ``use_remote_address: true`` +
    ``xff_num_trusted_hops`` + a JSON access_log filter.
  * The K8s ConfigMap inline copy did NOT get the change.
  * Operator (this user) reported "I still only see 10.x.x.x"
    because the K8s envoy was running off the stale template.
  * Half a day to find that the K8s manifest carried a parallel copy.

This ratchet parses both, normalises into Python dicts, and asserts
they're structurally equal. Any new field on either side requires a
matching update on the other side, in the same PR, before this test
will pass.

Whitespace / quoting / key order differences don't matter — we
compare the parsed YAML representation. So formatting is free; it's
only the *content* that's locked.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

import yaml  # noqa: E402

COMPOSE_TEMPLATE = ROOT / "config" / "defaults" / "compose" / "envoy.runtime.base.yaml"
K8S_MANIFEST = ROOT / "deploy" / "k8s" / "base" / "edge" / "envoy.yaml"

# The K8s manifest is a multi-doc YAML stream (ConfigMap, PVC,
# Deployment, Service). Find the ConfigMap whose data carries the
# template; both env (default + custom-named) need the same content.
_CONFIGMAP_DATA_KEY = "envoy.runtime.base.yaml"


# Paths that are *intentionally* different between deploys. Keep
# this list short and well-justified; every entry is a place where
# the ratchet's "templates must match" guarantee weakens.
ALLOWED_DRIFT_PATHS: dict[str, str] = {
    # Compose binds Envoy as root on :80; K8s deployment runs as
    # non-root and binds to :8880 (the K8s service maps :80→:8880).
    ".static_resources.listeners[0].address.socket_address.port_value":
        "K8s envoy runs non-root; Service maps :80→:8880",
}


class EnvoyTemplateParityRatchet(unittest.TestCase):
    def test_compose_template_exists(self) -> None:
        self.assertTrue(
            COMPOSE_TEMPLATE.is_file(),
            f"Expected {COMPOSE_TEMPLATE.relative_to(ROOT)} to exist; "
            f"the deploy paths anchor on it.",
        )

    def test_k8s_manifest_exists(self) -> None:
        self.assertTrue(
            K8S_MANIFEST.is_file(),
            f"Expected {K8S_MANIFEST.relative_to(ROOT)} to exist; "
            f"this is the K8s parallel copy of the compose template.",
        )

    def test_k8s_configmap_inlines_the_compose_template_verbatim(self) -> None:
        compose_text = COMPOSE_TEMPLATE.read_text(encoding="utf-8")
        compose_doc = yaml.safe_load(compose_text)

        # Walk the K8s multi-doc stream looking for the ConfigMap.
        k8s_stream = list(yaml.safe_load_all(K8S_MANIFEST.read_text(encoding="utf-8")))
        cm_docs = [
            d for d in k8s_stream
            if isinstance(d, dict)
            and d.get("kind") == "ConfigMap"
            and isinstance((d.get("data") or {}).get(_CONFIGMAP_DATA_KEY), str)
        ]
        self.assertEqual(
            len(cm_docs), 1,
            f"Expected exactly one ConfigMap with data.{_CONFIGMAP_DATA_KEY} "
            f"in {K8S_MANIFEST.relative_to(ROOT)}; got {len(cm_docs)}.",
        )

        k8s_inline_text = cm_docs[0]["data"][_CONFIGMAP_DATA_KEY]
        k8s_inline_doc = yaml.safe_load(k8s_inline_text)

        diff = _summarise_doc_diff(compose_doc, k8s_inline_doc)
        # Filter out allowed-drift paths (legitimate per-deploy
        # differences — see ALLOWED_DRIFT_PATHS for justifications).
        unallowed = [
            d for d in diff
            if not any(d.startswith(allowed) for allowed in ALLOWED_DRIFT_PATHS)
        ]
        if unallowed:
            self.fail(
                "Envoy templates have drifted between compose and K8s.\n"
                f"  compose:  {COMPOSE_TEMPLATE.relative_to(ROOT)}\n"
                f"  k8s:      {K8S_MANIFEST.relative_to(ROOT)}\n"
                f"           (ConfigMap.data.{_CONFIGMAP_DATA_KEY})\n\n"
                f"Drifted paths:\n  - " + "\n  - ".join(unallowed[:20]) + (
                    f"\n  ...({len(unallowed) - 20} more)"
                    if len(unallowed) > 20 else ""
                ) + "\n\n"
                "Apply the same change to BOTH files in the same PR. "
                "If the change is genuinely K8s-only or compose-only, "
                "add the path to ALLOWED_DRIFT_PATHS in this test "
                "with a one-line justification."
            )


def _summarise_doc_diff(a, b, path: str = "") -> list[str]:
    """Recursive structural diff of two YAML-parsed objects. Returns
    a flat list of dotted paths where they differ. Used only for the
    failure message; correct test runs short-circuit on equality."""
    out: list[str] = []
    if type(a) is not type(b):
        out.append(f"{path or '<root>'}: type mismatch ({type(a).__name__} vs {type(b).__name__})")
        return out
    if isinstance(a, dict):
        all_keys = set(a.keys()) | set(b.keys())
        for k in sorted(all_keys):
            if k not in a:
                out.append(f"{path}.{k}: missing on compose side")
                continue
            if k not in b:
                out.append(f"{path}.{k}: missing on k8s side")
                continue
            out.extend(_summarise_doc_diff(a[k], b[k], f"{path}.{k}"))
        return out
    if isinstance(a, list):
        if len(a) != len(b):
            out.append(f"{path}: list length {len(a)} vs {len(b)}")
            # Continue diffing common-prefix items.
        for i, (av, bv) in enumerate(zip(a, b)):
            out.extend(_summarise_doc_diff(av, bv, f"{path}[{i}]"))
        return out
    if a != b:
        # Truncate scalars so the diff doesn't dump a whole Lua script.
        sa, sb = str(a)[:60], str(b)[:60]
        out.append(f"{path}: {sa!r} vs {sb!r}")
    return out


if __name__ == "__main__":
    unittest.main()
