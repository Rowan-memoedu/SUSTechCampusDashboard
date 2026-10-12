"""Owner-authorized READ ONLY probes. Output counts/field names, never campus bodies."""
import argparse
import json
from pathlib import Path
import sys
from urllib.parse import urlparse, parse_qs


def main():
    p = argparse.ArgumentParser()
    p.add_argument('service')
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    from sustech_dashboard.authentication import load_owner_credentials
    from sustech_dashboard import academic_extras, campus_extras, browser_services
    from sustech_dashboard.provider import Blackboard
    assert load_owner_credentials()
    report = {'service': a.service, 'school_writes': 0}
    try:
        if a.service == 'announcements':
            bb = Blackboard()
            data = [academic_extras.announcements(bb, c['id']) for c in bb.current_courses()]
            report['courses'] = len(data)
            report['announcements'] = sum(len(x['items']) for x in data)
            report['attachments'] = sum(len(r['attachments']) for x in data for r in x['items'])
            report['download_checks'] = []
            for entry in data:
                for item in entry['items']:
                    for attachment in item['attachments']:
                        path = parse_qs(urlparse(attachment['url']).query)['path'][0]
                        with bb.get(path, stream=True) as response:
                            first = next(response.iter_content(4096), b'')
                            report['download_checks'].append({'status': response.status_code,
                                'mime': response.headers.get('Content-Type', ''), 'nonempty': bool(first),
                                'not_login_html': b'<html' not in first.lower() and b'<!doctype html' not in first.lower()})
            assert all(r['nonempty'] and r['not_login_html'] for r in report['download_checks'])
        elif a.service == 'grades':
            data = academic_extras.gradebook(Blackboard())
            report.update(rows=len(data['rows']), warnings=len(data['warnings']),
                          exemptions=sum(r['exempt'] for r in data['rows']))
            assert not data['warnings']
        elif a.service == 'content':
            data = academic_extras.content_search(Blackboard(), {'q': ''})
            report.update(rows=data['total'], kinds=list(data['counts']), warnings=len(data['warnings']))
            assert not data['warnings']
        else:
            args = {'transit': {'op': 'live'}, 'nces': {'q': 'CS109'}, 'faculty': {'department': '计算机科学与工程系'},
                    'programs': {}, 'mirror': {'q': 'CS109'}, 'library': {'q': 'linear algebra'},
                    'cle': {'op': 'slots'}, 'evaluations': {}}[a.service]
            module = browser_services if a.service in {'library', 'cle', 'evaluations'} else campus_extras
            data = getattr(module, a.service)(args)
            report.update(rows=len(data.get('items', [])), fields=sorted(data['items'][0]) if data.get('items') else [],
                          warnings=len(data.get('warnings', [])))
            assert not data.get('warnings')
        report['state'] = 'passed'
    except Exception as exc:
        report.update(state='failed', error_type=type(exc).__name__)
        # Sanitize errors using the same interface as the product.
        from sustech_dashboard.app import _public_error
        report['message'] = _public_error(exc)
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return 0 if report['state'] == 'passed' else 1


if __name__ == '__main__':
    sys.exit(main())
