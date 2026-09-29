"""Local commands. No campus write operations are exposed."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import subprocess
import sys

from .core import DATA_ROOT, load_json


CREDENTIALS_PATH = DATA_ROOT / "credentials.txt"


def _init_environment() -> None:
    os.environ["SUSTECH_CREDENTIALS"] = str(CREDENTIALS_PATH)


def _configure() -> None:
    """Interactive only; never accept a password on the command line."""
    if not sys.stdin.isatty():
        raise RuntimeError("请在本机交互终端运行 configure")
    sid = input("南科大学号：").strip()
    password = getpass.getpass("CAS 密码（输入不显示）：")
    if not sid or not password or "\n" in sid + password or ":" in sid:
        raise ValueError("学号或密码格式无效")
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    if CREDENTIALS_PATH.exists():
        raise FileExistsError("本机凭据文件已存在；不会覆盖")
    created = False
    try:
        if os.name == "nt":
            sid_text = subprocess.check_output(["whoami", "/user", "/fo", "csv", "/nh"], text=True)
            user_sid = next(__import__("csv").reader([sid_text.strip()]))[1]
            subprocess.run(["icacls", str(DATA_ROOT), "/inheritance:r", "/grant:r", f"*{user_sid}:(OI)(CI)F"],
                           check=True, capture_output=True, text=True)
        with CREDENTIALS_PATH.open("x", encoding="utf-8") as handle:
            created = True
            handle.write(f"{sid}:{password}\n")
    except Exception:
        if created:
            CREDENTIALS_PATH.unlink(missing_ok=True)
        raise RuntimeError("本机凭据配置失败，已移除新建文件") from None
    print(f"凭据已保存到 {CREDENTIALS_PATH}；未连接校园系统。")


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
    _init_environment()
    try:
        if args.command == "configure":
            _configure()
        elif args.command == "sync":
            from .app import sync_all

            print(json.dumps(sync_all(), ensure_ascii=False, indent=2, default=str))
        elif args.command == "serve":
            from .app import serve

            serve(args.port)
        elif args.command == "bookings":
            from .provider import read_bookings

            print(json.dumps(read_bookings(), ensure_ascii=False, indent=2))
        elif args.command == "status":
            print(json.dumps(load_json(DATA_ROOT / "snapshot.json", {"message": "尚未同步"}), ensure_ascii=False, indent=2))
    except KeyboardInterrupt:
        print("已停止。")
    except Exception as exc:
        print(f"错误：{exc}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
