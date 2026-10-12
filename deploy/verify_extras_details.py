"""Read-only detail canaries. Reports counts/shape, without private school bodies."""
import argparse
import json
from pathlib import Path
import traceback


def main():
    p = argparse.ArgumentParser()
    p.add_argument('service')
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    from sustech_dashboard.authentication import load_owner_credentials
    from sustech_dashboard import campus_extras as c, browser_services as b
    load_owner_credentials()
    checks = []
    def run(name, fn):
        try:
            data = fn()
            checks.append({'check': name, 'state': 'passed', 'rows': len(data.get('items', [])),
                           'fields': sorted(data['items'][0]) if data.get('items') else []})
            return data
        except Exception as exc:
            checks.append({'check': name, 'state': 'failed', 'error': type(exc).__name__,
                           'location': traceback.extract_tb(exc.__traceback__)[-1].name})
            return {'items': []}
    s = args.service
    if s == 'transit':
        lines = run('lines', lambda: c.transit({}))
        if lines['items']:
            run('schedule', lambda: c.transit({'op': 'schedule', 'line': lines['items'][0]['id']}))
        places = run('places', lambda: c.transit({'op': 'places'}))
        for direction in (0, 1):
            run('stops-' + str(direction), lambda: c.transit({'op': 'stops', 'direction': direction}))
        if len(places['items']) >= 2:
            run('route', lambda: c.transit({'op': 'route', 'from': places['items'][0]['facility_id'],
                                           'to': places['items'][1]['facility_id']}))
    elif s == 'nces':
        rows = run('code-search', lambda: c.nces({'q': 'CS109'}))
        run('title-search', lambda: c.nces({'q': '线性代数'}))
        run('index', lambda: c.nces({}))
        if rows['items']:
            row = rows['items'][0]
            run('detail', lambda: c.nces({'op': 'detail', 'id': row['id']}))
            run('teacher', lambda: c.nces({'teacher': row['teacher_names'].split(',')[0]}))
    elif s == 'faculty':
        rows = run('list', lambda: c.faculty({'department': '计算机科学与工程系'}))
        if rows['items']:
            run('profile', lambda: c.faculty({'op': 'detail', 'id': rows['items'][0]['slug']}))
    elif s == 'programs':
        rows = run('search', lambda: c.programs({'q': '交换'}))
        if rows['items']:
            run('detail', lambda: c.programs({'op': 'detail', 'id': rows['items'][0]['ID']}))
    elif s == 'mirror':
        run('pdf-text', lambda: c.mirror({'q': 'CS109', 'text': '1'}))
        for kind in ('program', 'handbook', 'map'):
            run(kind, lambda: c.mirror({'kind': kind}))
    elif s == 'library':
        rows = run('filtered-search', lambda: b.library({'q': 'linear algebra', 'kind': 'Book', 'fulltext': '1'}))
        if rows['items']:
            run('detail', lambda: b.library({'op': 'detail', 'id': rows['items'][min(1, len(rows['items'])-1)]['id']}))
        run('no-results', lambda: b.library({'q': 'ZZZqqqNonexistentRecord847192'}))
    elif s == 'cle':
        for op in ('catalog', 'mine', 'walkin'):
            run(op, lambda: b.cle({'op': op}))
    elif s == 'borrow':
        from sustech_dashboard.study import semester
        from sustech_survival.tis.classroom.booking import VenueBorrowClient
        permission = VenueBorrowClient().check_permission(semester())
        checks.append({'check': 'permission', 'state': 'passed', 'allowed': permission.allowed})
    report = {'school_writes': 0, 'service': s, 'checks': checks,
              'state': 'passed' if checks and all(c['state'] == 'passed' for c in checks) else 'failed'}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return int(report['state'] != 'passed')


if __name__ == '__main__':
    raise SystemExit(main())
