"""Phase 16-B migration shim — moved to media_stack.domain.auth.users.audit_actions."""

from media_stack.domain.auth.users.audit_actions import *  # noqa: F401, F403
from media_stack.domain.auth.users import audit_actions as _impl

globals().update({k: v for k, v in vars(_impl).items() if not k.startswith("__")})
