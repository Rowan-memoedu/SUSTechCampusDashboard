"""Local dashboard commands; booking writes require an explicit web watch."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys

from .core import DATA_ROOT, load_json
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
    parser = argparse.ArgumentParser(prog="sustech-dashboard", description="南科大本地信息面板")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("configure", help="在本机隐藏输入校园凭据")
    sub.add_parser("sync", help="同步页面数据和新附件；首次成功扫描只建立基线")
    serve_parser = sub.add_parser("serve", help="启动本机网页并定期同步")
    serve_parser.add_argument("--port", type=int, default=8765)
    sub.add_parser("bookings", help="查询图书馆空闲数、场地和本人预约")
    sub.add_parser("status", help="输出上次同步状态")
    args = parser.parse_args()
    try:
        if args.command == "configure":
            _configure()
        elif args.command == "status":
            print(json.dumps(load_json(DATA_ROOT / "snapshot.json", {"message": "尚未同步"}), ensure_ascii=False, indent=2))
        else:
            _init_environment()
            if args.command == "sync":
                from .app import sync_all

                print(json.dumps(sync_all(), ensure_ascii=False, indent=2, default=str))
            elif args.command == "serve":
                from .app import serve

                serve(args.port)
            elif args.command == "bookings":
                from .provider import read_bookings

                print(json.dumps(read_bookings(), ensure_ascii=False, indent=2))
    except KeyboardInterrupt:
        print("已停止。")
    except Exception as exc:
        print(f"错误：{exc}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
