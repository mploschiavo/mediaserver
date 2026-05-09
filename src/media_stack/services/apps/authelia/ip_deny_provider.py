"""Authelia IPDenyProvider — merges deny rules into configuration.yml.

Authelia enforces IP-level access control at the gateway via its
``access_control.rules`` list in ``configuration.yml``. Envoy's
ext_authz sidecar consults Authelia on every request; a user or
request from a denied CIDR gets a 403 before it reaches any
downstream app. This works identically in docker-compose and
kubernetes — both mount the same ``configuration.yml`` via a shared
volume — so ban enforcement is deployment-parity by construction.

Managed-rule convention
-----------------------

We own exactly **one** rule in ``access_control.rules`` — pinned at
**position 0** so Authelia evaluates it before any admin-authored
rule. The shape is canonical:

    - domain: "*"
      policy: deny
      networks:
        - 203.0.113.45/32
        - 198.51.100.0/24

We detect our rule by this exact shape: ``domain == "*"`` AND
``policy == "deny"`` AND key set is exactly ``{domain, policy, networks}``.
Any other rule the operator adds is left untouched.

When the ban list becomes empty, we REMOVE the rule entirely rather
than leaving an empty-networks entry — Authelia's validator rejects
``networks: []`` and the deploy would fail to reload.

Reload signalling
-----------------

Authelia watches ``users_database.yml`` (``watch: true`` in compose +
k8s configs) but does **not** watch ``configuration.yml``. An IP ban
therefore needs an explicit reload — either ``SIGHUP`` to the
Authelia process, or a service restart. The provider takes an
optional ``reload_hook`` callable; production wiring passes in a
function that calls ``admin_svc.restart_service("authelia")``.

In tests and during bootstrap we pass ``None`` so writes are
deterministic without side effects. The hook being called counts as
part of the public contract — observable through a mock.

Config-generator interaction
----------------------------

``core/auth/authelia_config_generator.py`` owns the broader shape of
``configuration.yml``. If it ever regenerates the file from scratch,
our managed rule is lost. Two safeguards:

1. The config generator is expected to call
   ``AutheliaIPDenyProvider.reapply()`` after it writes, handing back
   the persistent ban-list from ``BanStore``. A follow-up task wires
   this cleanly; for now, re-run of the generator **will** drop
   active bans until the next ``add_ip_deny`` call.
2. The provider itself is idempotent: calling ``add_ip_deny`` on a
   cidr already present is a no-op; ``remove_ip_deny`` on a missing
   cidr is a no-op.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from media_stack.core.auth.users.ip_deny import IPDeny
from media_stack.core.auth.users.safe_yaml_edit import SafeYamlEditor

_log = logging.getLogger("media_stack")

_MANAGED_DOMAIN = "*"
_MANAGED_POLICY = "deny"
_MANAGED_KEY_SET = frozenset({"domain", "policy", "networks"})

ReloadHook = Callable[[], None]


class AutheliaIPDenyError(RuntimeError):
    """Raised when the provider cannot safely apply a mutation.

    Includes misshapen configuration.yml, validator rejections from
    SafeYamlEditor, and cases where the managed rule can't be found
    unambiguously (multiple candidates — a human edited our slot).
    """


@dataclass(frozen=True)
class _ManagedRuleView:
    """Read-only snapshot of the managed rule + its position in the list."""

    index: int
    networks: tuple[str, ...]


class AutheliaIPDenyProvider:
    """Implements ``IPDenyProvider`` against Authelia's configuration.yml.

    Constructor
    -----------
    - ``config_path``: full path to Authelia's ``configuration.yml``.
      Typically mounted at ``/srv-config/authelia/configuration.yml``
      in both compose and k8s.
    - ``reload_hook``: optional callable invoked after a successful
      write. None means skip the reload (useful in tests or when the
      caller is batching multiple edits and will reload once at the
      end). Errors in the hook propagate so callers can see reload
      failures.
    """

    name = "authelia"

    def __init__(
        self,
        config_path: Path | None = None,
        reload_hook: ReloadHook | None = None,
    ) -> None:
        self._path = Path(config_path) if config_path is not None else None
        self._reload_hook = reload_hook

    # ---- IPDenyProvider -------------------------------------------------

    def list_ip_denies(self) -> list[IPDeny]:
        """Return the currently-persisted deny CIDRs.

        Only the ``cidr`` field is populated — Authelia's config
        doesn't carry our metadata (reason/actor/expires_at). The
        controller's ``BanStore`` is the source of truth for that
        metadata; this provider only confirms what the gateway is
        actually enforcing.
        """
        rule = self._read_managed_rule()
        if rule is None:
            return []
        return [IPDeny(cidr=c) for c in rule.networks]

    def add_ip_deny(self, rule: IPDeny) -> None:
        """Add ``rule.cidr`` to the managed deny list.

        Idempotent — re-adding an existing CIDR is a no-op except
        that the reload hook still fires (explicit re-sync is
        sometimes useful operationally).
        """
        cidr = rule.cidr
        mod = sys.modules[__name__]

        def _mutate(current: dict[str, Any]) -> dict[str, Any]:
            return mod._merge_deny(current, add=cidr, remove=None)

        self._editor().edit(_mutate)
        self._signal_reload()

    def remove_ip_deny(self, cidr: str) -> None:
        """Remove ``cidr`` from the managed deny list.

        Idempotent — removing an absent CIDR is a no-op. Input is
        normalised through ``IPDeny``'s validator so callers can pass
        bare addresses ("203.0.113.45") and have them match /32 form.
        """
        normalised = IPDeny(cidr=cidr).cidr
        mod = sys.modules[__name__]

        def _mutate(current: dict[str, Any]) -> dict[str, Any]:
            return mod._merge_deny(current, add=None, remove=normalised)

        self._editor().edit(_mutate)
        self._signal_reload()

    # ---- Internals ------------------------------------------------------

    def _editor(self) -> SafeYamlEditor:
        if self._path is None:
            raise AutheliaIPDenyError(
                "AutheliaIPDenyProvider has no config_path bound; "
                "construct an instance with config_path before editing",
            )
        return SafeYamlEditor(
            self._path,
            validator=sys.modules[__name__]._validate_authelia_config,
        )

    def _read_managed_rule(self) -> _ManagedRuleView | None:
        if self._path is None or not self._path.is_file():
            return None
        import yaml
        try:
            data = yaml.safe_load(self._path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            return None
        return sys.modules[__name__]._find_managed_rule(data)

    def _signal_reload(self) -> None:
        if self._reload_hook is None:
            return
        self._reload_hook()

    # ---- Pure helpers (instance methods, no IO) -------------------------

    def _find_managed_rule(self, data: dict[str, Any]) -> _ManagedRuleView | None:
        """Locate our managed rule in ``access_control.rules``.

        Returns None when the rule isn't present. Raises
        ``AutheliaIPDenyError`` if MULTIPLE candidates match — a human
        has edited our slot and we refuse to silently pick one.
        """
        mod = sys.modules[__name__]
        rules = mod._rules_list(data)
        matches: list[tuple[int, dict[str, Any]]] = []
        for idx, rule in enumerate(rules):
            if mod._is_managed_rule(rule):
                matches.append((idx, rule))
        if not matches:
            return None
        if len(matches) > 1:
            raise AutheliaIPDenyError(
                "multiple candidate managed rules in access_control.rules — "
                "refusing to guess which one is ours",
            )
        idx, rule = matches[0]
        networks = tuple(str(n) for n in (rule.get("networks") or []))
        return _ManagedRuleView(index=idx, networks=networks)

    def _is_managed_rule(self, rule: Any) -> bool:
        if not isinstance(rule, dict):
            return False
        if set(rule.keys()) != _MANAGED_KEY_SET:
            return False
        if rule.get("domain") != _MANAGED_DOMAIN:
            return False
        if rule.get("policy") != _MANAGED_POLICY:
            return False
        if not isinstance(rule.get("networks"), list):
            return False
        return True

    def _rules_list(self, data: dict[str, Any]) -> list[Any]:
        ac = data.get("access_control") or {}
        if not isinstance(ac, dict):
            return []
        rules = ac.get("rules") or []
        if not isinstance(rules, list):
            return []
        return rules

    def _merge_deny(
        self,
        current: dict[str, Any],
        *,
        add: str | None,
        remove: str | None,
    ) -> dict[str, Any]:
        """Pure merge: return an updated document with our managed rule
        updated according to ``add`` / ``remove``.

        One of ``add`` or ``remove`` must be non-None; both being None is
        a caller bug. Not exposed; used only by the mutator lambdas.
        """
        if add is None and remove is None:
            raise ValueError("_merge_deny requires either add or remove")

        mod = sys.modules[__name__]
        data = dict(current)
        ac = dict(data.get("access_control") or {})
        rules = list(ac.get("rules") or [])
        # Find existing managed rule, capturing its index.
        managed_idx = -1
        managed_networks: list[str] = []
        for idx, rule in enumerate(rules):
            if mod._is_managed_rule(rule):
                if managed_idx != -1:
                    raise AutheliaIPDenyError(
                        "duplicate managed rules present; cannot merge",
                    )
                managed_idx = idx
                managed_networks = list(rule.get("networks") or [])

        # Apply the requested change.
        new_networks = list(managed_networks)
        if add is not None and add not in new_networks:
            new_networks.append(add)
        if remove is not None and remove in new_networks:
            new_networks.remove(remove)
        # Deduplicate while preserving first-seen order (set() doesn't).
        seen: set[str] = set()
        ordered: list[str] = []
        for n in new_networks:
            if n in seen:
                continue
            seen.add(n)
            ordered.append(n)
        new_networks = ordered

        # Write back.
        if not new_networks:
            # Remove the managed rule entirely — Authelia rejects
            # empty-networks entries at validation.
            if managed_idx != -1:
                rules.pop(managed_idx)
        else:
            new_rule = {
                "domain": _MANAGED_DOMAIN,
                "policy": _MANAGED_POLICY,
                "networks": new_networks,
            }
            if managed_idx == -1:
                # Pin to position 0 so it takes precedence over any
                # admin-authored allow rules.
                rules.insert(0, new_rule)
            else:
                rules[managed_idx] = new_rule

        ac["rules"] = rules
        data["access_control"] = ac
        return data

    def _validate_authelia_config(self, data: dict[str, Any]) -> None:
        """SafeYamlEditor validator — ensures we never write a shape that
        Authelia would reject on reload.

        We only validate the parts we touched. A busted ``configuration.yml``
        elsewhere is out of scope for this writer; the config generator
        owns the broader contract.
        """
        mod = sys.modules[__name__]
        ac = data.get("access_control")
        if ac is None:
            return  # file may not have access_control yet; our writer adds it
        if not isinstance(ac, dict):
            raise ValueError("access_control must be a mapping")
        rules = ac.get("rules")
        if rules is None:
            return
        if not isinstance(rules, list):
            raise ValueError("access_control.rules must be a list")
        for i, rule in enumerate(rules):
            if not isinstance(rule, dict):
                raise ValueError(f"access_control.rules[{i}] must be a mapping")
            if mod._is_managed_rule(rule):
                networks = rule["networks"]
                if not networks:
                    raise ValueError(
                        f"access_control.rules[{i}] is our managed rule but "
                        "has empty networks — Authelia would reject this",
                    )


# Module-level singleton + aliases so loose-helper call sites and
# ``mock.patch`` targets continue to resolve. All public/private helper
# names dispatch through ``sys.modules[__name__].<name>`` inside the
# class so test-time monkeypatching of these aliases takes effect.
_INSTANCE = AutheliaIPDenyProvider()

_find_managed_rule = _INSTANCE._find_managed_rule
_is_managed_rule = _INSTANCE._is_managed_rule
_rules_list = _INSTANCE._rules_list
_merge_deny = _INSTANCE._merge_deny
_validate_authelia_config = _INSTANCE._validate_authelia_config


__all__ = [
    "AutheliaIPDenyError",
    "AutheliaIPDenyProvider",
    "ReloadHook",
]
