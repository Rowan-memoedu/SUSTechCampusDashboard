"""Invoke fixed monitor commands on the private server over SSH."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

REMOTE = "ubuntu@124.221.144.155"
PYTHON = "/opt/sustech-room-monitor/venv/bin/python"


def call(command: str, data: dict | None = None) -> dict[str, Any]:
    if command not in {"status", "create", "stop"}:
        raise ValueError("未知监控命令")
    if os.environ.get("SUSTECH_CLOUD") == "1":
        from .room_monitor import create_watch, stop_watch, state_lock, read_state, public_state
        state = Path(os.environ["SUSTECH_MONITOR_STATE"])
        if command == "status":
            return public_state(read_state(state))
        with state_lock(state):
            return create_watch(state, data) if command == "create" else stop_watch(state)
    run = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", REMOTE,
         f"sudo -n -u sustechmon {PYTHON} -m sustech_dashboard.monitor_cli {command}"],
        input=json.dumps(data, ensure_ascii=False) if data is not None else None,
        text=True, capture_output=True, timeout=20, encoding="utf-8",
    )
    try:
        result = json.loads(run.stdout)
    except ValueError:
        raise RuntimeError("远程监控服务暂不可用") from None
    if run.returncode or "error" in result:
        raise RuntimeError(result.get("error", "远程监控命令失败"))
    return result
