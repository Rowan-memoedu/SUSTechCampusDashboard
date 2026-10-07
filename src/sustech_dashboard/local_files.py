"""Open only existing downloaded documents from this local account's receipts."""
import os
import subprocess
from pathlib import Path

from flask import jsonify, render_template, request

from .core import DATA_ROOT, DOWNLOAD_ROOT, load_json

DOCUMENTS = {'.pdf','.doc','.docx','.ppt','.pptx','.xls','.xlsx','.txt','.csv','.png','.jpg','.jpeg','.gif','.zip'}


def target(key=None, kind='root'):
    root = Path(DOWNLOAD_ROOT).resolve()
    if kind == 'root':
        root.mkdir(parents=True, exist_ok=True)
        return root
    if kind not in {'file','folder'}:raise ValueError('打开方式无效')
    receipt = load_json(DATA_ROOT / 'downloaded-files.json', {}).get(key, {})
    relative = receipt.get('path')
    if not isinstance(relative, str) or Path(relative).is_absolute():raise ValueError('此附件没有本机保存记录')
    path = root / relative
    resolved = path.resolve()
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise ValueError('本机文件不存在或不在下载目录内')
    if kind == 'folder':return resolved.parent
    if resolved.suffix.lower() not in DOCUMENTS:raise ValueError('此类型请先打开所在文件夹，再自行选择打开方式')
    return resolved


def open_target(path):
    if os.name == 'nt':os.startfile(str(path))
    else:subprocess.Popen(['xdg-open', str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def register_local_files(app, guard, csrf):
    @app.get('/files')
    def files_page():
        guard()
        return render_template('local_files.html', csrf_token=csrf, api_base=request.script_root)

    @app.post('/api/materials/open')
    def open_material():
        try:
            guard(write=True)
            payload = request.get_json() or {}
            path = target(payload.get('key'), payload.get('kind'))
            open_target(path)
            return jsonify(ok=True)
        except Exception as exc:
            return jsonify(error=str(exc) if isinstance(exc, ValueError) else '无法打开，请检查文件是否存在及默认应用'),400
