"""Commands for the same per-owner runtime on a PC or personal server."""
import argparse
import getpass
import json
import os
import sys
import webbrowser


def main():
    parser = argparse.ArgumentParser(prog="sustech-dashboard", description="南科大个人校园客户端")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("start", "serve"):
        p = sub.add_parser(name, help="启动本机客户端")
        p.add_argument("--no-browser", action="store_true")
        p.add_argument("--port", type=int, default=18765)
    sub.add_parser("configure", help="在本机交互终端配置账号")
    sub.add_parser("status", help="输出此实例最近同步状态")
    sub.add_parser("bookings", help="输出此实例最近预约查询结果")
    sub.add_parser("download-agent", help="启动连接自己服务器的附件代理")
    sub.add_parser("open-server", help="打开已配对的个人服务器")
    sub.add_parser("sync", help="同步此实例；配对模式下载服务器资料")
    sub.add_parser("check-update", help="检查签名版本信息")
    args = parser.parse_args()
    try:
        from .paths import DATA_ROOT, prepare_private_directory
        from .core import load_json
        if args.command in {"start", "serve"}:
            from .runtime import supervise
            raise SystemExit(supervise(args.port, not args.no_browser))
        if args.command == "configure":
            if not sys.stdin.isatty():
                raise RuntimeError("请在本机交互终端运行 configure")
            prepare_private_directory()
            from .authentication import configure_credentials
            sid = input("南科大学号：").strip()
            password = getpass.getpass("CAS 密码（输入不显示）：")
            configure_credentials(sid, password, os.name == "nt")
            print("Windows 凭据已加密保存。" if os.name == "nt" else "账号验证成功；持久登录请配置 systemd 加密凭据。")
        elif args.command == "download-agent":
            from .download_agent import main as agent
            agent()
        elif args.command == "open-server":
            from .download_agent import CONFIG
            config = json.loads(CONFIG.read_text(encoding="utf-8"))
            webbrowser.open(config["url"])
        elif args.command in {"status", "bookings"}:
            result = load_json(DATA_ROOT / "snapshot.json", {})
            if not result and (DATA_ROOT / "cloud-access.dpapi.json").exists():
                from .download_agent import cloud_status
                result = cloud_status()
            if args.command == "bookings":
                result = {"updated_at": result.get("source_updated_at", {}).get("bookings"),
                          "bookings": result.get("bookings"), "errors": result.get("errors")}
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == "sync":
            if (DATA_ROOT / "cloud-access.dpapi.json").exists() and not (DATA_ROOT / "snapshot.json").exists():
                from .download_agent import sync_once
                result = sync_once()
            else:
                from .authentication import load_owner_credentials
                from .locking import exclusive_file
                with exclusive_file(DATA_ROOT / "backend.lock"):
                    if not load_owner_credentials():
                        raise RuntimeError("尚未配置账号")
                    from .app import sync_all
                    result = sync_all()
            if result.get("errors") or result.get("mode") == "error" or result.get("failed"):
                raise SystemExit(1)
        elif args.command == "check-update":
            from .updates import UpdateManager
            print(json.dumps(UpdateManager().check(), ensure_ascii=False))
    except KeyboardInterrupt:
        print("已停止。")
    except Exception as exc:
        print(f"命令失败：{type(exc).__name__}。请检查本实例配置及连接。", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
