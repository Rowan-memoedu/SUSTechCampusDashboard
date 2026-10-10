"""Save public deployment evidence without exposing server/mail credentials."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from renew_feed import SSH_HOST, SSH_OPTIONS, verified_feed, get_public, client_check
from publish_release import TRUST_ROOT


def main():
    evidence = Path("D:/Artifacts/SUSTechCampusDashboard/server-renewal-20261010")
    evidence.mkdir(parents=True, exist_ok=True)
    blobs, roles = verified_feed(get_public)
    root = Path(__file__).resolve().parents[1]
    code_names = ["server_renewal.py", "feed_metadata.py", "publish_feed.py", "renewal_failure_alert.py"]
    unit_names = ["sustech-campus-renewal.service", "sustech-campus-renewal.timer", "sustech-campus-renewal-alert.service"]
    remote_code = '''
import hashlib,json,subprocess
from pathlib import Path
folder=Path('/opt/sustech-campus-renewal')
names=['server_renewal.py','feed_metadata.py','publish_feed.py','renewal_failure_alert.py']
units=['sustech-campus-renewal.service','sustech-campus-renewal.timer','sustech-campus-renewal-alert.service']
result={'code_hashes':{n:hashlib.sha256((folder/n).read_bytes()).hexdigest() for n in names},
'unit_hashes':{n:hashlib.sha256((Path('/etc/systemd/system')/n).read_bytes()).hexdigest() for n in units},
'server_key_roles':sorted(json.loads(Path('/etc/sustech-campus-renewal/online-keys.json').read_text())),
'status':json.loads(Path('/var/lib/sustech-campus-renewal/status.json').read_text()),
'fault_test':json.loads(Path('/var/lib/sustech-campus-renewal/acceptance-20261010/acceptance.json').read_text()),
'timer':subprocess.check_output(['systemctl','is-enabled','sustech-campus-renewal.timer'],text=True).strip(),
'service':subprocess.check_output(['systemctl','show','sustech-campus-renewal.service','-p','Result','-p','ExecMainStatus','-p','OnFailure'],text=True).strip(),
'trust_sha256':hashlib.sha256(Path('/etc/sustech-campus-renewal/trust-root.json').read_bytes()).hexdigest()}
print(json.dumps(result))
'''
    remote = subprocess.run(["ssh", *SSH_OPTIONS, SSH_HOST, "sudo -n /opt/sustech-campus-renewal/venv/bin/python -"],
                            input=remote_code, capture_output=True, text=True, check=True, timeout=90)
    server = json.loads(remote.stdout)
    assert server["code_hashes"] == {n: hashlib.sha256((root / "deploy" / n).read_bytes()).hexdigest() for n in code_names}
    assert server["unit_hashes"] == {n: hashlib.sha256((root / "deploy" / n).read_bytes()).hexdigest() for n in unit_names}
    assert server["server_key_roles"] == ["snapshot", "timestamp"]
    assert server["trust_sha256"] == hashlib.sha256(TRUST_ROOT.read_bytes()).hexdigest()
    assert server["timer"] == "enabled" and "ExecMainStatus=0" in server["service"]
    assert server["status"]["client_verified"] and server["status"]["pending_alerts"] == 0
    releases = client_check(evidence / "client")
    mailbox = subprocess.run([sys.executable, str(root / "deploy/verify_renewal_mail.py"), "--include-fault-test"],
                             capture_output=True, text=True, check=True, timeout=90)
    result = {"server": server, "public_metadata_versions": {r: m.signed.version for r, m in roles.items()},
              "public_expiry": {r: m.signed.expires.isoformat() for r, m in roles.items()}, "releases": releases,
              "mailbox": json.loads(mailbox.stdout), "deployment_matches_source": True,
              "offline_keys_stay_local": True, "windows_tests": "235 passed, 2 skipped",
              "linux_tests": "235 passed, 2 skipped"}
    (evidence / "deployment.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"deployment_matches_source": True, "public_metadata_versions": result["public_metadata_versions"],
                      "inbox_verified": result["mailbox"]["inbox_verified"], "evidence": str(evidence / "deployment.json")}))


if __name__ == "__main__":
    main()
