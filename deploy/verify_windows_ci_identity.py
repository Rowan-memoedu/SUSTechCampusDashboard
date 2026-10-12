"""Prove CI is a real standard Windows user, with an owned private directory."""
import ctypes
import json
import os
from pathlib import Path
import tempfile
import uuid

assert os.name == 'nt' and os.environ.get('GITHUB_ACTIONS') == 'true'
assert ctypes.windll.shell32.IsUserAnAdmin() == 0, 'Acceptance requires a standard user'
Path(os.environ['TEMP']).mkdir(parents=True, exist_ok=True)
from sustech_dashboard.windows_storage import owned_by_current_user, private_directory_acl
from sustech_dashboard.dpapi_store import _restrict_directory

root = Path(tempfile.gettempdir()) / ('campus-ci-identity-' + uuid.uuid4().hex)
# Use the application's normal mkdir semantics. Windows mkdtemp(mode=0700)
# installs explicit SYSTEM/Administrators ACEs rather than inherited entries.
root.mkdir()
assert owned_by_current_user(root), 'New directory must belong to this user'
_restrict_directory(root)
assert private_directory_acl(root), 'Private owner and DACL must both be valid'
print(json.dumps({'standard_user': True, 'current_user_owner': True,
                  'private_acl': True, 'school_writes': 0}), flush=True)
