"""Local migration: export ONLY online roles, reuse the SUSTech mailbox.

Credentials are decrypted in memory and sent over verified SSH stdin, never
written to an export, repository, public staging directory or command line.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import tomllib
import smtplib
import ssl

from feed_metadata import verified_feed
from publish_release import keys, TRUST_ROOT
from renew_feed import SSH_HOST, SSH_OPTIONS


def mail_settings():
    config = tomllib.loads(Path("D:/AppData/Himalaya/config.toml").read_text())
    account = config["accounts"]["sustech"]
    prefs = Path("D:/AppData/Thunderbird/Profiles/main/prefs.js").read_text()
    match = re.search(r'user_pref\("mail\.smtpserver\.(smtp\d+)\.hostname", "smtp\.exmail\.qq\.com"\)', prefs)
    if not match:
        raise ValueError("Existing SUSTech SMTP configuration not found")
    prefix = "mail.smtpserver." + match[1]
    if f'user_pref("{prefix}.port", 465)' not in prefs or f'user_pref("{prefix}.try_ssl", 3)' not in prefs:
        raise ValueError("Existing SMTP must use implicit TLS on port 465")
    command = account["imap"]["sasl"]["plain"]["password"]["command"]
    password = subprocess.run(command, check=True, capture_output=True, text=True, timeout=30).stdout.strip()
    if not password:
        raise ValueError("Mailbox credential reader returned no credential")
    return {"host": "smtp.exmail.qq.com", "port": 465, "sender": account["email"],
            "recipient": account["email"], "username": account["imap"]["sasl"]["plain"]["username"],
            "password": password}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", help="Validated remote directory containing public code only")
    args = parser.parse_args()
    if not re.fullmatch(r"/tmp/sustech-server-renewal-[A-Za-z0-9]+", args.stage):
        raise ValueError("Invalid remote stage")
    signers = keys()
    online = {role: signers[role].private_bytes.decode() for role in ("snapshot", "timestamp")}
    mail = mail_settings()
    # Validate the recovered unified mailbox credential before provisioning.
    with smtplib.SMTP_SSL(mail["host"], mail["port"], timeout=30, context=ssl.create_default_context()) as smtp:
        smtp.login(mail["username"], mail["password"])
    payload = json.dumps({"online_keys": online, "mail": mail})
    result = subprocess.run(["ssh", *SSH_OPTIONS, SSH_HOST,
        f"sudo -n /opt/sustech-campus-renewal/venv/bin/python {args.stage}/provision_server_renewal.py {args.stage}"],
        input=payload, text=True, capture_output=True, timeout=120)
    # Never echo subprocess output on a credential-transport failure.
    if result.returncode:
        raise RuntimeError("Restricted server provisioning failed; inspect server journal without printing credentials")
    print(result.stdout.strip())


if __name__ == "__main__":
    main()
