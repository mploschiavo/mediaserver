"""Re-export shim for :class:`DeployConfigService` (ADR-0015 Phase 3b).

The Phase 3 commit (``ac75320e``) introduced this module as a flat
file holding the post-consolidation god class. Phase 3b split the
god class into ``cli/workflows/deploy_config/`` — six SRP resolvers
plus a Facade. The Facade class itself is now in
``cli/workflows/deploy_config/facade.py``; the public class name
(:class:`DeployConfigService`) is unchanged.

This shim re-exports the Facade from its new location so existing
imports continue to work:

    from media_stack.cli.workflows.deploy_config_service import (
        DeployConfigService,
    )

remains valid. The :class:`DeployStackRunner` import in
``cli/commands/deploy_stack_main.py`` doesn't need to change for
the Phase 3b refactor; new code can import directly from
``cli.workflows.deploy_config``.

This shim is expected to be deleted in Phase 6 (the boundary
ratchet + cleanup phase) once all known importers have moved to
the canonical sub-package path.
"""

from media_stack.cli.workflows.deploy_config import DeployConfigService


__all__ = ["DeployConfigService"]
