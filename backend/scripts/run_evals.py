#!/usr/bin/env python3
"""
scripts/run_evals.py — convenience launcher for the eval battery.
Delegates to backend.evals.runner so the deploy hook stays one line:

    cd backend && python scripts/run_evals.py
"""
from __future__ import annotations

import os
import sys

# Make `backend/` importable
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from evals.runner import main as _main  # noqa: E402


if __name__ == "__main__":
    sys.exit(_main())
