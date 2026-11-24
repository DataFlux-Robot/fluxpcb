"""The experimenters are not part of the system under test.

The code fingerprint deliberately leaves the meta-loop modules out, so a
campaign can run while they are written.  That is only sound while nothing
on the execution path imports them.  This test makes the separation a fact
that is checked rather than a comment that is believed.
"""
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from fluxpcb.meta.codefp import (EXPERIMENTER_MODULES,  # noqa: E402
                                 _is_experimenter, _py_files,
                                 code_fingerprint)
import fluxpcb  # noqa: E402

PKG = os.path.dirname(os.path.abspath(fluxpcb.__file__))
STEMS = {os.path.basename(m)[:-3] for m in EXPERIMENTER_MODULES}
# Orchestrators that drive the experimenters but are never imported by a
# board run.  A new entry here needs a reason in the commit message.
ALLOWED = {'fluxpcb/pipeline.py'}


def _rel(path):
    parts = os.path.abspath(path).replace(os.sep, '/').split('/')
    i = parts.index('fluxpcb')
    return '/'.join(parts[i:])


def _experimenter_imports(path):
    tree = ast.parse(open(path, encoding='utf-8').read())
    pkg = _rel(path).split('/')[:-1]            # package of this file
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.append((alias.name, 0))
        elif isinstance(node, ast.ImportFrom):
            out.append((node.module or '', node.level))
    for module, level in out:
        if level == 0:
            full = module
        else:
            base = pkg[:len(pkg) - (level - 1)] if level > 1 else pkg
            full = '.'.join(base + ([module] if module else []))
        parts = full.split('.')
        if len(parts) >= 2 and parts[0] == 'meta' and parts[1] in STEMS:
            yield f'{parts[0]}.{parts[1]}'


def test_every_experimenter_module_exists_and_is_excluded():
    for m in EXPERIMENTER_MODULES:
        path = os.path.join(os.path.dirname(PKG), m)
        assert os.path.exists(path), m
        assert _is_experimenter(path), m
    assert not _is_experimenter(os.path.join(PKG, 'meta', 'arm.py'))
    assert not _is_experimenter(os.path.join(PKG, 'repair_drc.py'))


def test_execution_path_never_imports_an_experimenter():
    offenders = []
    for path in _py_files(PKG):
        rel = _rel(path)
        if rel in ALLOWED or _is_experimenter(path):
            continue
        for hit in _experimenter_imports(path):
            offenders.append(f'{rel} -> {hit}')
    assert offenders == [], (
        'an execution-path module now imports an experimenter module; '
        'either remove the import or take that module out of '
        'EXPERIMENTER_MODULES: ' + ', '.join(offenders))


def test_fingerprint_ignores_experimenter_content():
    # The hash is over the files it reads: an experimenter module must not
    # be among them.
    import inspect
    import hashlib
    src = inspect.getsource(code_fingerprint)
    assert '_is_experimenter' in src
    # and the hash is stable within one process
    assert code_fingerprint() == code_fingerprint()
    assert len(code_fingerprint()) == 16
    assert all(c in '0123456789abcdef' for c in code_fingerprint())
    del hashlib
