"""Observe the installed local panel's pending review without approving anything."""
import argparse
import json
from pathlib import Path

from sustech_dashboard.browser_services import browser_context


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--job', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--stage', choices=('awaiting_source', 'awaiting_pdf'), default='awaiting_source')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    base = 'http://127.0.0.1:18765'
    with browser_context() as context:
        assert context.request.post(base + '/auth/unlock', headers={'Origin': base},
            data={'token': (args.root / 'browser-token').read_text()}).status == 200
        status = context.request.get(base + '/api/homework').json()
        job = next(x for x in status['jobs'] if x['id'] == args.job)
        assert job['state'] == args.stage
        page = context.new_page()
        writes, errors = [], []
        page.on('request', lambda req: writes.append(req.url) if req.method not in {'GET', 'HEAD'} else None)
        page.on('pageerror', lambda exc: errors.append(str(exc)))
        page.set_viewport_size({'width': 1280, 'height': 980})
        page.goto(base, wait_until='domcontentloaded')
        dialog = page.locator('.homework-dialog')
        dialog.wait_for(state='visible')
        assert job['name'] in dialog.inner_text()
        if args.stage == 'awaiting_source':
            assert not job['artifacts']
            assert all(value in dialog.locator('.homework-ids').inner_text() for value in job['plan']['ids'])
            assert dialog.get_by_role('button', name='确认，生成三册').is_enabled()
        else:
            assert {x['module'] for x in job['artifacts']} == {'questions', 'writing', 'solutions'}
            assert dialog.get_by_role('button', name='确认正确，开启自动化').is_enabled()
            from sustech_dashboard.homework_store import HomeworkStore
            originals = HomeworkStore(args.root / 'homework').get(args.job)['data']['artifacts']
            for artifact in originals:
                link = dialog.locator('a[href$="/file/' + artifact['module'] + '"]')
                assert link.count() == 1
                response = context.request.get(base + link.get_attribute('href'))
                assert response.status == 200 and response.body().startswith(b'%PDF-')
                assert response.body() == Path(artifact['path']).read_bytes()
        page.screenshot(path=str(args.output / 'live-ch9-confirmation.png'))
        after = context.request.get(base + '/api/homework').json()
        assert next(x for x in after['jobs'] if x['id'] == args.job)['state'] == args.stage
        assert not errors and not writes, (errors, writes)
        report = {'installed_ui_visible': True, 'job': job['id'], 'state': job['state'], 'ids': job['plan']['ids'],
                  'book': job['plan']['books'][0]['title'], 'no_confirmation_sent': True, 'page_errors': errors}
        (args.output / 'live-review.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report))


if __name__ == '__main__': main()
