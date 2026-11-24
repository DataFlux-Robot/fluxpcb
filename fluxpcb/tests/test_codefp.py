"""Code fingerprint: the static half, the measured half, and a cell's own.

The cases here matter because each one went wrong on the real entry
before it went right (see the module docstring):

  * a module nothing on the path imports must not change the fingerprint
    — but a script a reached module names by path must;
  * a .py string written as DATA ('sut/x.py' in a list of names) is not
    a script, and a stdlib name is not a same-named project module
    unless that module is the importer's own sibling;
  * a module imported inside a function is deferred: it counts for the
    cells that load it and for no others;
  * a nested third-party checkout is environment, recorded not hashed;
  * a cell is compared on its own files, so editing a module only that
    cell loaded stales only that cell;
  * an import nobody can resolve statically gives up and hashes the
    whole package rather than guessing.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from fluxpcb.meta import codefp as c  # noqa: E402

REQ = {}      # the SUT directory of the current test's workspace


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write(text)
    return path


def _git(root):
    os.makedirs(os.path.join(root, '.git'), exist_ok=True)


@pytest.fixture
def ws(tmp_path):
    """Two repositories; `sut` is a package inside the second, the way
    fluxpcb sits inside its repository.

    repo_a/entry.py       import sut.core; importlib.import_module('sut.late')
                          NAMES = ['sut/x.py']           data, not a script
                          HELPER = 'scripts/helper.py'   a subprocess script
                          def later(): import sut.lazy; import vendor.lib.env
    repo_a/scripts/helper.py   import common; import builtins
    repo_a/common.py, repo_a/other/unused.py (imported by nothing)
    repo_a/vendor/lib/         a third-party checkout nested in the tree
    repo_b/sut/{__init__,core,util,late,lazy,builtins}.py
    repo_b/sut/core.py    from . import util
    """
    a, b = tmp_path / 'repo_a', tmp_path / 'repo_b'
    sut = b / 'sut'
    for r in (a, b, a / 'vendor' / 'lib'):
        _git(r)
    _write(a / 'entry.py', (
        "import sut.core\n"
        "import importlib\n"
        "importlib.import_module('sut.late')\n"
        "NAMES = ['sut/x.py']\n"
        "HELPER = 'scripts/helper.py'\n"
        "def later():\n"
        "    import sut.lazy\n"
        "    import vendor.lib.env\n"))
    _write(sut / '__init__.py', '')
    _write(sut / 'core.py', 'from . import util\n')
    _write(sut / 'util.py', 'X = 1\n')
    _write(sut / 'late.py', 'Y = 2\n')
    _write(sut / 'lazy.py', 'Z = 3\n')
    _write(sut / 'builtins.py', 'NOT_THE_STDLIB = True\n')   # a same-named module
    _write(a / 'scripts' / 'helper.py', 'import common\nimport builtins\n')
    _write(a / 'common.py', 'K = 4\n')
    _write(a / 'other' / 'unused.py', 'U = 0\n')
    _write(a / 'vendor' / 'lib' / '__init__.py', '')
    _write(a / 'vendor' / 'lib' / 'env.py', 'E = 5\n')
    c.forget()
    REQ['sut'] = str(sut)
    yield dict(a=a, b=b, sut=sut, entry=str(a / 'entry.py'),
               helper=str(a / 'scripts' / 'helper.py'), tmp=tmp_path)
    c.forget()


def _rels(info, key='files'):
    """Basenames, with the package name for __init__ files."""
    out = []
    for f in info[key]:
        f = f.replace(os.sep, '/')
        out.append(f.split('/')[-2] + '/__init__.py' if f.endswith('__init__.py')
                   else f.split('/')[-1])
    return sorted(out)


def _required():
    return REQ['sut']


def _closure(ws, helper=True):
    return c.path_closure([ws['entry']], [ws['helper']] if helper else [],
                          required=str(ws['sut']))


def test_static_half_is_what_every_cell_runs(ws):
    info = _closure(ws)
    assert _rels(info) == ['common.py', 'core.py', 'entry.py', 'helper.py',
                                 'late.py', 'sut/__init__.py', 'util.py']
    assert not info['dynamic']
    # the SUT repository is the owner of the package, not a stranger
    assert os.path.normcase(os.path.abspath(ws['b'])) in {
        os.path.normcase(os.path.abspath(r)) for r in info['own_roots']}


def test_function_level_imports_are_deferred_not_static(ws):
    info = _closure(ws)
    assert 'lazy.py' in _rels(info, 'deferred')
    assert 'lazy.py' not in _rels(info)


def test_module_nothing_imports_does_not_change_the_fingerprint(ws):
    args = [ws['entry'], ws['helper']]
    c.code_fingerprint(args, REQ['sut'])
    _write(ws['a'] / 'other' / 'unused.py', 'U = 1\n')
    assert not c.static_changed(args, REQ['sut'])
    # a deferred module is not in the static half either
    _write(ws['sut'] / 'lazy.py', 'Z = 33\n')
    assert not c.static_changed(args, REQ['sut'])


def test_static_half_changes_when_code_it_runs_changes(ws):
    args = [ws['entry'], ws['helper']]
    c.code_fingerprint(args, REQ['sut'])
    _write(ws['sut'] / 'util.py', 'X = 11\n')
    assert c.static_changed(args, REQ['sut'])
    _write(ws['a'] / 'scripts' / 'helper.py', 'import common\nimport builtins\nK2 = 1\n')
    assert c.static_changed(args, REQ['sut'])


def test_fingerprint_is_one_code_state_per_process(ws):
    args = [ws['entry'], ws['helper']]
    before = c.code_fingerprint(args, REQ['sut'])
    _write(ws['sut'] / 'util.py', 'X = 111\n')
    assert c.code_fingerprint(args, REQ['sut']) == before      # cached, by design
    c.forget()
    assert c.code_fingerprint(args, REQ['sut']) != before


def test_py_string_written_as_data_is_not_a_script(ws):
    info = _closure(ws)
    assert all(not s.replace(os.sep, '/').endswith('sut/x.py')
               for s in info['scripts'])


def test_stdlib_name_is_not_a_same_named_module_elsewhere(ws):
    # scripts/helper.py does `import builtins`; sut/builtins.py is not its
    # sibling, so the standard library wins and the decoy is never reached
    info = _closure(ws)
    assert 'builtins.py' not in _rels(info)
    assert 'builtins.py' not in _rels(info, 'deferred')


def test_nested_checkout_is_recorded_not_hashed(ws):
    info = _closure(ws)
    assert any('vendor' in r.replace(os.sep, '/') for r in info['external_repos'])
    assert 'env.py' not in _rels(info) + _rels(info, 'deferred')


def test_cell_fingerprint_is_the_static_half_plus_what_it_loaded(ws):
    args = [ws['entry'], ws['helper']]
    static = c.code_fingerprint(args, REQ['sut'])
    lazy = c._norm(str(ws['sut'] / 'lazy.py'))
    idle, busy = c.cell_fingerprint([]), c.cell_fingerprint([lazy])
    assert idle == static
    assert busy != idle
    # editing the deferred module stales the cell that ran it, and only it
    _write(ws['sut'] / 'lazy.py', 'Z = 333\n')
    assert c.cell_fingerprint([lazy]) != busy
    assert c.cell_fingerprint([]) == idle


def test_gaps_are_loaded_files_the_analysis_cannot_reach(ws):
    c.code_fingerprint([ws['entry'], ws['helper']], REQ['sut'])
    inside = c._norm(str(ws['sut'] / 'lazy.py'))
    outside = c._norm(str(ws['a'] / 'other' / 'unused.py'))
    assert c.gaps([inside]) == []
    assert c.gaps([outside]) == [outside]
    assert c.gaps([c._norm(c.__file__)]) == []      # the measurer is expected


def test_unresolvable_import_falls_back_to_the_whole_package(ws):
    _write(ws['sut'] / 'core.py', (
        'from . import util\n'
        'import importlib\n'
        'M = "sut.util"\n'
        'importlib.import_module(M)\n'))
    raw = _closure(ws)
    assert any('not a literal' in d for d in raw['dynamic'])
    c.code_fingerprint([ws['entry'], ws['helper']], REQ['sut'])
    info = c.fingerprint_info()
    assert info['scheme'] == 'all' and any('fallback' in d for d in info['dynamic'])
    names = sorted(os.path.basename(f) for f in info['files'])
    assert 'lazy.py' in names               # nothing is left to a cell's luck
    assert 'entry.py' in names              # the entry is still hashed
    assert 'unused.py' not in names         # another repository's file is not the package


def test_worker_hashing_the_parents_list_agrees_with_the_parent(ws, monkeypatch):
    args = [ws['entry'], ws['helper']]
    parent = c.code_fingerprint(args, REQ['sut'])
    handed = c.closure_files()
    c.forget()
    monkeypatch.setenv(c.FILES_ENV, os.pathsep.join(handed))
    assert c.code_fingerprint(args, REQ['sut']) == parent
    assert c.cell_fingerprint([c._norm(str(ws['sut'] / 'lazy.py'))]) != parent


def test_loaded_project_files_reports_only_project_code(ws):
    c.code_fingerprint([ws['entry'], ws['helper']], REQ['sut'])
    import types
    fake = types.ModuleType('fake_proj_mod')
    fake.__file__ = str(ws['a'] / 'other' / 'unused.py')
    std = types.ModuleType('fake_std_mod')
    std.__file__ = os.path.abspath(os.__file__)        # not under any indexed root
    sys.modules['fake_proj_mod'], sys.modules['fake_std_mod'] = fake, std
    try:
        loaded = c.loaded_project_files()
    finally:
        del sys.modules['fake_proj_mod'], sys.modules['fake_std_mod']
    assert c._norm(fake.__file__) in loaded
    assert all('test_codefp.py' not in p for p in loaded)
