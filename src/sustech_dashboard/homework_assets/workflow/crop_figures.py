"""Make exact, bounded crops of staged original PNG pages. Never redraw figures."""
import json
import sys
from pathlib import Path
from PIL import Image


def crop(root, manifest):
    root = Path(root).resolve()
    for item in manifest['crops']:
        source = (root / item['source']).resolve()
        target = (root / item['target']).resolve()
        if not source.is_relative_to(root) or not target.is_relative_to(root / 'work/review/figure-candidates'):
            raise ValueError('Figure inputs and candidates must stay in the job workspace')
        if target.exists():
            raise ValueError('A figure candidate may not be overwritten')
        box = item['box']
        with Image.open(source) as original:
            left, top, right, bottom = box
            if (len(box) != 4 or any(type(x) is not int for x in box)
                    or not 0 <= left < right <= original.width or not 0 <= top < bottom <= original.height):
                raise ValueError('Crop coordinates are outside the original page')
            target.parent.mkdir(parents=True, exist_ok=True)
            original.crop(box).save(target, format='PNG')


if __name__ == '__main__':
    crop(Path(__file__).resolve().parents[1], json.loads(Path(sys.argv[1]).read_text(encoding='utf-8')))
