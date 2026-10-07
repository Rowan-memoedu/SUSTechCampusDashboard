"""Never let test collection import the owner's live data paths or pairing."""
import os
from pathlib import Path
import tempfile

cache = Path(os.environ.get('SUSTECH_TEST_CACHE',
    'D:/Caches/SUSTechCampusDashboard' if os.name == 'nt' and Path('D:/').is_dir() else tempfile.gettempdir()))
cache.mkdir(parents=True, exist_ok=True)
root = Path(tempfile.mkdtemp(prefix='pytest-owner-', dir=cache))
os.environ['SUSTECH_DASHBOARD_DATA_ROOT'] = str(root)
os.environ['SUSTECH_DOWNLOAD_ROOT'] = str(root / 'files')
os.environ['SUSTECH_REGISTER_PROTOCOL'] = '0'
os.environ['SUSTECH_EXECUTION_MODE'] = 'local'
for key in ('SUSTECH_CLOUD', 'CREDENTIALS_DIRECTORY', 'SUSTECH_PROXY_PREFIX', 'SUSTECH_PUBLIC_HOST'):
    os.environ.pop(key, None)
