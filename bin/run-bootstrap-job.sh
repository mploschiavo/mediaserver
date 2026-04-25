#!/usr/bin/env bash
# Phase 12-D Wave-2 shim — delegates to the `media-stack-run-job`
# console-script. The real CLI lives in
# `src/media_stack/cli/commands/run_controller_job_main.py` and is
# wired through `[project.scripts]` in `pyproject.toml`.
#
# Operator muscle-memory keeps `bin/run-bootstrap-job.sh` alive; new
# callers should invoke `media-stack-run-job` directly.
set -Eeuo pipefail
exec media-stack-run-job "$@"
