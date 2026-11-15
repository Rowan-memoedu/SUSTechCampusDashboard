"""Isolated, source-neutral image sizing regression and independent PDF canary."""
import argparse
import importlib.util
import json
from pathlib import Path

from sustech_dashboard.homework_backend import CodexBackend, command, toolchain
ASSETS = Path(__file__).resolve().parents[1] / 'src/sustech_dashboard/homework_assets/workflow'


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--review', action='store_true')
    args = p.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    chain = toolchain()
    # Use the same image/PDF-enabled interpreter as real homework production.
    if not all(chain.get(n) for n in ('python', 'tectonic')):
        raise ValueError('Image/PDF Python and Tectonic are required')
    import pdfplumber
    import pypdfium2 as pdfium
    from PIL import Image, ImageDraw, ImageFont
    spec = importlib.util.spec_from_file_location('diagram_canary_renderer', ASSETS / 'render_booklets.py')
    renderer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(renderer)
    font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 26)
    diagrams = [('portrait', (700, 1400)), ('landscape', (1400, 500)), ('square', (800, 800))]
    for name, (w, h) in diagrams:
        im = Image.new('RGB', (w, h), 'white')
        d = ImageDraw.Draw(im)
        for x in range(80, w - 70, 100):
            d.line((x, 80, x, h - 80), fill='#ccd4da', width=2)
            d.text((x, h - 65), str(x // 100), font=font, fill='black')
        for y in range(100, h - 70, 100):
            d.line((80, y, w - 80, y), fill='#ccd4da', width=2)
            d.text((10, y), str(y // 100), font=font, fill='black')
        d.line((80, h - 80, w - 65, h - 80), fill='black', width=4)
        d.line((80, h - 80, 80, 70), fill='black', width=4)
        d.text((110, 25), name + ' coordinate diagram', font=font, fill='black')
        d.line((140, h - 150, w - 140, 180), fill='#2563a6', width=5)
        d.text((w - 190, 135), 'B (x, y)', font=font, fill='black')
        d.text((140, h - 145), 'A (0, 0)', font=font, fill='black')
        im.save(root / (name + '.png'))
    dimensions = {}
    for variant in ('legacy', 'current'):
        parts = [renderer.PREAMBLE]
        for name, _ in diagrams:
            parts.append(renderer.convert('#### ' + name + '\nLabels and coordinates must remain readable.\n\n![' + name + '](' + name + '.png)\n\nEnd of diagram.\n', root,
                image_height_cm=5.2 if variant == 'legacy' else 22,
                image_width=0.8 if variant == 'legacy' else 1.0))
        parts.append('\\end{document}')
        tex = root / (variant + '.tex')
        tex.write_text(''.join(parts), encoding='utf-8')
        command([chain['tectonic'], '--keep-logs', '--outdir', str(root), str(tex)], root, root / (variant + '-compile.log'), timeout=600)
        with pdfplumber.open(root / (variant + '.pdf')) as pdf:
            dimensions[variant] = [{'width': im['width'], 'height': im['height']} for page in pdf.pages for im in page.images]
        assert len(dimensions[variant]) == len(diagrams)
    assert dimensions['current'][0]['height'] > dimensions['legacy'][0]['height'] * 3.5
    assert dimensions['current'][1]['width'] > 480
    assert dimensions['current'][2]['width'] > 480
    log = (root / 'current.log').read_text(encoding='utf-8', errors='replace')
    assert 'Overfull \\hbox' not in log and 'Overfull \\vbox' not in log and 'Missing character:' not in log
    images = []
    with pdfium.PdfDocument(root / 'current.pdf') as pdf:
        for i, page in enumerate(pdf):
            name = f'current-{i+1:02}.png'
            page.render(scale=1.5).to_pil().save(root / name)
            images.append(name)
    report = {'isolated_fixture': True, 'no_school_material': True, 'dimensions_pt': dimensions,
              'regression': 'PASS', 'rendered_pages': images}
    if args.review:
        schema = {'type': 'object', 'properties': {
            'status': {'type': 'string', 'enum': ['PASS', 'FAIL']},
            'reviewed_pages': {'type': 'array', 'items': {'type': 'string'}},
            'findings': {'type': 'array', 'items': {'type': 'string'}}, 'notes': {'type': 'string'}},
            'required': ['status', 'reviewed_pages', 'findings', 'notes'], 'additionalProperties': False}
        result = CodexBackend({'read_only': True}).run(root, 'independent-diagram-canary',
            'Review the attached synthetic PDF pages at their original resolution. This is an isolated renderer canary, not student homework. '
            'Read only this canary workspace and the attached images; do not read preferences, external files or other tasks. '
            'Check every page for readable diagram ticks, vertex labels and captions, preserved aspect ratio, clipping, overlap and page breaks. '
            'Do not edit any file or infer approval of real homework. Return PASS only when all pages are usable. '
            'reviewed_pages must list exactly these images: ' + json.dumps(images), schema, images=images)
        (root / 'independent-review.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
        assert result['status'] == 'PASS' and not result['findings'] and set(result['reviewed_pages']) == set(images), result
        report['independent_visual_review'] = result
    (root / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
