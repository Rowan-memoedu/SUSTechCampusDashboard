"""Bind an explicitly chosen local textbook to one existing course, without approving jobs."""
import argparse
import json
from pathlib import Path

from sustech_dashboard.core import load_json
from sustech_dashboard.homework_sources import local_reference
from sustech_dashboard.homework_store import HomeworkStore
from sustech_dashboard.paths import DATA_ROOT


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=DATA_ROOT)
    parser.add_argument('--course-id', required=True)
    parser.add_argument('--file', type=Path, required=True)
    parser.add_argument('--title', default='')
    parser.add_argument('--expected-sha256')
    args = parser.parse_args()
    snapshot = load_json(args.root / 'snapshot.json', {})
    if args.course_id not in {x['course_id'] for x in snapshot.get('assignments', [])}:
        raise ValueError('The course is not in the current local assignment snapshot')
    reference = local_reference(args.file, args.title)
    if args.expected_sha256 and reference['sha256'] != args.expected_sha256.lower():
        raise ValueError('The source PDF differs from the expected original')
    store = HomeworkStore(args.root / 'homework')
    key = 'course_references:' + args.course_id
    references = store.setting(key, [])
    if reference['key'] not in {x['key'] for x in references}:
        references.append(reference)
        store.set_setting(key, references)
    print(json.dumps({'course_id': args.course_id, 'reference_key': reference['key'],
        'sha256': reference['sha256'], 'source_preserved': True, 'job_confirmation_sent': False}))


if __name__ == '__main__':
    main()
