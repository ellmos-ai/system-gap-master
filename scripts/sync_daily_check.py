#!/usr/bin/env python3
"""Compatibility wrapper for the former sync-master gate script name."""
from __future__ import annotations

import sys
import warnings

from system_gap_daily_check import main

warnings.warn(
    "scripts/sync_daily_check.py was renamed to system_gap_daily_check.py",
    DeprecationWarning,
    stacklevel=2,
)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
