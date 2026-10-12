"""Record an actual AI review; review/codex-review.json is required.

This script checks evidence bytes. It cannot perform visual or semantic review.
"""
from pathlib import Path
from datetime import date
import hashlib
import json
import re
import sys

from pypdf import PdfReader
from render_booklets import BASE, read_sections, solution_sections


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def validate_ai_review(root, hw, report, review):
    assert review['schema_version'] == 1
    assert review['directory'] == hw['directory']
    assert review['reviewed_ids'] == hw['ids']
    assert review['reviewer'].strip() and review['findings'] == []
    date.fromisoformat(review['date'])
    for stage in ('source', 'solution', 'translation', 'visual'):
        assert review[stage]['status'] == 'PASS', f'{stage} review is not complete'
        assert review[stage]['notes'].strip(), f'{stage} review has no explanation'
    assert review['markdown'] == report['markdown'], 'Review predates current Markdown'
    for name, digest in review['markdown'].items():
        assert sha(root / name) == digest, 'Reviewed Markdown changed: ' + name
    expected_pdfs = {item['file']: item['sha256'] for item in report['pdfs']}
    assert review['pdf_hashes'] == expected_pdfs, 'Review predates current PDFs'
    for item in report['pdfs']:
        assert sha(Path(item['verified_source'])) == item['sha256']
    required_records = {'review/sources.json', 'review/accepted-solutions.json',
                        'review/calculation-review.json', 'review/translation/accepted.json'}
    assert required_records <= set(review['evidence_hashes']), 'Missing review evidence'
    for name, digest in review['evidence_hashes'].items():
        path = (root / name).resolve()
        assert path.is_relative_to(root.resolve()), 'Evidence must be inside this homework'
        assert sha(path) == digest, 'Reviewed evidence changed: ' + name
    expected_pages = {f"{item['module']}-{n:02}.png" for item in report['pdfs']
                      for n in range(1, item['pages'] + 1)}
    assert set(review['rendered_page_hashes']) == expected_pages, 'Review does not cover every current page'
    for name, digest in review['rendered_page_hashes'].items():
        assert sha(root / 'review/pdf-pages' / name) == digest, 'Reviewed page image changed: ' + name


def main():
    assert len(sys.argv) == 2, 'Usage: finalize_booklets.py <registered-homework-directory>'
    matches = [x for x in json.loads((BASE / 'homework_sets.json').read_text(encoding='utf-8'))
               if x['directory'] == sys.argv[1]]
    assert len(matches) == 1, 'Expected one registered homework'
    hw = matches[0]
    root = (BASE / hw['directory']).resolve()
    assert root.is_relative_to(BASE.resolve()) and root != BASE.resolve()
    if (root / 'homework.json').exists():
        assert json.loads((root / 'homework.json').read_text(encoding='utf-8')) == hw
    review_path = root / 'review/codex-review.json'
    assert review_path.is_file(), 'Complete the actual AI review and save review/codex-review.json first'
    review = json.loads(review_path.read_text(encoding='utf-8'))
    ids = hw['ids']
    questions = read_sections(root / '题目.md')
    assert list(questions) == ids
    sources = json.loads((root / 'review/sources.json').read_text(encoding='utf-8'))
    assert sha(Path(sources['textbook'])) == sources['sha256']
    calculation_path = root / 'review/calculation-review.json'
    calculation = json.loads(calculation_path.read_text(encoding='utf-8'))
    assert calculation['status'] == 'PASS'
    accepted = json.loads((root / 'review/accepted-solutions.json').read_text(encoding='utf-8'))
    assert [item['id'] for item in accepted] == ids

    evidence = []
    for item in accepted:
        candidate = Path(item['candidate'])
        source_review_path = Path(item['source_review'])
        assert sha(candidate) == item['sha256']
        assert sha(source_review_path) == item['source_sha256']
        data = json.loads(source_review_path.read_text(encoding='utf-8'))
        outcome = json.loads(data['output'])
        assert outcome['status'] == 'pass' and not outcome['discrepancies']
        assert [str(x) for x in outcome['checked_ids']] == [item['id']]
        assert data['input']['input'] == f"### Problem {item['id']}\n\n{questions[item['id']]}"
        assert data['image_count'] > 0
        image_hashes = [{'path': x['path'], 'sha256': sha(Path(x['path']))} for x in data['input']['images']]
        evidence.append({'id': item['id'], 'record': str(source_review_path), 'sha256': sha(source_review_path), 'images': image_hashes})

    report_path = root / 'review/final-verification.json'
    report = json.loads(report_path.read_text(encoding='utf-8'))
    assert report['pdf_structure'] == 'PASS' and report['translation_review']['status'] == 'PASS'
    validate_ai_review(root, hw, report, review)
    for name, digest in report['markdown'].items():
        assert sha(root / name) == digest
    answers = solution_sections(root, questions)
    for item in report['pdfs']:
        pdf = Path(item['verified_source'])
        assert sha(pdf) == item['sha256'] and item['checks'] == 'PASS'
        if item['module'] != 'solutions':
            continue
        pages = PdfReader(pdf).pages
        full_text = '\n'.join(page.extract_text() for page in pages)
        chunks = re.split(r'^Problem[ \t]+(\d+(?:\.\d+)*)[ \t]*$', full_text, flags=re.M)
        assert [chunks[i] for i in range(1, len(chunks), 2)] == ids
        for i in range(1, len(chunks), 2):
            prose = answers[chunks[i]].split('#### Solution', 1)[1]
            prose = re.sub(r'!\[[^\]]*\]\([^)]*\)|\$\$[\s\S]*?\$\$|\$[^$]+\$', '', prose)
            tokens = re.findall(r'[a-z]+|[\u4e00-\u9fff]', prose.lower())
            actual = ''.join(re.findall(r'[a-z]+|[\u4e00-\u9fff]', chunks[i + 1].split('Solution', 1)[1].lower()))
            cursor = 0
            for token in tokens:
                found = actual.find(token, cursor)
                assert found >= 0, (chunks[i], token)
                cursor = found + len(token)

    report['source_review'] = {
        'status': 'PASS',
        'method': review['source']['notes'],
        'evidence': evidence,
    }
    report['solution_review'] = {
        'status': 'PASS',
        'method': review['solution']['notes'],
        'calculation_record': str(calculation_path),
        'calculation_sha256': sha(calculation_path),
        'accepted': accepted,
    }
    report['visual_review'] = {
        'status': 'PASS',
        'reviewer': review['reviewer'],
        'date': review['date'],
        'method': review['visual']['notes'],
        'pdf_hashes': {item['file']: item['sha256'] for item in report['pdfs']},
    }
    report['manual_markdown_approval_required'] = False
    report['ai_review_record'] = {'path': str(review_path), 'sha256': sha(review_path)}
    save(report_path, report)
    index = BASE / 'workflow/verification.json'
    all_reports = json.loads(index.read_text(encoding='utf-8'))
    all_reports = [r for r in all_reports if (r['subject'], r['homework']) != (hw['subject'], hw['label'])] + [report]
    save(index, all_reports)
    (root / '核验说明.md').write_text(
        f"# {hw['subject']} {hw['label']} 核验\n\n"
        f"审核日期：{review['date']}。题号：{', '.join(ids)}。\n\n"
        + '\n\n'.join(review[stage]['notes'] for stage in ('source', 'solution', 'translation', 'visual'))
        + '\n\n'
        f"三册页数（题目／作答／答案）：{'／'.join(str(x['pages']) for x in report['pdfs'])}。"
        "作答册每题两页，续页仅书写线。PDF 正文顺序、A4 尺寸、编译日志、全页渲染和哈希核验通过。\n",
        encoding='utf-8',
    )
    print(f"PASS: {hw['label']} source, solution, translation and PDF reviews recorded.")


if __name__ == '__main__':
    main()
