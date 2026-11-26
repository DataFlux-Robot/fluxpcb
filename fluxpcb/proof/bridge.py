"""Bridge to the Lean kernel: write a request, run flux-kernel, read the report."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LEAN_DIR = os.path.join(ROOT, 'lean')
EXE = os.path.join(LEAN_DIR, '.lake', 'build', 'bin',
                   'flux-kernel.exe' if os.name == 'nt' else 'flux-kernel')


class KernelError(RuntimeError):
    pass


def _lake():
    p = shutil.which('lake')
    if p:
        return p
    for c in (os.path.expanduser('~/.elan/bin/lake.exe'),
              os.path.expanduser('~/.elan/bin/lake')):
        if os.path.exists(c):
            return c
    return None


def available():
    return os.path.exists(EXE)


def build(timeout=900):
    lake = _lake()
    if not lake:
        raise KernelError('lake not found (install elan or put lake on PATH)')
    r = subprocess.run([lake, 'build'], cwd=LEAN_DIR, capture_output=True,
                       text=True, timeout=timeout)
    if r.returncode != 0:
        raise KernelError(f'lake build failed: {(r.stderr or r.stdout)[-400:]}')
    return EXE


def request(state, claims, certs, before=None, touched=(), carried=(),
            obligations=()):
    req = {'state': state, 'claims': claims,
           'certs': [{'claim': c['claim'], 'kind': c.get('kind', 'tree'),
                      'edges': c.get('edges', [])} for c in certs],
           'touched': list(touched),
           'obligations': list(obligations),
           'carried': [{'id': i, 'scope': s} for (i, s) in carried]}
    if before is not None:
        req['before'] = before
    return req


def run(req, timeout=300):
    if not available():
        build()
    fd, path = tempfile.mkstemp(suffix='.json')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(req, f, ensure_ascii=False)
        r = subprocess.run([EXE, path], capture_output=True, text=True,
                           timeout=timeout)
        if r.returncode != 0:
            raise KernelError(f'kernel rc={r.returncode}: {(r.stderr or r.stdout)[-400:]}')
        return json.loads(r.stdout)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
