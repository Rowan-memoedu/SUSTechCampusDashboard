"""Current-user launch preferences; never re-enable old scheduled tasks."""
import os
from pathlib import Path
import sys

RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
RUN_VALUE = 'SUSTechCampusDashboard'
OWNER_KEY = r'Software\SUSTechCampusDashboard'


def registered_autostart():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, OWNER_KEY) as key:
            return winreg.QueryValueEx(key, 'AutostartCommand')[0]
    except OSError:
        return ''


def autostart_supported():
    return os.name == 'nt' and bool(getattr(sys, 'frozen', False))


def autostart_enabled():
    if os.name != 'nt':
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, RUN_VALUE)
        return bool(registered_autostart()) and value == registered_autostart()
    except OSError:
        return False


def set_autostart(enabled):
    if not isinstance(enabled, bool) or not autostart_supported():
        raise ValueError('后台启动设置需要 Windows 分发客户端')
    import winreg
    command = f'"{Path(sys.executable).resolve()}" --no-browser'
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        if enabled:
            winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, command)
        else:
            try:
                value, _ = winreg.QueryValueEx(key, RUN_VALUE)
                if value == registered_autostart():
                    winreg.DeleteValue(key, RUN_VALUE)
            except FileNotFoundError:
                pass
    if enabled:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, OWNER_KEY) as key:
            winreg.SetValueEx(key, 'AutostartOwner', 0, winreg.REG_SZ,
                             os.environ.get('SUSTECH_INSTALL_ROOT', str(Path(sys.executable).parent)))
            winreg.SetValueEx(key, 'AutostartCommand', 0, winreg.REG_SZ, command)


def preferences(root, current_download):
    from .core import load_json
    saved = load_json(root / 'instance.json', {})
    return {'download_root': saved.get('download_root', str(current_download)),
            'autostart': autostart_enabled(), 'autostart_supported': autostart_supported()}


def save_preferences(root, payload):
    from .core import load_json, save_json
    if not isinstance(payload, dict) or set(payload) - {'download_root', 'autostart'}:
        raise ValueError('设置字段无效')
    value = payload.get('download_root')
    if not isinstance(value, str) or not value.strip() or len(value) > 500 or '\x00' in value:
        raise ValueError('请输入完整的资料保存目录')
    path = Path(value).expanduser()
    if not path.is_absolute() or str(path) == path.anchor or (path.exists() and not path.is_dir()):
        raise ValueError('资料目录必须是绝对路径下的文件夹，不能是磁盘根目录或文件')
    path = path.resolve()
    if root.resolve() == path or root.resolve().is_relative_to(path) or path.is_relative_to(root.resolve()):
        raise ValueError('资料目录须与私密运行数据目录分开')
    enabled = payload.get('autostart')
    if enabled is not None:
        set_autostart(enabled)
    saved = load_json(root / 'instance.json', {})
    save_json(root / 'instance.json', {**saved, 'download_root': str(path)})
    return {'ok': True, 'message': '设置已保存。资料目录在下次启动时生效，原有文件保持原位。'}
