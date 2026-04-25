#!/usr/bin/env bash
# Phase 12-D Wave-2 shim — delegates to the `media-stack-bootstrap-all`
# console-script. The real CLI lives in
# `src/media_stack/cli/commands/controller_all_main.py` and is wired
# through `[project.scripts]` in `pyproject.toml`.
#
# Operator muscle-memory keeps `bin/bootstrap-all.sh` alive; new
# callers (Dockerfiles, k8s manifests, CI) should invoke
# `media-stack-bootstrap-all` directly.
set -Eeuo pipefail
exec media-stack-bootstrap-all "$@"
