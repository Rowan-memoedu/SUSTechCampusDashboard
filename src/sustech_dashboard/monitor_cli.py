"""Small command interface for the isolated cloud monitor."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .room_monitor import create_watch, now, public_state, read_state, schedule, state_lock, stop_watch, worker

STATE = Path(os.environ.get("SUSTECH_MONITOR_STATE", "/var/lib/sustech-room-monitor/watch.json"))


def _client():
    directory = os.environ.get("CREDENTIALS_DIRECTORY")
    if not directory:
        raise RuntimeError("systemd 凭据未装载")
    credentials = json.loads((Path(directory) / "sustech-cas").read_text(encoding="utf-8"))
    os.environ["SUSTECH_CREDENTIALS"] = "/nonexistent/sustech-credentials"
    from sustech_survival.sso import cred_set
    from sustech_survival.lib.booking.client import lib_booking

    cred_set(sid=credentials["sid"], pwd=credentials["password"])
    return lib_booking()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("worker", "status", "create", "stop", "auth-check", "probe"))
    args = parser.parse_args()
    try:
        if args.command == "worker":
            worker(STATE, _client())
            return
        if args.command == "auth-check":
            _client().whoami()
            print(json.dumps({"authenticated": True}))
            return
        if args.command == "probe":
            rooms = schedule(_client(), now().date())
            print(json.dumps({"rooms": len(rooms), "authenticated": True}))
            return
        if args.command == "create":
            data = json.load(sys.stdin)
            if not isinstance(data, dict) or set(data) != {"room_id", "room_name", "begin", "end", "title", "members"}:
                raise ValueError("监控参数不完整")
            with state_lock(STATE):
                result = create_watch(STATE, data)
        elif args.command == "stop":
            with state_lock(STATE):
                result = stop_watch(STATE)
        else:
            result = public_state(read_state(STATE))
        print(json.dumps(result, ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
