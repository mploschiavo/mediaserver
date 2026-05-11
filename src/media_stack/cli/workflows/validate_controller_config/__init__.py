"""``cli/workflows/validate_controller_config/`` — bootstrap config validation.

ADR-0015 Phase 7b. The sub-package contains five SRP classes
(each with a named GoF pattern in its docstring) that together
implement the ``bin/validate-bootstrap-config.sh`` workflow:

* :class:`JsonPathFormatter` (Value Object) — render path parts
  as ``$.foo[0].bar`` for error-message readability.
* :class:`MediaServerOperationPlanValidator` (Strategy) — walk
  ``media_server_*operation_plans`` sub-trees.
* :class:`Microk8sReconcileHookValidator` (Strategy) — walk
  the ``adapter_hooks.microk8s_reconcile`` sub-tree.
* :class:`BasicConfigValidator` (Validator) — top-level
  bootstrap-config shape checks; composes the two Strategy
  validators above.
* :class:`ValidateControllerConfigService` (Composition Root) —
  load + schema-validate (jsonschema) + semantic-validate
  (:class:`TopLevelBootstrapConfig`).
"""

from media_stack.cli.workflows.validate_controller_config.basic_config_validator import (
    BasicConfigValidator,
)
from media_stack.cli.workflows.validate_controller_config.event_plan_validators import (
    MediaServerOperationPlanValidator,
    Microk8sReconcileHookValidator,
)
from media_stack.cli.workflows.validate_controller_config.path_formatter import (
    JsonPathFormatter,
)
from media_stack.cli.workflows.validate_controller_config.validate_service import (
    ValidateControllerConfigService,
)


__all__ = [
    "BasicConfigValidator",
    "JsonPathFormatter",
    "MediaServerOperationPlanValidator",
    "Microk8sReconcileHookValidator",
    "ValidateControllerConfigService",
]
