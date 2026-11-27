"""Run Lab generation and review against frozen inputs on a private SSH host."""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import re
import sys
import tempfile
import time

from .homework_backend import CodexBackend, clean_env, command, confined


STAGES = {'lab-generate', 'lab-independent-review'}
MAX_REQUEST = 32 * 1024 * 1024


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def filenames(stage, images):
    if (stage not in STAGES or not isinstance(images, list) or
            any(not isinstance(name, str) or not re.fullmatch(r'source-images/[a-f0-9]{64}\.(?:png|jpe?g|webp)', name)
                for name in images)):
        raise ValueError('Invalid frozen Lab worker stage or attachment list')
    result = {'input.json', 'feedback.json', 'source.pdf', *images}
    if stage == 'lab-independent-review':
        result.add('candidate.json')
    return result


def run_on_mac(config, root, stage, prompt, schema, images, stop):
    host, python = config['host'], config['python']
    if (not isinstance(host, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]*', host) or
            not isinstance(python, str) or not re.fullmatch(r'/[A-Za-z0-9_./-]+', python) or '..' in python.split('/')):
        raise ValueError('Invalid Lab worker SSH configuration')
    images = list(images)
    files = {}
    total = 0
    for name in sorted(filenames(stage, images)):
        source = confined(root, name)
        total += source.stat().st_size
        if total > MAX_REQUEST*3//4:
            raise ValueError('Frozen Lab materials exceed the SSH request limit')
        raw = source.read_bytes()
        files[name] = {'sha256': sha(raw), 'data': base64.b64encode(raw).decode('ascii')}
    request = json.dumps({'version':1, 'stage':stage, 'prompt':prompt, 'schema':schema,
                          'images':images, 'files':files}, ensure_ascii=False, separators=(',', ':'))
    encoded = request.encode('utf-8')
    if len(encoded) > MAX_REQUEST:
        raise ValueError('Frozen Lab materials exceed the SSH request limit')
    log = root/'agent-runs'/('mac-'+stage+'-'+str(time.time_ns())+'.json')
    command(['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=8',
             '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=3', '--', host,
             python+' -m sustech_dashboard.lab_worker'], root, log, stop, timeout=1800,
            input_text=request, env=clean_env())
    reply = json.loads(log.read_bytes())
    if (reply.get('stage') != stage or reply.get('input_sha256') != sha(encoded) or
            not isinstance(reply.get('result'), dict) or any(sha(confined(root, name).read_bytes()) != item['sha256']
                                                              for name, item in files.items())):
        raise ValueError('Mac Lab receipt does not match the unchanged frozen materials')
    return reply['result']


def execute(raw, backend=None):
    content = json.loads(raw)
    if (set(content) != {'version','stage','prompt','schema','images','files'} or content['version'] != 1 or
            set(content['files']) != filenames(content['stage'], content['images'])):
        raise ValueError('Incomplete frozen Lab request')
    with tempfile.TemporaryDirectory(prefix='campus-lab-worker-') as temporary:
        root = Path(temporary)
        for name, item in content['files'].items():
            data = base64.b64decode(item['data'], validate=True)
            if sha(data) != item['sha256']:
                raise ValueError('Frozen Lab source hash mismatch')
            target = confined(root, name)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        (root/'workflow').mkdir()
        runner = backend if backend is not None else CodexBackend({'read_only': True})
        result = runner.run(root, content['stage'], content['prompt'], content['schema'], images=content['images'])
        if any(sha(confined(root, name).read_bytes()) != item['sha256'] for name, item in content['files'].items()):
            raise ValueError('Mac worker changed a frozen input')
    return {'stage':content['stage'], 'input_sha256':sha(raw), 'result':result}


if __name__ == '__main__':
    try:
        data = sys.stdin.buffer.read(MAX_REQUEST+1)
        if len(data) > MAX_REQUEST:
            raise ValueError('Frozen Lab request is too large')
        sys.stdout.buffer.write(json.dumps(execute(data), ensure_ascii=False).encode('utf-8'))
    except Exception as error:
        print(type(error).__name__, file=sys.stderr)
        raise SystemExit(1)
