"""Read-only PDF helpers. Page numbers are one-based physical PDF pages."""
import json
import sys
from pathlib import Path

from pypdf import PdfReader


def inside(name):
    root = Path.cwd().resolve()
    path = (root / name).resolve()
    if not path.is_relative_to(root):
        raise ValueError('Path is outside the staged homework workspace')
    return path


mode, filename = sys.argv[1:3]
source = inside(filename)
if mode == 'index':
    out = inside(sys.argv[3])
    out.mkdir(parents=True, exist_ok=True)
    reader = PdfReader(source)
    for i, page in enumerate(reader.pages, 1):
        (out / f'{i:04}.txt').write_text(page.extract_text() or '', encoding='utf-8')
    (out / 'index.json').write_text(json.dumps({'file': filename, 'pages': len(reader.pages)}), encoding='utf-8')
    print(json.dumps({'pages': len(reader.pages), 'directory': str(out)}))
elif mode == 'text':
    page, out = int(sys.argv[3]), inside(sys.argv[4])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(PdfReader(source).pages[page - 1].extract_text() or '', encoding='utf-8')
elif mode == 'render':
    import pypdfium2 as pdfium
    page, out = int(sys.argv[3]), inside(sys.argv[4])
    out.parent.mkdir(parents=True, exist_ok=True)
    with pdfium.PdfDocument(source) as pdf:
        p = pdf[page - 1]
        bitmap = p.render(scale=2)
        bitmap.to_pil().save(out)
        bitmap.close()
        p.close()
else:
    raise ValueError('Expected index, text or render')
