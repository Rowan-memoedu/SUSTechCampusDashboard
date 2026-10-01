"""Compatibility entry for an owner's authenticated HTTPS reverse proxy."""
import os


def main():
    os.environ["SUSTECH_CLOUD"] = "1"
    from .runtime import supervise
    return supervise(port=18771, open_browser=False)


if __name__ == "__main__":
    raise SystemExit(main())
