"""Read Windows ownership without credentials, subprocesses, or ACL changes."""
import os


def owned_by_current_user(path):
    return _current_user_security(path)


def private_directory_acl(path):
    """True only for an owned, protected DACL allowing this user alone."""
    return _current_user_security(path, private_acl=True)


def _current_user_security(path, *, private_acl=False):
    if os.name != "nt":
        return False
    import ctypes
    from ctypes import wintypes

    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    pointer = ctypes.c_void_p
    advapi.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        ctypes.POINTER(pointer), pointer, pointer, pointer, ctypes.POINTER(pointer),
    ]
    advapi.GetNamedSecurityInfoW.restype = wintypes.DWORD
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    advapi.OpenProcessToken.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi.OpenProcessToken.restype = wintypes.BOOL
    advapi.GetTokenInformation.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, pointer, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi.GetTokenInformation.restype = wintypes.BOOL
    advapi.EqualSid.argtypes = [pointer, pointer]
    advapi.EqualSid.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.LocalFree.argtypes = [pointer]
    kernel.LocalFree.restype = pointer

    owner, descriptor, dacl, token = pointer(), pointer(), pointer(), wintypes.HANDLE()
    try:
        # SE_FILE_OBJECT and OWNER_SECURITY_INFORMATION; no data files are read.
        if advapi.GetNamedSecurityInfoW(str(path), 1, 5 if private_acl else 1, ctypes.byref(owner),
                                      None, ctypes.byref(dacl) if private_acl else None,
                                      None, ctypes.byref(descriptor)):
            return False
        if not owner or not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 8,
                                                   ctypes.byref(token)):
            return False
        size = wintypes.DWORD()
        # TokenUser: first query the required buffer size, then its SID.
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        if not size.value:
            return False
        buffer = ctypes.create_string_buffer(size.value)
        if not advapi.GetTokenInformation(token, 1, buffer, size.value,
                                         ctypes.byref(size)):
            return False
        user_sid = ctypes.cast(buffer, ctypes.POINTER(pointer))[0]
        if not advapi.EqualSid(owner, user_sid):
            return False
        if not private_acl:
            return True
        advapi.GetSecurityDescriptorControl.argtypes = [
            pointer, ctypes.POINTER(wintypes.WORD), ctypes.POINTER(wintypes.DWORD),
        ]
        advapi.GetSecurityDescriptorControl.restype = wintypes.BOOL
        advapi.GetAce.argtypes = [pointer, wintypes.DWORD, ctypes.POINTER(pointer)]
        advapi.GetAce.restype = wintypes.BOOL
        control, revision, ace = wintypes.WORD(), wintypes.DWORD(), pointer()
        if not advapi.GetSecurityDescriptorControl(descriptor, ctypes.byref(control),
                                                  ctypes.byref(revision)):
            return False
        # SE_DACL_PROTECTED; a NULL DACL allows everyone and must never pass.
        if not control.value & 0x1000 or not dacl:
            return False
        # ACL header's AceCount is the WORD at byte offset 4.
        if ctypes.cast(dacl, ctypes.POINTER(wintypes.WORD))[2] != 1:
            return False
        if not advapi.GetAce(dacl, 0, ctypes.byref(ace)):
            return False
        header = ctypes.string_at(ace, 8)
        # ACCESS_ALLOWED_ACE, explicit OI/CI inheritance and FILE_ALL_ACCESS.
        if header[0] != 0 or header[1] != 3 or int.from_bytes(header[4:8], "little") != 0x1F01FF:
            return False
        if int.from_bytes(header[2:4], "little") < 16:
            return False
        return bool(advapi.EqualSid(pointer(ace.value + 8), user_sid))
    finally:
        if token:
            kernel.CloseHandle(token)
        if descriptor:
            kernel.LocalFree(descriptor)
