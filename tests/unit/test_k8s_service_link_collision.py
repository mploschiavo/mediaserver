"""K8s service-link env collision ratchet.

Kubernetes auto-injects per-Service environment variables into every
container in the namespace, named ``<SERVICENAME_UPPER>_SERVICE_HOST``,
``<SERVICENAME_UPPER>_SERVICE_PORT``, and
``<SERVICENAME_UPPER>_PORT_<PORT>_TCP[_ADDR|_PORT|_PROTO]``.

For most apps this is harmless. For apps whose own configuration is
driven by environment variables with the same prefix as their Service
name, K8s' auto-injected vars collide with the app's config-from-env
mechanism. Concrete failure caught during the v1.0.158 K8s fresh
install:

    Authelia 4.38 reads ``AUTHELIA_*`` env vars as config keys.
    K8s injects ``AUTHELIA_SERVICE_HOST`` and ``AUTHELIA_SERVICE_PORT``
    because there's a Service named ``authelia``. Authelia maps those
    to the deprecated ``server.host`` / ``server.port`` keys, which
    conflict with the modern ``server.address`` set in our config:

        error occurred performing deprecation mapping for keys
        'server.host', 'server.port', and 'server.path' to new key
        server.address: the new key already exists with value
        'tcp://0.0.0.0:9091' but the deprecated keys and the new
        key can't both be configured

    Authelia refused to start; CrashLoopBackOff every 30s. The fix is
    ``enableServiceLinks: false`` on the pod spec — K8s-only field
    (compose has no equivalent because it doesn't auto-inject these
    vars in the first place).

This ratchet enforces: any Deployment whose pod runs an app whose name
matches the app's env-var prefix MUST set ``enableServiceLinks: false``
when there's a Service of the same name in the namespace.

Why a ratchet instead of just trusting reviewers: this bug class is
silent. The manifest validates, the schema is fine, kubectl apply
succeeds. The pod just CrashLoops with a config error that doesn't
look like a service-link issue at all. The next maintainer who adds
a service named ``foobar`` will not remember this is a thing.

Apps known to have this collision pattern:

    authelia      AUTHELIA_*    (config keys map from env)
    authentik     AUTHENTIK_*   (AUTHENTIK_POSTGRESQL__* etc. are real config)

Apps that are safe (their own env vars don't collide with K8s patterns):

    sonarr/radarr/lidarr/readarr  - use SONARR_API_KEY etc., no
                                    SONARR_PORT_* config
    jellyfin                      - uses JELLYFIN_PublishedServerUrl
                                    only, no host/port from env
    qbittorrent / sabnzbd         - no config-from-env mechanism

When a new app gets added that DOES use env-var-driven config and has
a Service of the same name, this ratchet will fail until either:

    1. The Deployment sets ``enableServiceLinks: false`` (preferred), or
    2. The app's name is added to the SERVICE_LINK_SAFE_APPS allowlist
       below with a one-line explanation.
"""

from __future__ import annotations

import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
K8S_DIR = ROOT / "k8s"


# Apps whose Service name matches an env-var prefix the app itself uses
# for configuration. These MUST set ``enableServiceLinks: false`` on
# their pod spec, or K8s' auto-injected service-link env vars will
# clobber the app's config-from-env mechanism and the pod will
# CrashLoop with a non-obvious error.
APPS_NEEDING_SERVICE_LINK_DISABLE = {
    "authelia",         # AUTHELIA_SERVICE_HOST/PORT → deprecated server.host/port
    "authentik",        # AUTHENTIK_POSTGRESQL__* etc. — see auth-authentik.yaml
    "authentik-worker",
}


def _load_k8s_docs() -> list[dict]:
    """Load every YAML doc under k8s/, not just the default
    kustomization. Optional manifests like auth-authentik.yaml live
    outside the default kustomization (they're profile-gated) but
    still need to satisfy the service-link contract — a user who
    enables the authentik profile expects it to come up cleanly.

    Phase 5 (ADR-0001) regrouped manifests under k8s/base/<concern>/,
    so the scan walks recursively and skips overlay directories
    (profiles/, all/) that re-aggregate the base files."""
    docs: list[dict] = []
    if not K8S_DIR.is_dir():
        return docs
    base_dir = K8S_DIR / "base"
    scan_root = base_dir if base_dir.is_dir() else K8S_DIR
    for path in sorted(scan_root.rglob("*.yaml")):
        if path.name == "kustomization.yaml":
            continue
        try:
            for doc in yaml.safe_load_all(path.read_text(encoding="utf-8")):
                if isinstance(doc, dict):
                    doc["__source"] = path.name
                    docs.append(doc)
        except Exception:
            continue
    return docs


def _services_by_name(docs: list[dict]) -> dict[str, dict]:
    return {
        d["metadata"]["name"]: d
        for d in docs
        if d.get("kind") == "Service"
        and isinstance(d.get("metadata"), dict)
        and d["metadata"].get("name")
    }


def _deployments(docs: list[dict]) -> list[dict]:
    return [
        d for d in docs
        if d.get("kind") in ("Deployment", "StatefulSet", "DaemonSet")
    ]


class ServiceLinkCollisionRatchet(unittest.TestCase):

    def setUp(self):
        self.docs = _load_k8s_docs()
        self.services = _services_by_name(self.docs)
        self.workloads = _deployments(self.docs)

    def test_known_collision_apps_disable_service_links(self):
        """Apps whose env-var prefix collides with their Service name
        MUST set ``enableServiceLinks: false`` on the pod spec."""
        bad: list[str] = []
        for w in self.workloads:
            name = (w.get("metadata") or {}).get("name") or ""
            if name not in APPS_NEEDING_SERVICE_LINK_DISABLE:
                continue
            if name not in self.services:
                # No matching Service → K8s won't inject the env vars,
                # so the field isn't required for THIS app even though
                # it's on the watchlist.
                continue
            pod_spec = (
                ((w.get("spec") or {}).get("template") or {}).get("spec") or {}
            )
            if pod_spec.get("enableServiceLinks") is not False:
                bad.append(
                    f"  {w['__source']}: workload '{name}' has a Service "
                    "of the same name but does not set "
                    "``enableServiceLinks: false`` on its pod spec. "
                    "K8s will inject service-link env vars that collide "
                    "with the app's config-from-env mechanism and the "
                    "pod will CrashLoop with a non-obvious error. See "
                    "this test's docstring for the v1.0.158 Authelia "
                    "incident details."
                )
        self.assertFalse(
            bad,
            "Service-link env collision risk:\n" + "\n".join(bad),
        )

    def test_allowlist_entries_still_have_a_service(self):
        """Sanity check: the allowlist isn't carrying entries for
        apps that no longer have a Service. If an app was removed,
        the allowlist entry is dead code and should be cleaned up."""
        orphans = [
            name for name in APPS_NEEDING_SERVICE_LINK_DISABLE
            if name not in self.services
            and not any(
                (w.get("metadata") or {}).get("name") == name
                for w in self.workloads
            )
        ]
        self.assertFalse(
            orphans,
            "APPS_NEEDING_SERVICE_LINK_DISABLE has entries for apps "
            "with neither a workload nor a Service — clean these up:\n"
            + "\n".join(f"  - {o}" for o in orphans),
        )


if __name__ == "__main__":
    unittest.main()
