"""Static parity checks between the compose and K8s deployment
shapes.

The 2026-04-19 Jellyseerr-OIDC + Prowlarr-UrlBase incidents both
traced back to the same structural problem: compose went through
the dynamic controller-generated config path, K8s went through a
hand-written static ConfigMap. The two paths drifted. These tests
pin the invariants that keep them from drifting again.

Invariants enforced here:

1. Auth-config volume is shared between the Authelia service AND
   the controller service on BOTH platforms. Without this the
   controller generates config that Authelia never reads.

2. The controller's boot-time profile env var
   (``BOOTSTRAP_PROFILE_FILE``) is set on BOTH platforms, so the
   ``_run_boot_configure_auth`` hook's provider check doesn't
   silently return "" and skip OIDC generation.

3. The controller image tag pinned in the k8s kustomization
   matches the one pinned in docker-compose.yml. Drift here
   means "works on compose, breaks on K8s" bugs.

4. The Authelia ``storage.local.path`` seeded in the K8s ConfigMap
   points at the shared config volume so db.sqlite3 survives
   restart AND is reachable by the generator's secret-reuse code.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
_COMPOSE_FILE = ROOT / "docker" / "docker-compose.yml"
_K8S_DIR = ROOT / "k8s"
_K8S_AUTHELIA = _K8S_DIR / "auth-authelia.yaml"
_K8S_CONTROLLER = _K8S_DIR / "controller.yaml"
_K8S_KUSTOMIZATION = _K8S_DIR / "kustomization.yaml"
_K8S_SECRETS = _K8S_DIR / "secrets.example.yaml"

_AUTHELIA_CONFIG_PVC = "media-stack-config-authelia"
_CONTROLLER_IMAGE = "harbor.iomio.io/library/media-stack-controller"


def _split_yaml_docs(text: str) -> list[dict]:
    return [d for d in yaml.safe_load_all(text) if isinstance(d, dict)]


def _k8s_authelia_deployment() -> dict:
    for doc in _split_yaml_docs(_K8S_AUTHELIA.read_text(encoding="utf-8")):
        if doc.get("kind") == "Deployment" \
                and doc.get("metadata", {}).get("name") == "authelia":
            return doc
    raise AssertionError("authelia Deployment not found in k8s/auth-authelia.yaml")


def _k8s_controller_deployment() -> dict:
    for doc in _split_yaml_docs(_K8S_CONTROLLER.read_text(encoding="utf-8")):
        if doc.get("kind") == "Deployment" \
                and doc.get("metadata", {}).get("name") == "media-stack-controller":
            return doc
    raise AssertionError("controller Deployment not found in k8s/controller.yaml")


def _k8s_authelia_configmap() -> dict:
    for doc in _split_yaml_docs(_K8S_AUTHELIA.read_text(encoding="utf-8")):
        if doc.get("kind") == "ConfigMap" \
                and doc.get("metadata", {}).get("name") == "authelia-config":
            return doc
    raise AssertionError("authelia-config ConfigMap not found")


def _compose_services() -> dict:
    data = yaml.safe_load(_COMPOSE_FILE.read_text(encoding="utf-8")) or {}
    return data.get("services") or {}


def _pvc_claim_name(volume: dict) -> str:
    pvc = volume.get("persistentVolumeClaim") or {}
    return str(pvc.get("claimName", "")).strip()


class ComposeK8sAutheliaConfigShareTests(unittest.TestCase):
    """The core parity invariant: both the Authelia service and
    the controller service must mount the same Authelia config
    volume on both platforms. Without this the controller's
    ``_run_boot_configure_auth`` writes to a place Authelia
    never reads from, and OIDC / secret-preservation break."""

    def test_k8s_authelia_config_volume_is_shared_pvc(self):
        dep = _k8s_authelia_deployment()
        volumes = dep["spec"]["template"]["spec"].get("volumes") or []
        config_vol = next((v for v in volumes
                           if v.get("name") == "config"), None)
        self.assertIsNotNone(
            config_vol, "authelia has no 'config' volume")
        # The critical rule: the config volume MUST be a PVC,
        # NOT an emptyDir. emptyDir means the generator's output
        # is erased every pod restart — the exact fresh-install
        # crashloop we keep hitting.
        self.assertNotIn(
            "emptyDir", config_vol,
            "authelia /config is emptyDir — controller-generated "
            "config is lost on every pod restart. Switch to "
            "persistentVolumeClaim so the generator's output "
            "survives and Authelia reads what the controller wrote.",
        )
        self.assertEqual(
            _pvc_claim_name(config_vol), _AUTHELIA_CONFIG_PVC,
            "authelia /config must be the shared "
            f"{_AUTHELIA_CONFIG_PVC} PVC so the controller can "
            "write into it.",
        )

    def test_k8s_controller_mounts_authelia_config_pvc(self):
        dep = _k8s_controller_deployment()
        spec = dep["spec"]["template"]["spec"]
        containers = spec.get("containers") or []
        self.assertTrue(containers, "controller has no containers")
        mounts = containers[0].get("volumeMounts") or []
        auth_mounts = [m for m in mounts
                       if m.get("mountPath") == "/srv-config/authelia"]
        self.assertTrue(
            auth_mounts,
            "controller has no /srv-config/authelia mount — "
            "_run_boot_configure_auth will write to container-local "
            "disk and Authelia will never see it.",
        )
        volumes = spec.get("volumes") or []
        auth_vol_name = auth_mounts[0]["name"]
        vol = next((v for v in volumes if v.get("name") == auth_vol_name), None)
        self.assertIsNotNone(
            vol, f"controller mount references undefined volume "
            f"{auth_vol_name!r}",
        )
        self.assertEqual(
            _pvc_claim_name(vol), _AUTHELIA_CONFIG_PVC,
            f"controller /srv-config/authelia must bind "
            f"{_AUTHELIA_CONFIG_PVC} — the same PVC the Authelia "
            "pod uses — so the generator's write is what Authelia "
            "reads.",
        )

    def test_compose_authelia_and_controller_share_config_path(self):
        svcs = _compose_services()
        authelia = svcs.get("authelia") or {}
        controller = svcs.get("media-stack-controller") or {}
        # Compose mounts bind strings like
        # "${CONFIG_ROOT:-./config}/authelia:/config". The root
        # variable resolves to ./config on both sides, so authelia's
        # /config and the controller's /srv-config/authelia point
        # at the same host directory.
        auth_mounts = authelia.get("volumes") or []
        ctrl_mounts = controller.get("volumes") or []
        auth_config_src = next(
            (m.split(":", 1)[0] for m in auth_mounts
             if isinstance(m, str) and m.endswith(":/config")),
            "",
        )
        ctrl_srv_config_src = next(
            (m.split(":", 1)[0] for m in ctrl_mounts
             if isinstance(m, str) and (m.endswith(":/srv-config")
                                        or ":/srv-config:" in m)),
            "",
        )
        self.assertTrue(
            auth_config_src,
            "authelia compose service has no :/config mount",
        )
        self.assertTrue(
            ctrl_srv_config_src,
            "controller compose service has no :/srv-config mount",
        )
        # Authelia source ends in /authelia; controller source is
        # the parent. Strip trailing /authelia and compare.
        auth_parent = re.sub(r"/authelia/?$", "", auth_config_src)
        self.assertEqual(
            auth_parent.rstrip("/"),
            ctrl_srv_config_src.rstrip("/"),
            f"compose config-root mismatch: authelia reads from "
            f"{auth_config_src}, controller reads from "
            f"{ctrl_srv_config_src}. Both must share a root so the "
            f"generator's writes are visible to Authelia.",
        )


class ComposeK8sControllerEnvParityTests(unittest.TestCase):
    """Env vars the controller boot hooks depend on must be set
    on both platforms. A missing BOOTSTRAP_PROFILE_FILE silently
    no-ops configure-auth (profile.auth.provider read as '').
    A missing STACK_ADMIN_USERNAME breaks admin-bootstrap's
    identity resolution."""

    _REQUIRED_ENV_VARS = (
        "BOOTSTRAP_PROFILE_FILE",
        "STACK_ADMIN_USERNAME",
        "STACK_ADMIN_PASSWORD",
    )

    def _k8s_controller_env_keys(self) -> set[str]:
        dep = _k8s_controller_deployment()
        env = (dep["spec"]["template"]["spec"]
               .get("containers") or [{}])[0].get("env") or []
        return {str(e.get("name", "")).strip() for e in env
                if isinstance(e, dict)}

    def _compose_controller_env_keys(self) -> set[str]:
        svcs = _compose_services()
        env = (svcs.get("media-stack-controller") or {}).get("environment") or {}
        if isinstance(env, dict):
            return set(env.keys())
        # list form: "KEY=value" or "KEY"
        out: set[str] = set()
        for line in env:
            if isinstance(line, str):
                out.add(line.split("=", 1)[0])
        return out

    def test_required_env_vars_present_on_both(self):
        k8s_keys = self._k8s_controller_env_keys()
        compose_keys = self._compose_controller_env_keys()
        for name in self._REQUIRED_ENV_VARS:
            self.assertIn(
                name, k8s_keys,
                f"{name} missing from k8s/controller.yaml env — "
                "boot hooks that depend on it will silently skip.",
            )
            self.assertIn(
                name, compose_keys,
                f"{name} missing from docker-compose.yml "
                "media-stack-controller environment — same risk.",
            )


class ComposeK8sAdminSeedParityTests(unittest.TestCase):
    """The admin seed credential (STACK_ADMIN_USERNAME +
    STACK_ADMIN_PASSWORD) MUST match between compose and K8s.
    Drift here produces the 2026-04-20 "why is k8s admin/media-stack
    but compose admin/admin?" bug: a user trained on one platform's
    default is locked out of the other. The forced-rotation flow
    also relies on both platforms using the same weak seed so the
    dashboard modal's "current password" field accepts the same
    value everywhere."""

    @staticmethod
    def _k8s_secret_defaults() -> dict:
        for doc in _split_yaml_docs(_K8S_SECRETS.read_text(encoding="utf-8")):
            if doc.get("kind") == "Secret" \
                    and doc.get("metadata", {}).get("name") \
                            == "media-stack-secrets":
                return doc.get("stringData") or {}
        raise AssertionError(
            "media-stack-secrets Secret not found in secrets.example.yaml")

    @staticmethod
    def _compose_controller_env_default(key: str) -> str:
        """Extract the ``${KEY:-default}`` default for an env var
        on the compose controller service."""
        svcs = _compose_services()
        env = (svcs.get("media-stack-controller") or {}).get("environment") or {}
        raw = ""
        if isinstance(env, dict):
            raw = str(env.get(key, ""))
        elif isinstance(env, list):
            for item in env:
                if isinstance(item, str) and item.startswith(f"{key}="):
                    raw = item.split("=", 1)[1]
                    break
        # raw looks like "${STACK_ADMIN_PASSWORD:-admin}"
        m = re.search(r":-([^}]+)\}", raw)
        return (m.group(1).strip() if m else raw).strip('"').strip("'")

    def test_admin_username_matches(self):
        k8s = self._k8s_secret_defaults().get("STACK_ADMIN_USERNAME", "")
        compose = self._compose_controller_env_default("STACK_ADMIN_USERNAME")
        self.assertEqual(
            k8s, compose,
            f"STACK_ADMIN_USERNAME drift: k8s={k8s!r} "
            f"compose={compose!r}. Bump both in the same commit.",
        )

    def test_admin_password_matches(self):
        k8s = self._k8s_secret_defaults().get("STACK_ADMIN_PASSWORD", "")
        compose = self._compose_controller_env_default("STACK_ADMIN_PASSWORD")
        self.assertEqual(
            k8s, compose,
            f"STACK_ADMIN_PASSWORD drift: k8s={k8s!r} "
            f"compose={compose!r}. A user who logs in with 'admin' "
            f"on compose will be locked out on K8s (or vice versa). "
            f"Update both in the same commit.",
        )


class ComposeK8sImageTagParityTests(unittest.TestCase):
    """Controller image tag drift = "works on compose, breaks on
    K8s" bugs. Both deploys must point at the same registered
    tag. Bumping happens at release time; this test prevents
    accidentally bumping one and forgetting the other."""

    def _kustomization_controller_tag(self) -> str:
        data = yaml.safe_load(
            _K8S_KUSTOMIZATION.read_text(encoding="utf-8")
        ) or {}
        for entry in data.get("images", []):
            if isinstance(entry, dict) \
                    and entry.get("name") == _CONTROLLER_IMAGE:
                return str(entry.get("newTag", "")).strip()
        return ""

    def _compose_controller_tag(self) -> str:
        svcs = _compose_services()
        img = (svcs.get("media-stack-controller") or {}).get("image") or ""
        # image is "...${VAR:-default-tag}". Extract the default.
        m = re.search(r":-([^}]+)\}", img)
        if m:
            return m.group(1).strip().rsplit(":", 1)[-1]
        return img.rsplit(":", 1)[-1] if ":" in img else ""

    def test_compose_and_k8s_reference_same_image_tag(self):
        k8s_tag = self._kustomization_controller_tag()
        compose_tag = self._compose_controller_tag()
        self.assertTrue(k8s_tag,
                        "kustomization.yaml has no controller tag")
        self.assertTrue(compose_tag,
                        "docker-compose.yml has no controller tag")
        self.assertEqual(
            k8s_tag, compose_tag,
            f"controller image tag drifted: k8s={k8s_tag!r} "
            f"compose={compose_tag!r}. Bump both in the same "
            f"commit.",
        )


class K8sKustomizationCoversAllManifestsTests(unittest.TestCase):
    """Every meaningful manifest under ``k8s/`` must be wired into
    ``kustomization.yaml`` — otherwise ``kubectl apply -k k8s/``
    silently skips it and the deploy looks healthy while a whole
    service is missing. (Caught the 2026-04-20 authelia-not-
    provisioned bug: auth-authelia.yaml was a standalone manifest
    nobody applied unless they knew.)"""

    _IGNORED = frozenset({
        "kustomization.yaml",  # the index itself
        "namespace.yaml",      # referenced; below listed
        # .example.yaml files are profile templates, not manifests
        # to apply by default — profiles/ has its own kustomizations.
        "pvc-storage.example.yaml",
        "storageclass-microk8s.example.yaml",
        "storageclass-aks-azurefile.example.yaml",
        "keda-workers.example.yaml",
        # networkpolicy.yaml is an opt-in overlay (see hardening.yaml
        # which covers the defaults)
        "networkpolicy.yaml",
        # Alternative auth provider; enabled per profile rather
        # than in the default deploy (authelia is the default).
        "auth-authentik.yaml",
    })

    def test_every_manifest_is_referenced(self):
        data = yaml.safe_load(_K8S_KUSTOMIZATION.read_text(encoding="utf-8"))
        resources = set(data.get("resources") or [])
        missing: list[str] = []
        for path in sorted(_K8S_DIR.glob("*.yaml")):
            name = path.name
            if name in self._IGNORED:
                continue
            if name in resources:
                continue
            missing.append(name)
        self.assertFalse(
            missing,
            "k8s manifests not in kustomization.yaml resources:\n  "
            + "\n  ".join(missing)
            + "\nEither add them to the resources list, or if they "
              "are an opt-in overlay, add to _IGNORED in this test "
              "with a one-line justification.",
        )


class K8sControllerStateIsPersistentTests(unittest.TestCase):
    """Every subdirectory the controller writes to under
    ``/srv-config/`` MUST have a PVC mount — otherwise the state
    is ephemeral and pod restart wipes it.

    2026-04-20 incident: no PVC for ``/srv-config/controller/``,
    users.json (the controller user store) and audit.log.jsonl
    were both ephemeral. A pod restart would have wiped every
    non-admin user and broken the audit chain silently.

    The corresponding compose side uses a single host bind mount
    covering the whole CONFIG_ROOT — parity with K8s requires a
    per-subdirectory PVC because K8s doesn't have an equivalent
    single-mount idiom."""

    # Files the controller writes that MUST survive restart. If
    # the controller grows a new persistent artifact, add its
    # containing path here.
    #
    # ``/srv-config/.controller`` (dot-prefixed) — the state directory
    # moved from ``/srv-config/controller`` to ``/srv-config/.controller``
    # in v1.0.162 because state.py uses the dot-prefixed path. Before
    # the move the PVC was bound but unused, so every dashboard
    # "Save Routing" silently no-op'd on K8s.
    _STATEFUL_PATHS = (
        "/srv-config/authelia",     # users_database.yml, db.sqlite3
        "/srv-config/.controller",  # users.json, audit.log.jsonl, overrides
    )

    def test_controller_stateful_paths_have_pvc_mounts(self):
        dep = _k8s_controller_deployment()
        containers = dep["spec"]["template"]["spec"].get("containers") or []
        mounts = containers[0].get("volumeMounts") or []
        volumes = dep["spec"]["template"]["spec"].get("volumes") or []
        volume_by_name = {v.get("name"): v for v in volumes}

        missing: list[str] = []
        for required in self._STATEFUL_PATHS:
            mount = next((m for m in mounts
                          if m.get("mountPath") == required), None)
            if not mount:
                missing.append(
                    f"{required}: no volumeMount on controller")
                continue
            vol = volume_by_name.get(mount["name"])
            if not vol:
                missing.append(
                    f"{required}: mount references undefined volume "
                    f"{mount['name']!r}")
                continue
            if not vol.get("persistentVolumeClaim"):
                missing.append(
                    f"{required}: volume {mount['name']!r} isn't a "
                    f"PVC ({list(vol.keys())[:3]}). State at this "
                    "path is ephemeral — pod restart wipes it.",
                )
        self.assertFalse(missing, "\n  ".join(["Ephemeral stateful paths:"] + missing))


class K8sAutheliaSeedMatchesGeneratorDefaultsTests(unittest.TestCase):
    """The K8s ConfigMap ships seed values ONLY to populate the
    PVC on first pod start when the controller hasn't run yet.
    These values MUST be placeholders the controller's
    ``_real_secret`` detection recognises — otherwise the
    generator preserves them across regens (thinking they're
    real) and every install ships with a known credential."""

    _PLACEHOLDER_PREFIXES = ("PLACEHOLDER_", "change-this-")

    def _seed_config(self) -> dict:
        cm = _k8s_authelia_configmap()
        raw = (cm.get("data") or {}).get("configuration.yml") or ""
        return yaml.safe_load(raw) or {}

    def _is_placeholder(self, value: str) -> bool:
        return any(str(value).startswith(p)
                   for p in self._PLACEHOLDER_PREFIXES)

    def test_jwt_secret_is_placeholder(self):
        v = (self._seed_config().get("identity_validation") or {}) \
            .get("reset_password", {}).get("jwt_secret", "")
        self.assertTrue(
            self._is_placeholder(v),
            f"K8s ConfigMap ships a real jwt_secret ({v[:20]!r}) — "
            "committing real secrets to the repo leaks them, and "
            "the generator's _real_secret check preserves this as "
            "a real value across regens.",
        )

    def test_session_secret_is_placeholder(self):
        v = (self._seed_config().get("session") or {}).get("secret", "")
        self.assertTrue(
            self._is_placeholder(v),
            f"K8s ConfigMap ships a real session.secret ({v[:20]!r})",
        )

    def test_storage_encryption_key_is_placeholder(self):
        v = (self._seed_config().get("storage") or {}).get("encryption_key", "")
        self.assertTrue(
            self._is_placeholder(v),
            f"K8s ConfigMap ships a real encryption_key ({v[:20]!r}) — "
            "on a fresh pod this is the key Authelia encrypts "
            "db.sqlite3 with. The first regen then rotates it and "
            "the container crashloops.",
        )

    def test_storage_path_on_config_volume(self):
        """The generator's _reuse_existing_secrets reads the
        existing configuration.yml from ``<config_root>/authelia``.
        Authelia must store db.sqlite3 on the same PVC for the
        encryption_key preservation to work. /data or any other
        path breaks that guarantee."""
        v = (self._seed_config().get("storage") or {}).get("local", {}).get("path", "")
        self.assertTrue(
            v.startswith("/config/"),
            f"storage.local.path={v!r} — must live under /config "
            "(the shared PVC) for the controller-managed secret "
            "preservation to see both configuration.yml and "
            "db.sqlite3 on the same filesystem.",
        )


if __name__ == "__main__":
    unittest.main()
