"""Windows DPAPI credential storage; plaintext exists only in process memory."""

from __future__ import annotations

import csv
import json
import os
import subprocess
from pathlib import Path

from .core import DATA_ROOT


CREDENTIALS_PATH = DATA_ROOT / "credentials.dpapi.json"


def _pwsh(script: str, value: str) -> str:
    if os.name != "nt":
        raise RuntimeError("凭据存储只支持 Windows DPAPI")
    result = subprocess.run(
        ["pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
        input=value, text=True, encoding="utf-8", capture_output=True, timeout=20,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError("Windows 凭据加解密失败")
    return result.stdout.rstrip("\r\n")


_ENCRYPT = r"""
[Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
$plain = [Console]::In.ReadToEnd()
$secure = ConvertTo-SecureString -String $plain -AsPlainText -Force
ConvertFrom-SecureString -SecureString $secure
"""

_DECRYPT = r"""
[Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
$encrypted = [Console]::In.ReadToEnd().Trim()
$secure = ConvertTo-SecureString -String $encrypted
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try { [Console]::Out.Write([Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)) }
finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
"""


def protect_password(password: str) -> str:
    return _crypt(password.encode("utf-16-le"), protect=True).hex()


def unprotect_password(encrypted: str) -> str:
    return _crypt(bytes.fromhex(encrypted), protect=False).decode("utf-16-le")


def _crypt(data: bytes, *, protect: bool) -> bytes:
    """Windows DPAPI, compatible with existing PowerShell SecureString files."""
    if os.name != "nt":
        raise RuntimeError("DPAPI 凭据只能在原 Windows 用户下使用")
    import ctypes
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                   ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    fn.restype = wintypes.BOOL
    if not fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise RuntimeError("当前 Windows 用户无法读取该加密凭据")
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        ctypes.memset(target.data, 0, target.size)
        kernel32.LocalFree(target.data)


def _restrict_directory(path: Path) -> None:
    user = subprocess.check_output(
        ["whoami", "/user", "/fo", "csv", "/nh"], text=True, encoding="utf-8", timeout=10,
    )
    user_sid = next(csv.reader([user.strip()]))[1]
    subprocess.run(
        ["icacls", str(path), "/inheritance:r", "/grant:r", f"*{user_sid}:(OI)(CI)F"],
        check=True, capture_output=True, text=True, timeout=10,
    )


def save_credentials(sid: str, password: str) -> None:
    if CREDENTIALS_PATH.exists():
        raise FileExistsError("本机凭据已存在；不会覆盖")
    encrypted = protect_password(password)
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    _restrict_directory(DATA_ROOT)
    with CREDENTIALS_PATH.open("x", encoding="utf-8") as handle:
        json.dump({"sid": sid, "password_dpapi": encrypted}, handle, ensure_ascii=False)


def load_credentials() -> tuple[str, str]:
    payload = json.loads(CREDENTIALS_PATH.read_text(encoding="utf-8"))
    sid = payload["sid"]
    password = unprotect_password(payload["password_dpapi"])
    if not sid or not password:
        raise RuntimeError("本机凭据内容无效")
    return sid, password


def save_paired_credentials(sid: str, password: str) -> None:
    """A verified one-use handoff may refresh the password of the same owner."""
    if not CREDENTIALS_PATH.exists():
        save_credentials(sid, password)
        return
    owner, previous = load_credentials()
    if owner != sid:
        raise ValueError('此电脑已有另一校园账号，未覆盖本机凭据')
    if previous == password:
        return
    from .core import save_json
    _restrict_directory(DATA_ROOT)
    save_json(CREDENTIALS_PATH, {'sid': sid, 'password_dpapi': protect_password(password)})
