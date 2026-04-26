"""Phase 16-B migration shim — moved to media_stack.application.auth.configure_auth_job.

Re-exports preserve back-compat for callers that still import via
``media_stack.core.auth.configure_auth_job``. Removed in Phase 16-F.

Note: ``contracts/services/authelia.yaml`` references the
``configure_auth`` callable through this shim's import path
(``media_stack.core.auth.configure_auth_job:configure_auth``), so this
shim must keep resolving until the contract is updated in 16-F.
"""

from media_stack.application.auth.configure_auth_job import *  # noqa: F401, F403
from media_stack.application.auth import configure_auth_job as _impl

globals().update({k: v for k, v in vars(_impl).items() if not k.startswith("__")})
