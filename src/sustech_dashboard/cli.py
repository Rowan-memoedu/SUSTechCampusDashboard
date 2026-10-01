"""Local dashboard commands; booking writes require an explicit web watch."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import subprocess
from pathlib import Path

from .core import DATA_ROOT
from .dpapi_store import CREDENTIALS_PATH, load_credentials, save_credentials


def _init_environment() -> None:
    # Never let the upstream library silently read a plaintext home credential file.
    os.environ["SUSTECH_CREDENTIALS"] = str(DATA_ROOT / "no-plaintext-credentials.txt")
    if CREDENTIALS_PATH.exists():
        from sustech_survival.sso import cred_set

        sid, password = load_credentials()
        cred_set(sid=sid, pwd=password)


def _configure() -> None:
    """Interactive only; never accept a password on the command line."""
    if not sys.stdin.isatty():
        raise RuntimeError("请在本机交互终端运行 configure")
    if CREDENTIALS_PATH.exists():
        raise FileExistsError("本机凭据已存在；不会覆盖")
    sid = input("南科大学号：").strip()
    password = getpass.getpass("CAS 密码（输入不显示）：")
    if not sid or not password or "\n" in sid + password or ":" in sid:
        raise ValueError("学号或密码格式无效")
    save_credentials(sid, password)
    print(f"凭据已加密保存到 {CREDENTIALS_PATH}；未连接校园系统。")


def main() -> None:
    parser = argparse.ArgumentParser(prog="sustech-dashboard", description="南科大云端面板与本机附件下载")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("configure", help="在本机隐藏输入校园凭据")
    sub.add_parser("sync", help="通过云端清单下载新附件；保留原有基线")
    sub.add_parser("serve", help="在 Chrome 打开云端校园面板")
    sub.add_parser("bookings", help="查询图书馆空闲数、场地和本人预约")
    sub.add_parser("status", help="输出上次同步状态")
    args = parser.parse_args()
    try:
        if args.command == "configure":
            _configure()
        elif args.command == "serve":
            url = "https://124.221.144.155/campus/"
            chrome = Path(os.environ["LOCALAPPDATA"]) / "Google/Chrome/Application/chrome.exe"
            subprocess.Popen([str(chrome), url])
            print(f"云端校园面板：{url}")
        elif args.command in {"status", "bookings"}:
            from .download_agent import cloud_status
            result = cloud_status()
            if args.command == "bookings":
                result = {"updated_at": result.get("source_updated_at", {}).get("bookings"),
                          "bookings": result.get("bookings"), "errors": result.get("errors")}
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == "sync":
            from .download_agent import sync_once
            result = sync_once()
            if result["mode"] == "error" or result.get("failed"):
                raise SystemExit(1)
    except KeyboardInterrupt:
        print("已停止。")
    except Exception as exc:
        print(f"命令失败：{type(exc).__name__}，请检查云端连接及面板登录配置。", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
