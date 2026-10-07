"""Organized paths, atomic file saves and durable local verification receipts."""
import hashlib
from pathlib import Path

from .core import attachment_key, safe_name, unique_name, load_json, save_json


def material_path(root, item):
    key = attachment_key(item["course_id"], item["content_id"], item["id"])
    folders = item.get("folders") or []
    if not folders and item.get("title") and Path(item["title"]).stem != Path(item["file_name"]).stem:
        folders = [item["title"]]
    course = safe_name(item["course_name"])[:60]
    parts = [safe_name(folder)[:28] for folder in folders[:3]]
    name = Path(item["file_name"])
    short_name = safe_name(name.stem)[:50] + name.suffix[:16]
    return Path(root) / course / Path(*parts) / unique_name(short_name, key)


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

    def save(self, provider, item):
        key = attachment_key(item["course_id"], item["content_id"], item["id"])
        old = self.receipts.get(key, {})
        if old.get("path"):
            path = self.root / old["path"]
            if path.resolve().is_relative_to(self.root.resolve()) and path.is_file():
                if not item.get('source_version') or old.get('source_version') == item['source_version']:
                    if path.stat().st_size != old.get("size") or file_digest(path) != old.get("sha256"):
                        raise FileExistsError("已保存文件被修改，保留原文件；请用浏览器另存下载")
                    corrected=material_path(self.root,item)
                    if path!=corrected and path.suffix!=corrected.suffix and corrected.suffix and not corrected.exists():
                        corrected.parent.mkdir(parents=True,exist_ok=True)
                        path.replace(corrected)
                        old={**old,'path':str(corrected.relative_to(self.root))}
                        self.receipts[key]=old
                        save_json(self.state_path,self.receipts)
                    return {**old, "status": "existing"}
        path = material_path(self.root, item)
        if item.get('source_version') and old.get('path'):
            suffix = hashlib.sha256(item['source_version'].encode()).hexdigest()[:10]
            path = path.with_name(path.stem + '-v' + suffix + path.suffix)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            raise FileExistsError("目标已有文件，未覆盖；请用浏览器另存下载")
        provider.download_attachment(item["course_id"], item["content_id"], item["id"], path)
        if not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError("下载返回空文件")
        receipt = {"key": key, "status": "saved", "size": path.stat().st_size,
                   "sha256": file_digest(path), "path": str(path.relative_to(self.root)), 'source_version': item.get('source_version', '')}
        self.receipts[key] = receipt
        save_json(self.state_path, self.receipts)
        return receipt

    def inventory(self):
        return [{**r, "status": r["status"] if (self.root / r["path"]).is_file() else "missing"}
                for r in self.receipts.values()]
