"""Code fingerprint — which code produced a result.

A cache keyed by (board, arm) alone stays valid across code changes, so a
result produced by last week's operator can be served to this week's
analysis.  The fingerprint hashes every Python file that can change an
outcome; a cell is fresh only when its recorded fingerprint matches.
"""
from __future__ import annotations

import hashlib
import importlib
import os

_CACHE = {}

# The meta loops are the EXPERIMENTER, not the system under test: nothing on
# the execution path imports them, so editing them cannot change a cell's
# outcome.  They are left out of the fingerprint so a campaign can run while
# they are being written; fluxpcb/tests/test_codefp.py fails if the execution
# path ever starts importing one of them.
EXPERIMENTER_MODULES = frozenset((
    'fluxpcb/meta/devloop.py',
    'fluxpcb/meta/meta_loop.py',
    'fluxpcb/meta/proposer.py',
    'fluxpcb/meta/diagnose.py',
    'fluxpcb/meta/feedback.py',
    'fluxpcb/meta/gate.py',
    'fluxpcb/meta/strategy_store.py',
    'fluxpcb/meta/memory.py',
    'fluxpcb/meta/policy.py',
    'fluxpcb/meta/distinguish.py',
))


def _py_files(root: str) -> list:
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames
                             if d not in ('__pycache__', '.git',
                                          'tests', '.pytest_cache'))
        for fn in sorted(filenames):
            if fn.endswith('.py'):
                out.append(os.path.join(dirpath, fn))
    return out


def _is_experimenter(path: str) -> bool:
    parts = os.path.abspath(path).replace(os.sep, '/').split('/')
    for i in range(len(parts)):
        if parts[i] == 'fluxpcb':
            rel = '/'.join(parts[i:])
            return rel in EXPERIMENTER_MODULES
    return False


def code_fingerprint(extra_paths=()) -> str:
    """Short hash of the fluxpcb package plus optional extra files/dirs."""
    roots = []
    for mod in ('fluxpcb',):
        try:
            m = importlib.import_module(mod)
            roots.append(os.path.dirname(os.path.abspath(m.__file__)))
        except Exception:
            continue
    key = (tuple(roots), tuple(sorted(map(str, extra_paths))))
    if key in _CACHE:
        return _CACHE[key]
    h = hashlib.sha256()
    files = []
    for r in roots:
        files.extend(_py_files(r))
    for p in extra_paths:
        p = str(p)
        if os.path.isdir(p):
            files.extend(_py_files(p))
        elif os.path.isfile(p):
            files.append(p)
    for f in sorted(set(files)):
        if _is_experimenter(f):
            continue
        try:
            with open(f, 'rb') as fh:
                blob = fh.read()
        except OSError:
            continue
        h.update(os.path.basename(f).encode('utf-8'))
        h.update(str(os.path.getsize(f)).encode())
        h.update(hashlib.sha256(blob).digest())
    fp = h.hexdigest()[:16]
    _CACHE[key] = fp
    return fp
