"""Observe visible console windows while exercising an isolated Windows binary."""
import argparse
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import threading
import time

from verify_client import verify


def console_window_info():
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    found = {}
    @callback_type
    def visit(window, _):
        name = ctypes.create_unicode_buffer(128)
        user32.GetClassNameW(window, name, len(name))
        if name.value in {'ConsoleWindowClass', 'CASCADIA_HOSTING_WINDOW_CLASS'} and user32.IsWindowVisible(window):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(window, ctypes.byref(pid))
            found[int(window)] = {'pid': pid.value, 'class': name.value}
        return True
    if not user32.EnumWindows(visit, 0):
        raise ctypes.WinError(ctypes.get_last_error())
    return found


def console_windows():
    return set(console_window_info())


def process_parents():
    """Native process ownership only; never collect command lines or window titles."""
    class Entry(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('usage', wintypes.DWORD),
                    ('pid', wintypes.DWORD), ('heap', ctypes.c_size_t),
                    ('module', wintypes.DWORD), ('threads', wintypes.DWORD),
                    ('parent', wintypes.DWORD), ('priority', wintypes.LONG),
                    ('flags', wintypes.DWORD), ('name', wintypes.WCHAR * 260)]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        entry = Entry()
        entry.size = ctypes.sizeof(entry)
        found = {}
        valid = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while valid:
            found[entry.pid] = {'parent': entry.parent, 'name': entry.name}
            valid = kernel.Process32NextW(snapshot, ctypes.byref(entry))
        return found
    finally:
        kernel.CloseHandle(snapshot)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('executable', type=Path)
    p.add_argument('--cache', type=Path, required=True)
    p.add_argument('--report', type=Path, required=True)
    p.add_argument('--expect-console', action='store_true', help='Negative control on the old binary')
    a = p.parse_args()
    initial, observed = console_windows(), set()
    stop = threading.Event()
    failures = []
    samples = []
    def monitor():
        try:
            while not stop.is_set():
                observed.update(console_windows() - initial)
                samples.append(time.monotonic())
                stop.wait(.005)
        except Exception as exc:
            failures.append(type(exc).__name__)
    thread = threading.Thread(target=monitor, daemon=True)
    thread.start()
    try:
        result = verify(a.executable.resolve(), a.cache.resolve())
    finally:
        stop.set()
        thread.join(timeout=5)
    assert not failures and len(samples) > 1, 'Console observation failed'
    result.update(new_visible_console_windows=len(observed), observation_samples=len(samples),
                  negative_control=a.expect_console)
    a.report.parent.mkdir(parents=True, exist_ok=True)
    a.report.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result), flush=True)
    assert bool(observed) == a.expect_console, 'Unexpected visible console result'


if __name__ == '__main__':
    main()
