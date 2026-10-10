"""Read only this project's test alert headers; never print other mail."""
from email.header import decode_header, make_header
from email.parser import BytesParser
import argparse
import imaplib
import json

from migrate_server_renewal import mail_settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--include-fault-test", action="store_true")
    args = parser.parse_args()
    settings = mail_settings()
    with imaplib.IMAP4_SSL("imap.exmail.qq.com", 993, timeout=30) as client:
        client.login(settings["username"], settings["password"])
        client.select("INBOX", readonly=True)
        status, ids = client.search(None, "ALL")
        if status != "OK":
            raise RuntimeError("Read-only inbox search failed")
        matched = []
        for uid in ids[0].split()[-30:]:
            status, data = client.fetch(uid, "(BODY.PEEK[HEADER.FIELDS (SUBJECT)])")
            if status != "OK":
                continue
            header = next((part[1] for part in data if isinstance(part, tuple)), b"")
            message = BytesParser().parsebytes(header)
            subject = str(make_header(decode_header(message.get("Subject", ""))))
            if subject.startswith("[校园面板续签]"):
                matched.append(subject)
        print(json.dumps({"inbox_verified": bool(matched), "project_alert_subjects": matched}, ensure_ascii=False))
        if not matched:
            raise RuntimeError("Test alert not yet present in the inbox")
        if args.include_fault_test and not all(any(text in subject for subject in matched)
                for text in ("服务器告警通道验收", "验收：续签／更新源检查失败", "验收：续签／更新源检查已恢复")):
            raise RuntimeError("Failure/recovery test alerts not yet present in the inbox")


if __name__ == "__main__":
    main()
