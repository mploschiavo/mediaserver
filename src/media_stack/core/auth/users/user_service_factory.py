"""Phase 16-B migration shim — moved to media_stack.application.auth.users.user_service_factory."""

from media_stack.application.auth.users.user_service_factory import *  # noqa: F401, F403
from media_stack.application.auth.users import user_service_factory as _impl

globals().update({k: v for k, v in vars(_impl).items() if not k.startswith("__")})
