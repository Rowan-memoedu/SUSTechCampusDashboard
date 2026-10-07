"""Organized paths, atomic file saves and durable local verification receipts."""
import hashlib
from pathlib import Path

from .core import attachment_key, safe_name, download_name, save_json


def material_path(root, item):
    folders = item.get("folders") or []
    if not folders and item.get("title") and Path(item["title"]).stem != Path(item["file_name"]).stem:
        folders = [item["title"]]
    course = safe_name(item["course_name"])[:60]
    parts = [safe_name(folder)[:28] for folder in folders[:3]]
    return Path(root) / course / Path(*parts) / download_name(item['file_name'])


def file_digest(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


class LocalMaterials:
    def __init__(self, root, state_path):
        self.root, self.state_path = Path(root), Path(state_path)
        if self.state_path.exists():
            import json
            self.receipts = json.loads(self.state_path.read_text(encoding="utf-8"))
        else:
            self.receipts = {}

    def available_path(self, item, key):
        path = material_path(self.root, item)
        if path.exists():
            # Disambiguate directories, never add an opaque suffix to the name.
            identity = key + '/' + item.get('source_version', '')
            path = path.parent / ('同名资料-' + hashlib.sha256(identity.encode()).hexdigest()[:10]) / path.name
        return path

    def save(self, provider, item):
        key = attachment_key(item["course_id"], item["content_id"], item["id"])
        old = self.receipts.get(key, {})
        if old.get("path"):
            path = self.root / old["path"]
            if path.resolve().is_relative_to(self.root.resolve()) and path.is_file():
                if not item.get('source_version') or old.get('source_version') == item['source_version']:
                    if path.stat().st_size != old.get("size") or file_digest(path) != old.get("sha256"):
                        raise FileExistsError("已保存文件被修改，保留原文件；请用浏览器另存下载")
                    corrected=self.available_path(item,key)
                    if path.name != download_name(item['file_name']) and not corrected.exists():
                        corrected.parent.mkdir(parents=True,exist_ok=True)
                        path.replace(corrected)
                        old={**old,'path':str(corrected.relative_to(self.root))}
                        self.receipts[key]=old
                        save_json(self.state_path,self.receipts)
                    return {**old, "status": "existing"}
        path = self.available_path(item, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            raise FileExistsError("目标已有文件，未覆盖；请用浏览器另存下载")
        provider.download_attachment(item["course_id"], item["content_id"], item["id"], path)
        if not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError("下载返回空文件")
        receipt = {"key": key, "status": "saved", "size": path.stat().st_size,
                   "sha256": file_digest(path), "path": str(path.relative_to(self.root)),
                   'mtime_ns': path.stat().st_mtime_ns, 'source_version': item.get('source_version', '')}
        self.receipts[key] = receipt
        save_json(self.state_path, self.receipts)
        return receipt

    def inventory(self):
        result = []
        for r in self.receipts.values():
            path = self.root / r['path']
            status = 'missing'
            if path.resolve().is_relative_to(self.root.resolve()) and path.is_file():
                stat = path.stat()
                status = r['status']
                if stat.st_size != r.get('size') or (stat.st_mtime_ns != r.get('mtime_ns') and file_digest(path) != r.get('sha256')):
                    status = 'modified'
            result.append({**r, 'status': status})
        return result
