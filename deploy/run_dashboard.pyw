"""Background entry point for the per-user Windows logon task."""

from __future__ import annotations

import sys

from sustech_dashboard.core import DATA_ROOT

DATA_ROOT.mkdir(parents=True, exist_ok=True)
sys.stdout = (DATA_ROOT / "serve.stdout.log").open("a", encoding="utf-8", buffering=1)
sys.stderr = (DATA_ROOT / "serve.stderr.log").open("a", encoding="utf-8", buffering=1)
from sustech_dashboard.download_agent import main

main()
