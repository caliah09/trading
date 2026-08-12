#!/usr/bin/env python3
"""Standalone one-shot refresh.

The dashboard schedules its own refreshes internally, so this script is *not*
required for scheduling. It exists to seed the database before the first launch
and for ad-hoc scripted runs:

    python scripts/refresh_rankings.py --provider synthetic

It is equivalent to ``python -m screener refresh``.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from screener.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(["refresh", *sys.argv[1:]]))
