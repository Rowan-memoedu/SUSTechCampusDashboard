"""Publish additive Academy units and verify them through the running IDE."""
import hashlib
import json
import os
import re
import subprocess
import time
import uuid
from pathlib import Path

from .homework_backend import confined, command
from .homework_recovery import StageFailure


def sha(value):
    return hashlib.sha256(value).hexdigest()


def atomic(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(path.name + '.lab-pending')
    pending.write_bytes(content)
    pending.replace(path)


def create_source(path, content):
    """Publish a complete new source atomically without replacing a raced edit."""
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(path.name + '.lab-pending-' + uuid.uuid4().hex)
    try:
        pending.write_bytes(content)
        try:
            os.link(pending, path)
            return True
        except FileExistsError:
            return False
    finally:
        pending.unlink(missing_ok=True)


def extend_lesson(path, names):
    text = path.read_text(encoding='utf-8') if path.exists() else 'content: []\n'
    if text.strip() in ('{}', '{ }', ''):
        text = 'content: []\n'
    for name in names:
        if re.search(r'^\s*-\s*' + re.escape(name) + r'\s*$', text, re.M):
            continue
        if re.search(r'^content:\s*\[\s*\]', text, re.M):
            text = re.sub(r'^content:\s*\[\s*\]', 'content:\n  - ' + name, text, count=1, flags=re.M)
        elif re.fullmatch(r'content:\s*\n(?:\s*- [A-Za-z0-9_ -]+\s*\n?)*', text):
            text = text.rstrip() + '\n  - ' + name + '\n'
        else:
            raise StageFailure('现有课程目录含未识别配置，已保留，需人工核对', kind='blocked')
    atomic(path, text.encode('utf-8'))


def publish(project, units, receipt_path, oj_config):
    project = Path(project).resolve(strict=True)
    if not (project / 'course-info.yaml').is_file() or not (project / 'settings.gradle').is_file():
        raise StageFailure('请选择已有的 Academy 学习工程', kind='blocked')
    receipt_path = Path(receipt_path)
    previous = json.loads(receipt_path.read_text(encoding='utf-8')) if receipt_path.exists() else {}
    writes, preserved, registrations = [], [], []
    # Preflight the complete package before publishing any Java file.
    for unit in units:
        rel = unit['path']
        if not re.fullmatch(r'(?:OJ/Practice/[A-Za-z0-9_-]+|lab 2026/week\d{2}/[A-Za-z0-9_-]+)', rel):
            raise StageFailure('生成的课程路径超出允许范围', kind='blocked')
        target = confined(project, rel)
        if not (project / rel.split('/')[0] / 'section-info.yaml').is_file():
            raise StageFailure('学习工程缺少现有课程分区', kind='blocked')
        paths = [f['path'] for f in unit['files']]
        if len(paths) != len(set(paths)):
            raise StageFailure('生成的任务包含重复文件', kind='blocked')
        for item in unit['files']:
            if not re.fullmatch(r'src/(?:[A-Za-z0-9_]+/)*[A-Za-z0-9_]+\.java', item['path']):
                raise StageFailure('生成的源码路径无效', kind='blocked')
            path = confined(project, rel + '/' + item['path'])
            data = item['content'].encode('utf-8')
            key = rel + '/' + item['path']
            if key in previous and sha(data) != previous[key]:
                raise StageFailure('题面或来源发生变化，已保留原练习和新候选，需要核对', kind='blocked')
            if path.exists() and path.read_bytes() != data:
                if key in previous and sha(path.read_bytes()) == previous[key]:
                    raise StageFailure('题面或来源发生变化，已保留原练习和新候选，需要核对', kind='blocked')
                preserved.append(key)  # Existing learner answers are never overwritten.
            else:
                if not path.exists():
                    writes.append((path, data, key))
                previous[key] = sha(data)
        if unit.get('pid') and unit['source'] not in paths:
            raise StageFailure('OJ 提交源不在生成文件列表中', kind='blocked')
        templates = {f['path']: f['content'] for f in unit['files']}
        meta = target / 'task-info.yaml'
        if meta.is_file():
            text = meta.read_text(encoding='utf-8')
            try:
                existing = [f['name'] for f in json.loads(text).get('files', [])]
            except ValueError:
                existing = re.findall(r'^\s*-\s*name:\s*[\'\"]?([^\'\"\r\n]+)', text, re.M)
            for name in existing:
                name = name.strip()
                if name.endswith(('.java', '.md', '.txt')) and name not in paths and confined(target, name).is_file():
                    paths.append(name)
        registrations.append({'path': rel, 'files': paths, 'kind': unit['kind'], 'pid': unit.get('pid', ''),
                              'source': unit.get('source', ''), 'templates': templates,
                              'module': rel.replace('/', '-').replace(' ', '-')})
    cfg_path = Path(oj_config)
    cfg = json.loads(cfg_path.read_text(encoding='utf-8')) if any(u.get('pid') for u in units) else None
    if cfg is not None:
        for unit in units:
            if not unit.get('pid'):
                continue
            old = next((b for b in cfg['bindings'] if b['pid'] == unit['pid'] and
                        Path(b['project_root']).resolve() == project), None)
            expected = {'project_root': project.as_posix(), 'task_path': unit['path'],
                        'pid': unit['pid'], 'lang': 'java', 'source': unit['source']}
            if old and old != expected:
                raise StageFailure('已有 OJ 绑定与新来源不一致，未修改提交入口', kind='blocked')
            if not old:
                cfg['bindings'].append(expected)
    for path, data, key in writes:
        if create_source(path, data) or path.read_bytes() == data:
            previous[key] = sha(data)
        elif key not in preserved:
            preserved.append(key)
    settings = (project / 'settings.gradle').read_text(encoding='utf-8')
    for unit, registration in zip(units, registrations):
        target = confined(project, unit['path'])
        md = target / 'task.md'
        if not md.exists():
            atomic(md, unit['markdown'].encode('utf-8'))
        meta = target / 'task-info.yaml'
        if not meta.exists():
            info = {'type': unit['kind'], 'files': [{'name': n, 'visible': True} for n in registration['files']],
                    'status': 'Unchecked', 'record': -1}
            if unit['kind'] == 'theory':
                info['post_submission_on_open'] = False
            atomic(meta, json.dumps(info, ensure_ascii=False, indent=2).encode('utf-8'))
        extend_lesson(target.parent / 'lesson-info.yaml', [target.name])
        module = registration['module']
        if registration['files'] and "include '" + module + "'" not in settings:
            settings += f"\ninclude '{module}'\nproject(':{module}').projectDir = file('{unit['path']}')\n"
    if (project / 'settings.gradle').read_text(encoding='utf-8') != settings:
        atomic(project / 'settings.gradle', settings.encode('utf-8'))
    if cfg is not None:
        atomic(cfg_path, json.dumps(cfg, ensure_ascii=False, indent=2).encode('utf-8'))
    atomic(receipt_path, json.dumps(previous, indent=2).encode('utf-8'))
    return {'registrations': registrations, 'preserved_files': preserved}


def connect(settings, registrations, workspace, stop):
    workspace = Path(workspace)
    attempt = uuid.uuid4().hex
    payload = {'project': str(Path(settings['project_root']).resolve()), 'units': registrations,
               'receipt': str(workspace / ('idea-' + attempt + '.json')), 'attempt': attempt}
    request = workspace / ('idea-' + attempt + '-input.json')
    request.write_text(json.dumps(payload), encoding='utf-8')
    template = Path(__file__).with_name('lab_assets') / 'register.groovy'
    script = workspace / ('idea-' + attempt + '.groovy')
    script.write_text(template.read_text(encoding='utf-8').replace('__INPUT_JSON__',
        request.as_posix().replace("'", "\\'")), encoding='utf-8')
    executable = Path(settings['idea_executable'])
    if not executable.is_file():
        raise StageFailure('未找到配置的 IDEA，请核对安装路径', kind='blocked')
    env = os.environ.copy()
    if settings.get('idea_properties'):
        env['IDEA_PROPERTIES'] = settings['idea_properties']
    # The dashboard is the long-lived owner. Do not use a short-lived timeout job
    # to own the GUI process, and never terminate/restart an existing IDE.
    now = time.time()
    last = float(settings.get('last_idea_launch', 0))
    if now - last >= 600:
        subprocess.Popen([str(executable), settings['project_root']], env=env,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        settings['last_idea_launch'] = now
        if settings.get('_on_launch'):
            settings['_on_launch'](now)
        if stop.wait(25):
            raise StageFailure('IDEA 接入已暂停，稍后自动续跑', kind='retry')
    try:
        command([str(executable), 'ideScript', str(script)], workspace, workspace / ('idea-' + attempt + '.log'),
                stop, timeout=240, idle_timeout=0, env=env)
    except StageFailure as exc:
        log = Path(settings.get('idea_log', executable.parent.parent / 'log/idea.log'))
        if log.is_file():
            with log.open('rb') as handle:
                handle.seek(max(0, log.stat().st_size - 160000))
                if b'error=1455' in handle.read():
                    raise StageFailure('IDEA 启动受 Windows 错误 1455（页面文件不足）阻塞；已保留课程文件，稍后自动重试', kind='retry') from exc
        raise
    result = Path(payload['receipt'])
    if not result.is_file():
        raise StageFailure('IDEA 尚未返回加载回执，将自动重试', kind='retry')
    receipt = json.loads(result.read_text(encoding='utf-8'))
    if receipt.get('attempt') != attempt or not receipt.get('passed'):
        raise StageFailure('IDEA 尚未确认接入：' + str(receipt.get('error', '等待课程加载'))[:800], kind='retry')
    return receipt
