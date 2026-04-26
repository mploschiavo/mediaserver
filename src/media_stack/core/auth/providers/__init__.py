"""Phase 16-B migration shim — moved to media_stack.adapters.auth.providers.

This package's ``__path__`` is rewritten to point at the new location
so ``pkgutil.iter_modules`` over the old name still discovers the
authelia / authentik / none provider packages.

Removed in Phase 16-F.
"""

from media_stack.adapters.auth import providers as _new_pkg

# Reuse the new package's __path__ so plugin discovery works against
# both names.
__path__ = list(_new_pkg.__path__)
