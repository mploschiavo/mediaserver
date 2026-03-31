#!/usr/bin/env python3
"""Compatibility facade for extracted bootstrap runtime modules."""

import bootstrap_services.runtime_core as _core
import bootstrap_services.runtime_servarr_ops as _servarr
from bootstrap_services.runtime_core import *  # noqa: F401,F403
from bootstrap_services.runtime_media_ops import *  # noqa: F401,F403
from bootstrap_services.runtime_servarr_ops import *  # noqa: F401,F403

_disk_usage_percent = _core._disk_usage_percent
_fmt_bytes = _core._fmt_bytes
_to_float = _core._to_float
_servarr_pipeline_service = _servarr._servarr_pipeline_service
