"""Portable executable entry point. Arguments never contain campus passwords."""
import argparse
import sys


def main():
    parser = argparse.ArgumentParser(description="南科大校园面板个人客户端")
    parser.add_argument("--backend", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--port", type=int, default=18765)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--agent", action="store_true", help="连接自己服务器的执行主机客户端（附件下载与打印）")
    args = parser.parse_args()
    if args.self_test:
        from waitress import create_server
        from .authentication import secure_upstream
        from .app import create_app
        from . import __version__
        from .updates import TRUST_ROOT
        from tuf.api.metadata import Metadata
        secure_upstream()
        Metadata.from_bytes(TRUST_ROOT.read_bytes())
        assert len(list(create_app().url_map.iter_rules())) >= 35
        print("SELF_TEST_OK " + __version__, flush=True)
        return 0
    if args.agent:
        from .download_agent import main as agent
        agent()
        return 0
    from .runtime import backend, supervise
    return backend(args.port) if args.backend else supervise(args.port, not args.no_browser)


if __name__ == "__main__":
    sys.exit(main())
