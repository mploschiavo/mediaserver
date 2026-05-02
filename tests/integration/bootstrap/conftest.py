"""Bootstrap-pipeline integration tests.

Each test in this directory exercises some part of the full
bootstrap dispatch chain — JobRunner, *arr HTTP preflight, Jellyfin
SQLite reads, ``/srv-config`` writes, etc. They were originally in
``tests/unit/`` but most of them mock too narrowly to fully isolate
the call paths their modules cover; the leak-through hits real
HTTP/filesystem when no live stack is up.

Living under ``tests/integration/`` keeps the default
``pytest tests/unit/`` invocation clean. The tests still run when
invoked directly (``pytest tests/integration/bootstrap``) — most
of them pass against a live compose stack today (~64/87 at the
last count); the remainder need either a real fix or proper DI-based
mocks. A future PR should rewrite the leaky-mock cases with proper
dependency injection so they can collapse back into ``tests/unit/``.
"""
