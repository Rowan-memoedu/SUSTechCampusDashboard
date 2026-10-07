"""The public URI can only open the authenticated local instance."""
import os
from pathlib import Path
import sys


def installation_owner(executable=None):
    executable = Path(executable or sys.executable).resolve()
    if os.environ.get('SUSTECH_INSTALL_ROOT'):
        return os.environ['SUSTECH_INSTALL_ROOT']
    if os.name == 'nt':
        from .paths import DATA_ROOT
        if executable.is_relative_to(DATA_ROOT / 'updates' / 'releases'):
            import winreg
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Classes\sustech-campus') as key:
                    return winreg.QueryValueEx(key, 'InstallOwner')[0]
            except OSError:
                pass
    return str(executable.parent)


def parse_uri(value):
    if not isinstance(value, str) or value not in {'sustech-campus://open', 'sustech-campus://open/'}:
        raise ValueError('打开链接无效，请使用 sustech-campus://open')
    return {'action': 'open'}


def register_protocol(executable=None):
    if os.name != 'nt' or (executable is None and not getattr(sys, 'frozen', False)):
        return
    import winreg
    executable = Path(executable or sys.executable).resolve()
    if not executable.is_file() or '"' in str(executable):
        raise ValueError('客户端路径无效')
    root = r'Software\Classes\sustech-campus'
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, root) as key:
        winreg.SetValueEx(key, '', 0, winreg.REG_SZ, 'URL:SUSTech Campus Dashboard')
        winreg.SetValueEx(key, 'URL Protocol', 0, winreg.REG_SZ, '')
        winreg.SetValueEx(key, 'InstallOwner', 0, winreg.REG_SZ,
                         installation_owner(executable))
        winreg.SetValueEx(key, 'RegisteredCommand', 0, winreg.REG_SZ, f'"{executable}" --connect-uri "%1"')
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, root + r'\shell\open\command') as key:
        winreg.SetValueEx(key, '', 0, winreg.REG_SZ, f'"{executable}" --connect-uri "%1"')


def handle_uri(value, port=18765):
    parse_uri(value)  # Invalid input cannot access credentials or start a process.
    from .runtime import supervise
    return supervise(port, open_browser=True)
