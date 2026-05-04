"""DEPRECATED — superseded by ``test_static_invariants_ratchets.py::
OpenApiHandlerParity`` (ADR-0007 Phase 2 wave-8 / Phase E cleanup).

Why the file is empty
---------------------
This ratchet originally walked ``handlers_get.py`` / ``handlers_post.py``
with ``ast`` to extract ``path == "..."`` / ``path.startswith("...")``
literals and compared the resulting strings against the OpenAPI
spec. ADR-0007 Phase E retired both files entirely; routing now goes
through ``api/routing/router.py::Router`` with each route registered
via the ``RouteModule.__init_subclass__`` auto-discovery. There are
no more ``path == "..."`` literals in dispatcher source to grep.

The replacement ratchet ``OpenApiHandlerParity.test_every_openapi_
path_has_handler`` (in ``test_static_invariants_ratchets.py``) walks
the production Router's registered routes instead of grepping
source — a structural check rather than a literal-match check, with
the Router's own ``RouterMisconfigured`` startup error doubling as
the authoritative parity gate. The new ratchet covers strictly more
than this file did:

* It detects an unregistered RouteModule (the literal-grep ratchet
  couldn't — the literal would still appear in the source even if
  the module wasn't auto-discovered).
* It catches a spec path that has no matching ``@get(...)`` /
  ``@post(...)`` decorator (the literal-grep version was already
  doing this).
* It uses the Router's path-template + method-set comparison,
  matching ``/api/users/{user_id}`` against the registered
  parameterised handler, instead of normalising templates to ``*``
  on the fly.

This file is preserved as a tombstone so the test discovery picks
it up cleanly and a future grep for ``test_openapi_drift_ratchet``
in CHANGELOG / commit messages still resolves to a real path with a
deprecation notice.
"""

from __future__ import annotations

# Intentionally no test classes — the invariant lives in
# test_static_invariants_ratchets.py::OpenApiHandlerParity.
