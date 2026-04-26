"""Phase 16-B migration shim — moved to media_stack.domain.auth.users.service_admin_provider."""

from media_stack.domain.auth.users.service_admin_provider import *  # noqa: F401, F403
from media_stack.domain.auth.users import service_admin_provider as _impl

globals().update({k: v for k, v in vars(_impl).items() if not k.startswith("__")})
