"""Phase 16-B migration shim — moved to media_stack.adapters.auth.providers.authentik.provider."""

from media_stack.adapters.auth.providers.authentik.provider import *  # noqa: F401, F403
from media_stack.adapters.auth.providers.authentik import provider as _impl

globals().update({k: v for k, v in vars(_impl).items() if not k.startswith("__")})
