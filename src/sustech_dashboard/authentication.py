"""Local credential bootstrap and a strict replacement for upstream CAS TLS."""
import json
import os
from pathlib import Path

from .paths import DATA_ROOT


def strict_cas_session(self):
    import requests
    # Do not inherit the upstream adapter that forces verify=False.
    session = requests.Session()
    session.verify = True
    return session


def secure_upstream():
    os.environ["SUSTECH_CREDENTIALS"] = str(DATA_ROOT / "disabled-plaintext-credentials")
    os.environ["SUSTECH_HOME"] = str(DATA_ROOT / "upstream")
    from sustech_survival.sso.providers.cas import CASAuthorizer
    CASAuthorizer._build_cas_session = strict_cas_session


def set_credentials(sid, password):
    secure_upstream()
    if not isinstance(sid, str) or not isinstance(password, str) or not sid.strip() or not password:
        raise ValueError("请输入学号和 CAS 密码")
    if len(sid) > 128 or len(password) > 1024 or any(c in sid + password for c in "\r\n\x00"):
        raise ValueError("账号或密码格式无效")
    from sustech_survival.sso import cred_set
    cred_set(sid=sid.strip(), pwd=password)


def clear_credentials():
    from sustech_survival.sso import cred_clear
    from sustech_survival.sso.authorizer import Authorizer
    cred_clear()
    for auth in Authorizer._instances.values():
        cached = getattr(auth, "_cached_session", None)
        if cached:
            cached.close()
    Authorizer._instances.clear()


def load_owner_credentials():
    secure_upstream()
    directory = os.environ.get("CREDENTIALS_DIRECTORY")
    if directory:
        value = json.loads((Path(directory) / "sustech-cas").read_text(encoding="utf-8"))
        set_credentials(value["sid"], value["password"])
        return True
    if os.name == "nt":
        from .dpapi_store import CREDENTIALS_PATH, load_credentials
        if CREDENTIALS_PATH.exists():
            set_credentials(*load_credentials())
            return True
    return False


def configure_credentials(sid, password, remember):
    if remember and os.name != "nt":
        raise ValueError("Linux 持久登录请使用本机 systemd 加密凭据；网页仅支持本次运行")
    set_credentials(sid, password)
    try:
        from .provider import Blackboard
        Blackboard()  # Verify against the school before saving anything.
        if remember:
            from .dpapi_store import save_credentials
            save_credentials(sid.strip(), password)
    except Exception:
        clear_credentials()
        raise
