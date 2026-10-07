"""Exercise installer registration and uninstall in a disposable directory."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import winreg

PROTOCOL = r'Software\Classes\sustech-campus'
OWNER = r'Software\SUSTechCampusDashboard'
RUN = r'Software\Microsoft\Windows\CurrentVersion\Run'
NAME = 'SUSTechCampusDashboard'


def snapshot(path):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            values, children = [], {}
            for i in range(winreg.QueryInfoKey(key)[1]):
                values.append(winreg.EnumValue(key, i))
            for i in range(winreg.QueryInfoKey(key)[0]):
                child = winreg.EnumKey(key, i)
                children[child] = snapshot(path + '\\' + child)
            return values, children
    except FileNotFoundError:
        return None


def delete_key(path):
    value = snapshot(path)
    if value is None: return
    for child in value[1]: delete_key(path + '\\' + child)
    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)


def restore(path, value):
    delete_key(path)
    if value is None: return
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, path) as key:
        for name, data, kind in value[0]: winreg.SetValueEx(key, name, 0, kind, data)
    for child, data in value[1].items(): restore(path + '\\' + child, data)


def registry_value(path, name=''):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            return winreg.QueryValueEx(key, name)
    except FileNotFoundError:
        return None


def verify(installer, cache):
    cache.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix='installer-', dir=cache)).resolve()
    program, data = root / 'program', root / 'personal-data'
    data.mkdir()
    retained = data / 'fixture.txt'
    retained.write_bytes(b'personal data remains')
    before = {path: snapshot(path) for path in (PROTOCOL, OWNER)}
    previous_run = registry_value(RUN, NAME)
    shortcut = Path(os.environ['APPDATA']) / 'Microsoft/Windows/Start Menu/Programs/南科大校园面板.lnk'
    shortcut_bytes = shortcut.read_bytes() if shortcut.exists() else None
    (root / 'temp').mkdir()
    env = dict(os.environ, SUSTECH_DASHBOARD_DATA_ROOT=str(data), SUSTECH_REGISTER_PROTOCOL='0',
               TMP=str(root / 'temp'), TEMP=str(root / 'temp'))
    uninstall = program / 'unins000.exe'
    try:
        subprocess.run([str(installer), '/VERYSILENT', '/SP-', '/SUPPRESSMSGBOXES', '/NORESTART',
                        '/NOLAUNCH=1', '/TASKS=autostart', '/DIR=' + str(program), '/LOG=' + str(root / 'install.log')],
                       env=env, check=True, timeout=120)
        assert (program / 'campus-client.exe').is_file()
        expected = f'"{program / "campus-client.exe"}" --connect-uri "%1"'
        assert registry_value(PROTOCOL + r'\shell\open\command')[0] == expected
        assert registry_value(PROTOCOL, 'InstallOwner')[0] == str(program)
        assert registry_value(RUN, NAME)[0] == f'"{program / "campus-client.exe"}" --no-browser'
        assert shortcut.exists()
        # A later application taking over the command must survive uninstall.
        takeover = 'fixture takeover; never executed'
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, PROTOCOL + r'\shell\open\command') as key:
            winreg.SetValueEx(key, '', 0, winreg.REG_SZ, takeover)
        subprocess.run([str(uninstall), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/LOG=' + str(root / 'uninstall.log')],
                       env=env, check=True, timeout=120)
        # Inno returns the original process's exit code before its temporary
        # clone completes usPostUninstall. Wait for the actual registry result.
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and registry_value(RUN, NAME) is not None:
            time.sleep(.1)
        assert not (program / 'campus-client.exe').exists()
        assert registry_value(PROTOCOL + r'\shell\open\command')[0] == takeover
        assert registry_value(RUN, NAME) is None
        assert retained.read_bytes() == b'personal data remains'
        result = {'installed': True, 'current_user_registration': True, 'optional_autostart': True,
                  'uninstall_preserved_takeover': True, 'personal_data_retained': True,
                  'automatic_browser_launch': 'deferred_to_interactive_install', 'evidence': str(root)}
        (root / 'result.json').write_text(json.dumps(result, indent=2))
        print(json.dumps(result))
    finally:
        if (program / 'campus-client.exe').exists() and uninstall.exists():
            subprocess.run([str(uninstall), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART'], timeout=120, check=True)
        for path, value in before.items(): restore(path, value)
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN) as key:
            if previous_run is None:
                try: winreg.DeleteValue(key, NAME)
                except FileNotFoundError: pass
            else:
                winreg.SetValueEx(key, NAME, 0, previous_run[1], previous_run[0])
        if shortcut_bytes is not None:
            shortcut.write_bytes(shortcut_bytes)
        elif shortcut.exists():
            shortcut.unlink()
        assert {path: snapshot(path) for path in before} == before
        assert registry_value(RUN, NAME) == previous_run


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('installer', type=Path)
    p.add_argument('--cache', required=True, type=Path)
    args = p.parse_args()
    verify(args.installer, args.cache)
