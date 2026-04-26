"""Phase 16-B migration shim — moved to media_stack.application.auth.users.legacy_service_admin_adapter."""

from media_stack.application.auth.users.legacy_service_admin_adapter import *  # noqa: F401, F403
from media_stack.application.auth.users import legacy_service_admin_adapter as _impl

globals().update({k: v for k, v in vars(_impl).items() if not k.startswith("__")})
