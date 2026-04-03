#!/usr/bin/env python3
"""CLI compatibility wrapper for Kubernetes scale policy application."""

from __future__ import annotations

import sys

from core.exceptions import MediaStackError
from core.platforms.kubernetes.apply_scale_policy_main import main

if __name__ == "__main__":
    try:
        sys.exit(main())
    except MediaStackError as exc:
        print(f"[ERR] {exc}", file=sys.stderr)
        sys.exit(1)
