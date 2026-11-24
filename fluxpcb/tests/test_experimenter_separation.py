"""The experimenters are not part of the system under test.

The code fingerprint hashes what the execution path reaches, and the
meta-loop modules are never reached by a board run.  That is only true
while nothing on the path imports them.  This file makes the separation
something that is checked rather than believed, three ways:

  * every listed module exists;
  * no other module in the package imports one of them (the earlier
    version of this test compared the FIRST path segment against 'meta',
    which never matches a real import name — it could not fail);
  * the benchmark's static execution path, computed from its entry
    script, contains none of them (skipped when the bench is not
    installed).  The experimenters may be REACHABLE through a lazy
    import — `from fluxpcb.meta import MetaDevLoop` still works — but
    then they sit in the deferred tier, which no cell hashes unless it
    actually loads them.
"""
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from fluxpcb.meta.codefp import (EXPERIMENTER_MODULES,  # noqa: E402
                                 _is_experimenter, _py_files, path_closure)
import fluxpcb  # noqa: E402

PKG = os.path.dirname(os.path.abspath(fluxpcb.__file__))
REPO = os.path.dirname(PKG)
BENCH = os.path.join(os.path.dirname(REPO), 'autorouter-bench')
ENTRY = os.path.join(BENCH, 'work', 'pcbworld', 'run_bench.py')
HELPER = os.path.join(BENCH, 'scripts', 'kicad_board.py')
# Places allowed to name an experimenter.  A new entry here needs a reason.
#
# fluxpcb/meta/__init__.py is the lazy attribute hook: its imports sit
# inside __getattr__ and run only on `fluxpcb.meta.MetaDevLoop` access;
# test_importing_the_meta_package_loads_no_experimenter checks that
# importing the package loads none of them.
# fluxpcb/pipeline.py is an orchestrator no board run imports.
ALLOWED = {'fluxpcb/pipeline.py', 'fluxpcb/meta/__init__.py'}


def _rel(path):
    parts = os.path.abspath(path).replace(os.sep, '/').split('/')
    i = parts.index('fluxpcb')
    return '/'.join(parts[i:])


def _targets(path):
    """Package-relative names an import in `path` can denote."""
    tree = ast.parse(open(path, encoding='utf-8').read())
    pkg = _rel(path).split('/')[:-1]            # package of this file
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name.replace('.', '/')
        elif isinstance(node, ast.ImportFrom):
            base = node.module.split('.') if node.module else []
            if node.level:
                cut = len(pkg) - (node.level - 1)
                base = (pkg[:cut] if cut >= 0 else []) + base
            if base:
                yield '/'.join(base)             # the module/package itself
            # a plain name may be a submodule: from . import devloop
            for alias in node.names:
                yield '/'.join(base + [alias.name])


def _experimenter_imports(path):
    for target in _targets(path):
        name = f'{target}.py'
        if name in EXPERIMENTER_MODULES:
            yield name


def test_every_experimenter_module_exists():
    for m in EXPERIMENTER_MODULES:
        assert os.path.exists(os.path.join(REPO, m)), m
        assert _is_experimenter(os.path.join(REPO, m)), m
    # read by the bench for its own inputs: on the path, not experimenters
    for m in ('arm.py', 'policy.py', 'feedback.py', 'distinguish.py'):
        assert not _is_experimenter(os.path.join(PKG, 'meta', m)), m


def test_the_detector_itself_can_fail():
    # a file that imports an experimenter, written the way the package
    # writes it, must be reported — otherwise the next test proves nothing
    src = ('from .devloop import MetaDevLoop\n'
           'from fluxpcb.meta import memory\n'
           'import fluxpcb.meta.proposer\n')
    path = os.path.join(PKG, 'meta', '_probe.py')
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write(src)
    try:
        hits = sorted(_experimenter_imports(path))
    finally:
        os.remove(path)
    assert hits == ['fluxpcb/meta/devloop.py', 'fluxpcb/meta/memory.py',
                    'fluxpcb/meta/proposer.py']


def test_no_other_module_imports_an_experimenter():
    offenders = []
    for path in _py_files(PKG):
        rel = _rel(path)
        if rel in ALLOWED or _is_experimenter(path):
            continue
        offenders.extend(f'{rel} -> {hit}' for hit in _experimenter_imports(path))
    assert offenders == [], (
        'a module now imports an experimenter; remove the import or take '
        'that module out of EXPERIMENTER_MODULES: ' + ', '.join(offenders))


def test_importing_the_meta_package_loads_no_experimenter():
    import subprocess
    code = ('import sys; import fluxpcb.meta.arm, fluxpcb.meta.policy, '
            'fluxpcb.meta.codefp; '
            'bad = [m for m in sys.modules if m.startswith("fluxpcb.meta")]; '
            'print("\\n".join(sorted(bad)))')
    r = subprocess.run([sys.executable, '-c', code], cwd=REPO,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-400:]
    loaded = set(r.stdout.split())
    forbidden = {m[:-3].replace('/', '.') for m in EXPERIMENTER_MODULES}
    assert not (loaded & forbidden), sorted(loaded & forbidden)


def test_bench_execution_path_contains_no_experimenter():
    if not (os.path.exists(ENTRY) and os.path.exists(HELPER)):
        import pytest
        pytest.skip('autorouter-bench is not installed next to the repo')
    info = path_closure([ENTRY], [HELPER])
    assert not info['dynamic'], info['dynamic']
    static = {os.path.realpath(f).replace(os.sep, '/')
              for f in info['files']}
    for m in EXPERIMENTER_MODULES:
        assert os.path.realpath(os.path.join(REPO, m)).replace(
            os.sep, '/') not in static, m
    # and the static half does reach the system under test
    assert any(os.path.realpath(os.path.join(PKG, '__init__.py'))
               .replace(os.sep, '/') == h for h in static), sorted(static)
