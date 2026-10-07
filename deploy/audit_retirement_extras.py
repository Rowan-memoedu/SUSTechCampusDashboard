"""Read-only ownership review; keep private paths out of stdout."""
import json
from pathlib import Path
import pwd
import re
import subprocess

from retire_hosted import WORK, ROOTS, load, save


def main():
    inv = load('inventory.json')
    units = {u['name'] for u in inv['units']}
    account_names = {a['name'] for a in inv['accounts']}
    refs, namespaces = [], set()
    for root in (Path('/etc/systemd/system'), Path('/usr/lib/systemd/system')):
        for path in root.rglob('*'):
            if not path.is_file() or path.is_symlink():
                continue
            try:
                text = path.read_text()
            except (UnicodeError, OSError):
                continue
            for line in text.splitlines():
                if re.match(r'\s*(User|Group)=', line) and line.split('=', 1)[1].strip() in account_names:
                    refs.append({'file': str(path), 'line': line, 'recognized_unit': path.name in units or path.parent.name in {x+'.d' for x in units}})
                if path.name in units and line.startswith('LogNamespace='):
                    namespaces.add(line.split('=', 1)[1].strip())
    extras = []
    for namespace in namespaces:
        if not re.fullmatch(r'[A-Za-z0-9_-]+', namespace):
            raise ValueError('Invalid namespace')
        for base in (Path('/var/log/journal'), Path('/run/log/journal')):
            extras.extend(str(path) for path in base.glob('*.' + namespace))
        for name in (f'/etc/systemd/journald@{namespace}.conf', f'/etc/systemd/journald@{namespace}.conf.d'):
            if Path(name).exists():
                extras.append(name)
    active = []
    for path in Path('/proc').iterdir():
        if path.name.isdigit():
            try:
                if path.stat().st_uid in {a['uid'] for a in inv['accounts']}:
                    active.append(int(path.name))
            except FileNotFoundError:
                pass
    groups = []
    import grp
    for a in inv['accounts']:
        group = grp.getgrgid(a['gid'])
        other = [p.pw_name for p in pwd.getpwall() if p.pw_gid == a['gid'] and p.pw_name != a['name']]
        groups.append({'name': group.gr_name, 'gid': group.gr_gid, 'other_users': other, 'members': group.gr_mem})
    report = {'account_refs': refs, 'namespaces': sorted(namespaces), 'extras': sorted(set(extras)),
              'active_account_processes': active, 'groups': groups}
    expression = []
    for account in inv['accounts']:
        if expression:
            expression.append('-o')
        expression.extend(('-uid', str(account['uid'])))
    found = subprocess.run(['find', '/etc', '/opt', '/var', '/home', '/tmp', '/srv', '/usr/local', '-xdev',
                            '(', *expression, ')', '-print'], capture_output=True, text=True, timeout=120)
    outside = [name for name in found.stdout.splitlines()
               if not any(Path(name) == root or Path(name).is_relative_to(root) for root in ROOTS)]
    report['owned_outside_roots'] = outside
    save('extra-audit.json', report)
    print(json.dumps({'unit_account_references': len(refs), 'unclassified_references': sum(not r['recognized_unit'] for r in refs),
                      'namespace_count': len(namespaces), 'extra_paths': len(extras), 'account_processes': len(active),
                      'shared_groups': sum(bool(g['other_users'] or g['members']) for g in groups),
                      'owned_outside_roots': len(outside)}))


if __name__ == '__main__':
    main()
