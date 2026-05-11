"""``cli/workflows/deploy_config/`` — deploy config resolution sub-package.

ADR-0015 Phase 3b. The sub-package contains six single-responsibility
resolvers (each with a named GoF pattern in its docstring) plus a
Facade that composes them. :class:`DeployStackRunner` talks to the
Facade only; the individual resolvers are wired up internally so
the call-site surface stays stable across the Phase 3b refactor
and any future re-wiring.

Public surface:

* :class:`DeployConfigService` — Facade. The class
  ``DeployStackRunner`` constructs and calls; method names match
  the pre-Phase-3b mixin surface verbatim.
* :class:`BootstrapConfigLoader` — Repository for the bootstrap
  config JSON + ``adapter_hooks.*`` typed views + the
  ``profile_actions`` tuple decode. Exposed for tests that want to
  exercise the loader without spinning the whole resolver chain.
* The five resolver classes — :class:`EdgeRoutingResolver`,
  :class:`AuthProviderResolver`, :class:`ProfileCatalogValidator`,
  :class:`RuntimePolicyResolver`, :class:`ComposeDeployResolver`.
  Exposed for direct testing + for future call sites that only
  need one specific concern (no need to instantiate the whole
  facade just to ask for, say, valid_auth_providers).
"""

from media_stack.cli.workflows.deploy_config.auth_provider_resolver import (
    AuthProviderResolver,
)
from media_stack.cli.workflows.deploy_config.bootstrap_config_loader import (
    BootstrapConfigLoader,
)
from media_stack.cli.workflows.deploy_config.compose_deploy_resolver import (
    ComposeDeployResolver,
)
from media_stack.cli.workflows.deploy_config.edge_routing_resolver import (
    EdgeRoutingResolver,
)
from media_stack.cli.workflows.deploy_config.facade import (
    DeployConfigService,
)
from media_stack.cli.workflows.deploy_config.profile_catalog_validator import (
    ProfileCatalogValidator,
)
from media_stack.cli.workflows.deploy_config.runtime_policy_resolver import (
    RuntimePolicyResolver,
)


__all__ = [
    "AuthProviderResolver",
    "BootstrapConfigLoader",
    "ComposeDeployResolver",
    "DeployConfigService",
    "EdgeRoutingResolver",
    "ProfileCatalogValidator",
    "RuntimePolicyResolver",
]
