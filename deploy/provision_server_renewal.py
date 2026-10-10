"""Provision isolated renewal only. Receives credentials through encrypted SSH.

No root/targets private key is accepted, stored or printed. This bootstrap runs
as root; the persistent service runs with only metadata write permissions.
"""
import json
from pathlib import Path
import subprocess
import sys


def run(*args):
    subprocess.run(args, check=True)


def provision(stage):
    payload = json.load(sys.stdin)
    if set(payload["online_keys"]) != {"snapshot", "timestamp"}:
        raise ValueError("Offline keys must not reach the server")
    from feed_metadata import verified_feed
    from server_renewal import load_online_keys
    config = Path("/etc/sustech-campus-renewal")
    config.mkdir(mode=0o700, exist_ok=True)
    config.chmod(0o700)
    trust = (stage / "trust-root.json").read_bytes()
    feed = Path("/var/www/sustech-campus-updates")
    verified_feed(lambda n: (feed / "metadata" / n).read_bytes(), trust)
    run("id", "campus-renewal") if subprocess.run(["id", "campus-renewal"], capture_output=True).returncode == 0 else run(
        "useradd", "--system", "--home-dir", "/var/lib/sustech-campus-renewal", "--shell", "/usr/sbin/nologin", "campus-renewal")
    for name, value in (("online-keys.json", payload["online_keys"]), ("mail.json", payload["mail"])):
        path = config / name
        with path.open("w") as handle:
            json.dump(value, handle)
        path.chmod(0o600)
    (config / "trust-root.json").write_bytes(trust)
    load_online_keys(config / "online-keys.json", trust)
    (config / "trust-root.json").chmod(0o644)
    # The public root is readable; private credential directory remains 0700.
    run("install", "-o", "root", "-g", "campus-renewal", "-m", "0750", "-d", str(config))
    (config / "trust-root.json").chmod(0o644)
    feed_metadata = feed / "metadata"
    run("chown", "root:campus-renewal", str(feed_metadata), str(feed / ".publish.lock"))
    feed_metadata.chmod(0o2775)
    (feed / ".publish.lock").chmod(0o660)
    code = Path("/opt/sustech-campus-renewal")
    for name in ("server_renewal.py", "feed_metadata.py", "publish_feed.py", "renewal_failure_alert.py"):
        run("install", "-o", "root", "-g", "root", "-m", "0644", str(stage / name), str(code / name))
    for name in ("sustech-campus-renewal.service", "sustech-campus-renewal.timer", "sustech-campus-renewal-alert.service"):
        run("install", "-m", "0644", str(stage / name), "/etc/systemd/system/" + name)
    run("systemd-analyze", "verify", "/etc/systemd/system/sustech-campus-renewal.service",
        "/etc/systemd/system/sustech-campus-renewal.timer", "/etc/systemd/system/sustech-campus-renewal-alert.service")
    run("systemctl", "daemon-reload")
    print(json.dumps({"provisioned": True, "server_key_roles": sorted(payload["online_keys"])}))


if __name__ == "__main__":
    provision(Path(sys.argv[1]))
