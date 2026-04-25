"""Job framework — runs bootstrap/maintenance jobs declared in
``contracts/services/*.yaml``.

Moved here from ``media_stack.cli.commands.job_framework`` under
ADR-0002 Phase 16: this is a shared service used by ``services/``,
``api/``, and ``cli/``, so it doesn't belong in the CLI entry-point
layer. The old location now re-exports from here so existing
imports continue to work; new code should import directly from
``media_stack.services.jobs.framework``.
"""
