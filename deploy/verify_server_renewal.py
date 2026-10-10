"""Live isolated fault injection: deliver failure/recovery mail without resigning."""
import hashlib
import json
import os
from pathlib import Path

from server_renewal import Renewal, send_mail, client_check


def main():
    credentials = Path(os.environ["CREDENTIALS_DIRECTORY"])
    root = Path("/var/www/sustech-campus-updates")
    state = Path("/var/lib/sustech-campus-renewal/acceptance-20261010")
    before = hashlib.sha256((root / "metadata/timestamp.json").read_bytes()).hexdigest()

    def notify(config, subject, body):
        send_mail(config, "验收：" + subject, "这是服务器故障恢复演练，实际更新源正常。\n" + body)

    worker = Renewal(root, state, Path("/etc/sustech-campus-renewal/trust-root.json").read_bytes(),
                     credentials / "online-keys", json.loads((credentials / "mail").read_text()), notify=notify)
    worker.check = lambda *a, **k: (_ for _ in ()).throw(ConnectionError("isolated readback fault"))
    failed = worker.run()
    assert failed["status"] == "error" and failed["pending_alerts"] == 0
    worker.check = client_check
    recovered = worker.run()
    assert recovered["client_verified"] and recovered["pending_alerts"] == 0
    after = hashlib.sha256((root / "metadata/timestamp.json").read_bytes()).hexdigest()
    assert after == before
    result = {"live_failure_alert_sent": True, "live_recovery_alert_sent": True,
              "timestamp_unchanged": True, "client_verified": True}
    (state / "acceptance.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result))


if __name__ == "__main__":
    main()
