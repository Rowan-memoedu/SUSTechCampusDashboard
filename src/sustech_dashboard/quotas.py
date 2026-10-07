"""Bound hosted state growth without removing learner data or write receipts."""
import os
from pathlib import Path


def private_usage(root):
    return sum(path.stat().st_size for path in Path(root).rglob('*') if path.is_file() and
               not path.is_symlink() and path.suffix != '.document' and 'tmp' not in path.relative_to(root).parts)


def check_private_quota(extra=0):
    from .execution import hosted
    if hosted():
        from .paths import DATA_ROOT
        limit = int(os.environ.get('SUSTECH_PRIVATE_QUOTA', str(128*1024*1024)))
        if private_usage(DATA_ROOT) + extra > limit:
            raise ValueError('私密状态已达到 128 MiB 配额，已暂停新增；请联系维护者处理')
