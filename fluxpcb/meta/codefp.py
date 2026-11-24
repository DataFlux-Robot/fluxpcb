"""Code fingerprint — which code produced a result.

A cache keyed by (board, arm) alone stays valid across code changes, so a
result produced by last week's operator can be served to this week's
analysis.  The fingerprint names the code that can change an outcome; a
cell is fresh only when the code it ran is the code on disk now.

Two halves, because neither is enough alone:

  * STATIC, module level.  The entry script, everything its import
    statements reach (resolved from the AST, relative imports included,
    every package __init__ on the way), and every project script a
    reached module names by path — a subprocess entry point is an import
    the AST cannot see otherwise.  This code runs in every cell.
  * MEASURED, per cell.  The worker reports the project files it actually
    loaded (`loaded_project_files`).  This is what that cell ran: most of
    this package is imported inside functions, so the static half alone
    says nothing about an operator pulled in on the third repair round,
    and one cell's lazy import says nothing about every other cell.

A cell's fingerprint is the hash of STATIC ∪ its own measured set.  A
module imported lazily counts for the cells that load it and for no
others: editing the meta loops while a campaign runs stales nothing,
editing an operator stales exactly the cells that ran it.  Code a
subprocess ran by path is covered by the static half; a file a cell
loaded that the static analysis cannot reach is reported as an
instrument problem (`gaps`), never ignored.

What is hashed is derived, not listed.  The first version hashed the
whole package minus a hand-kept list of experimenter modules, and that
list was wrong three times in one day: every module written while a
batch ran changed the fingerprint of cells whose execution path had not
changed, and 152 finished cells were flagged as instrument faults.  A
second attempt hashed one static closure for everybody and, on the real
entry, pulled in a stale copy of the package, a third-party checkout and
the experimenters themselves.  Each of those was found by looking at
what the closure contained and who imported it; `fingerprint_info()`
keeps that record.
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import os
import sys

SCHEME = 'path-v3'
_CACHE = {}          # extra_paths -> fingerprint of the static half
_INFO = {}           # extra_paths -> what was hashed
_LAST_KEY = None

# The meta loops are the EXPERIMENTER, not the system under test.  The
# fingerprint needs no list of them — a module is hashed when a cell runs
# it, and no cell runs these — but the separation is a property worth
# testing: test_experimenter_separation.py fails if any other module
# starts importing one of them, if importing the meta package loads one,
# or if the bench's static execution path ever contains one.
# (fluxpcb/meta/policy.py, feedback.py and distinguish.py are NOT here:
# the bench loads them to read its own inputs, so cells do run them.)
EXPERIMENTER_MODULES = frozenset((
    'fluxpcb/meta/devloop.py',
    'fluxpcb/meta/meta_loop.py',
    'fluxpcb/meta/proposer.py',
    'fluxpcb/meta/diagnose.py',
    'fluxpcb/meta/gate.py',
    'fluxpcb/meta/strategy_store.py',
    'fluxpcb/meta/memory.py',
    'fluxpcb/meta/noise.py',
    'fluxpcb/meta/error_model.py',
    'fluxpcb/meta/retrieve.py',
    'fluxpcb/meta/templates.py',
    'fluxpcb/meta/tool_evolve.py',
    'fluxpcb/meta/tool_gate.py',
))

# The measurer: a worker loads it to stamp its cert, and it cannot
# change an outcome.  Hashing it would make every fix to this file
# stale every cell.
INSTRUMENT_MODULES = frozenset(('fluxpcb/meta/codefp.py',))

# Directories that hold no code a board run imports.
_SKIP_DIRS = frozenset(('__pycache__', '.git', 'tests', '.pytest_cache',
                        'node_modules', '.venv', 'venv', '.tox', 'build',
                        'dist', '.mypy_cache'))

# An import root is almost always within three directories of its
# repository root (scripts/, src/pkg/, a package).  A full walk of a
# repository that also holds boards and run outputs costs tens of
# seconds, so it is never done: a name found nowhere shallow is the
# environment, and a module a worker loads from deeper down shows up in
# its measured set.
_SHALLOW_DEPTH = 3

# A worker may receive the parent's static half instead of deriving its
# own, so that both hash the same files.
FILES_ENV = 'FLUXPCB_CODE_FILES'


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


def _rel_package(path: str) -> str:
    """'fluxpcb/meta/codefp.py' for a file inside the package, else ''."""
    parts = os.path.abspath(path).replace(os.sep, '/').split('/')
    for i in range(len(parts)):
        if parts[i] == 'fluxpcb':
            return '/'.join(parts[i:])
    return ''


def _is_experimenter(path: str) -> bool:
    return _rel_package(path) in EXPERIMENTER_MODULES


def _norm(path) -> str:
    """Absolute, symlink-resolved, '/'-separated: one key per file."""
    return os.path.realpath(os.path.abspath(str(path))).replace(os.sep, '/')


def _repo_root(path) -> str:
    """Nearest ancestor of `path` (itself included when it is a directory)
    that is a git work tree, else its own directory."""
    p = os.path.abspath(str(path))
    d = p if os.path.isdir(p) else os.path.dirname(p)
    while True:
        if os.path.exists(os.path.join(d, '.git')):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return os.path.dirname(p)
        d = parent


def _own_package() -> str:
    """The system under test: the package this measurer ships in."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _under(path, root) -> bool:
    """Is `path` inside `root`, whichever side of a link each is named by?"""
    p = _norm(path)
    for r in (os.path.abspath(str(root)), _norm(root)):
        r = str(r).replace(os.sep, '/').rstrip('/') + '/'
        if p.startswith(r) or os.path.abspath(str(path)).replace(
                os.sep, '/').startswith(r):
            return True
    return False


# ---------------------------------------------------------------- the index
class _Index:
    """Where a top-level import name can come from, inside known roots.

    A name maps to EVERY candidate of the same class: the order of sys.path
    entries is not known statically, and hashing one module too many costs
    freshness, never correctness.  Candidates come in three classes:

      pkg    a package directory (name is importable if its parent is on
             sys.path — the normal case)
      plain  a module in a directory that is not itself a package
             (scripts/, tools/, a repository root)
      inpkg  a module inside a package directory: importable by its bare
             name only after that directory was put on sys.path, which
             modules do for their OWN siblings.  Counted when the importer
             lives in that directory, or when nothing else resolves the
             name — otherwise `import builtins` would pick up a file that
             happens to be called builtins.py.

    The system under test is pinned to the package the measurer ships in:
    a repository may hold an older copy of it under the same name.
    """

    def __init__(self):
        self.roots = []        # repository roots, as named
        self.top = {}          # top-level name -> [(module file | package dir, class)]
        self.pins = {}         # top-level name -> the one package that counts
        self._file_set = set()
        self._seen = set()

    def add_root(self, root):
        root = os.path.abspath(root)
        if root in self.roots:
            return
        self.roots.append(root)
        self._walk(root, 0, _SHALLOW_DEPTH)

    def _walk(self, d: str, depth: int, limit):
        real = _norm(d)
        if real in self._seen:             # a link back into a walked tree
            return
        self._seen.add(real)
        try:
            with os.scandir(d) as it:
                entries = sorted(it, key=lambda e: e.name)
        except OSError:
            return
        for e in entries:
            if e.is_file() and e.name.endswith('.py'):
                self._file(e.path)
        if limit is not None and depth >= limit:
            return
        for e in entries:
            if e.is_dir() and e.name not in _SKIP_DIRS:
                if not os.path.isfile(os.path.join(e.path, '__init__.py')):
                    # a directory without __init__ is a namespace package:
                    # `import vendor.lib` works through it
                    same = self.top.setdefault(e.name, [])
                    if not any(c == e.path for c, _ in same):
                        same.append((e.path, 'ns'))
                self._walk(e.path, depth + 1, limit)

    def _file(self, path: str):
        if path in self._file_set:
            return
        self._file_set.add(path)
        name = os.path.basename(path)
        pkg = os.path.dirname(path)
        is_pkg = os.path.isfile(os.path.join(pkg, '__init__.py'))
        if name != '__init__.py__':
            self.top.setdefault(name[:-3], []).append(
                (path, 'inpkg' if is_pkg else 'plain'))
        else:
            same = self.top.setdefault(os.path.basename(pkg), [])
            if not any(c == pkg for c, _ in same):
                same.append((pkg, 'pkg'))

    def resolve(self, dotted: str, importer: str = None) -> tuple:
        """(files, status) that `dotted` can denote.

        status: 'found' | 'external' (resolved outside every repository,
        or nowhere).
        """
        first, _, rest = dotted.partition('.')
        if first in self.pins:             # the system under test, and only it
            files = self._descend(self.pins[first], rest)
            return files, ('found' if files else 'external')
        here = os.path.dirname(os.path.abspath(importer)) if importer else None
        cands = list(self.top.get(first, []))
        if '.' in dotted:
            # a.b.c: `a` has to be an importable top-level name on its own,
            # so only the classes that need no sys.path trick count
            strong = [c for c, k in cands if k != 'inpkg']
            if importer:
                same = [c for c in strong
                        if _repo_root(c) == _repo_root(importer)]
                strong = same or strong
            files = [f for cand in strong for f in self._descend(cand, rest)]
            return files, ('found' if files else 'external')
        strong = [c for c, k in cands
                  if k != 'inpkg' or (here and os.path.dirname(c) == here)]
        weak = [c for c, k in cands if c not in strong]
        # a name that exists in two repositories (a package copied into the
        # bench tree) resolves inside the importer's own repository first
        # — nothing puts the other repository root on sys.path
        if importer:
            same = [c for c in strong
                    if _repo_root(c) == _repo_root(importer)]
            strong = same or strong
        files = [f for cand in strong for f in self._descend(cand, rest)]
        if files:
            return files, 'found'
        # nothing strong: where would Python itself find the bare name?
        located = _locate(first, self)
        if located:
            return located, 'found'
        if located == []:                 # the environment, not the code
            return [], 'external'
        files = [f for cand in weak for f in self._descend(cand, rest)]
        return files, ('found' if files else 'external')

    @staticmethod
    def _descend(cand: str, rest: str) -> list:
        if not os.path.isdir(cand):
            return [cand] if not rest else []
        cur = cand
        for part in rest.split('.'):
            mod, sub = os.path.join(cur, part + '.py'), os.path.join(cur, part)
            if os.path.isfile(mod):
                cur = mod
            elif os.path.isdir(sub):
                cur = sub
            else:
                return []
        # importing a.b.c runs every package __init__ above it
        out, d = [], (os.path.dirname(cur) if os.path.isfile(cur) else cur)
        while os.path.isdir(d) and os.path.isfile(os.path.join(d, '__init__.py')):
            out.append(os.path.join(d, '__init__.py'))
            d = os.path.dirname(d)
        if os.path.isfile(cur):
            out.append(cur)
        return out


# ---------------------------------------------------------------- the AST
def _module_package(path: str) -> list:
    """Package parts of a file: ['fluxpcb', 'kicadgen'] for geomir.py."""
    parts, d = [], os.path.dirname(os.path.abspath(path))
    while os.path.isfile(os.path.join(d, '__init__.py')):
        parts.append(os.path.basename(d.rstrip(os.sep)))
        d = os.path.dirname(d)
    parts.reverse()
    return parts


def _imports_split(tree: ast.AST, pkg: list) -> tuple:
    """(module-level names, deferred names, unresolvable) in one module.

    An import at module level runs when the module is imported; one
    inside a function body runs only if that function is called.  The
    two are kept apart because a cell's path is the module-level code
    plus what that cell measured loading.
    """
    top, deferred, dynamic = [], [], []

    def handle(node, sink):
        if isinstance(node, ast.Import):
            sink.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module.split('.') if node.module else []
            if node.level:
                cut = len(pkg) - (node.level - 1)
                base = (pkg[:cut] if cut >= 0 else []) + base
            sink.append('.'.join(base))
            sink.extend('.'.join(base + [alias.name]) for alias in node.names)
        elif isinstance(node, ast.Call):
            f = node.func
            fn = (f.attr if isinstance(f, ast.Attribute)
                  else f.id if isinstance(f, ast.Name) else None)
            if fn in ('import_module', '__import__') and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    sink.append(arg.value)
                else:
                    dynamic.append((sink is deferred,
                                    f'{fn}() on a name that is not a literal '
                                    f'(line {node.lineno})'))

    def walk(node):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                  ast.Lambda)):
                for inner in ast.walk(child):
                    handle(inner, deferred)
            else:
                handle(child, top)
                walk(child)

    walk(tree)
    return top, deferred, dynamic


def _in_package(path: str) -> bool:
    """Does the file live in a Python package directory?"""
    return os.path.isfile(os.path.join(os.path.dirname(path), '__init__.py'))


def _script_literals(tree: ast.AST, index: _Index) -> list:
    """Project scripts a module names by path (subprocess entry points).

    A string that names an existing .py file outside every package is an
    entry point the AST cannot otherwise see: a script run by path lives
    in a plain directory (scripts/, tools/).  A .py string that resolves
    INSIDE a package is a module path written as data ('fluxpcb/meta/x.py'
    in a list of module names) and is ignored — following those pulled an
    older copy of the whole package into the closure.  A bare file name
    ('tool.py') is data as well.
    """
    out = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant)
                and isinstance(node.value, str)):
            continue
        s = node.value.replace('\\', '/')
        if not s.endswith('.py') or len(s) < 6:
            continue
        if os.path.isabs(s):
            if os.path.isfile(s):
                out.append(s)
            continue
        if '/' not in s:
            continue
        for r in index.roots:
            p = os.path.join(r, s)
            if os.path.isfile(p) and not _in_package(p):
                out.append(p)
    return out


# ---------------------------------------------------------------- closure
def _locate(name: str, index: _Index) -> list | None:
    """A top-level name the index does not know: where Python would find it.

    None  — found nowhere at all.
    []    — found outside every repository: the environment, not the code.
    files — inside a repository: index it and resolve.
    """
    try:
        spec = importlib.util.find_spec(name)
    except Exception:
        spec = None
    if spec is None:
        return None                           # not importable here at all
    origin = getattr(spec, 'origin', None)
    if not origin or not str(origin).endswith('.py'):
        return []                             # built-in / namespace / frozen
    if not os.path.exists(os.path.join(_repo_root(origin), '.git')):
        return []
    index.add_root(_repo_root(origin))
    return index.resolve(name)[0]


def path_closure(entries, extra=(), required: str = None) -> dict:
    """The execution path of `entries`, in two tiers.

    entries: entry scripts (.py).  extra: further files/dirs; .py files
    among them are hashed in full AND scanned as entries.  required: the
    directory of the system under test — a closure that misses it is a
    broken prediction, not a fingerprint.

    'files' is the module-level half (runs in every cell); 'deferred' is
    reachable only through imports inside functions (runs in the cells
    that load it).
    """
    if required is None:
        required = _own_package()
    info = {'scheme': SCHEME, 'dynamic': [], 'dynamic_deferred': [],
            'external': [], 'scripts': [], 'imported_by': {},
            'external_repos': {}}
    index = _Index()
    index.pins[os.path.basename(required)] = required
    entries = [os.path.abspath(e) for e in entries]
    scripts = entries + [os.path.abspath(e) for e in extra
                         if str(e).endswith('.py')]
    for p in scripts + [required]:
        index.add_root(_repo_root(p))
    own = {_repo_root(p) for p in scripts + [required]}

    tier, why_of, named = {}, {}, {}     # norm path -> 'L' | 'D'
    queue = [(p, 'entry', 'L') for p in scripts]
    while queue:
        path, why, t = queue.pop()
        key = _norm(path)
        if tier.get(key) in (t, 'L'):
            continue
        tier[key], why_of[key], named[key] = t, why, path
        try:
            with open(path, 'rb') as fh:
                tree = ast.parse(fh.read(), filename=path)
        except (OSError, SyntaxError, ValueError) as e:
            info['dynamic'].append(f'{path}: unreadable ({type(e).__name__})')
            continue
        top, deferred, dynamic = _imports_split(tree, _module_package(path))
        for is_deferred, note in dynamic:
            info['dynamic_deferred' if is_deferred else 'dynamic'].append(
                f'{path}: {note}')
        # a module reached only through a function-level import is
        # deferred together with everything it imports at module level
        for dotted in sorted(set(top)):
            found, status = index.resolve(dotted, path)
            queue.extend((f, f'import {dotted} <- {path}', t) for f in found)
            if not found and t == 'L' and '.' not in dotted:
                info['external'].append(dotted)
        for dotted in sorted(set(deferred)):
            found, _ = index.resolve(dotted, path)
            queue.extend((f, f'import {dotted} <- {path}', 'D') for f in found)
        for script in _script_literals(tree, index):
            if script not in info['scripts']:
                info['scripts'].append(script)
            queue.append((script, f'script literal in {path}', t))
    # non-.py extras (a jar, a data file the entry reads) are hashed whole
    for e in extra:
        e = str(e)
        if e.endswith('.py'):
            continue
        for f in (_py_files(e) if os.path.isdir(e) else [e]):
            key = _norm(f)
            tier[key], why_of[key], named[key] = 'L', 'extra', f
    # Code we own is hashed.  A third-party checkout nested in the tree
    # (a benchmark environment under the bench directory) is environment,
    # like the interpreter: it is recorded, not hashed, and counts for a
    # cell only if that cell actually loads it.
    level, deferred = [], []
    for key, t in tier.items():
        path = named[key]
        root = _repo_root(path) if str(path).endswith('.py') else None
        if root is not None and root not in own:
            r = _norm(root)
            info['external_repos'][r] = info['external_repos'].get(r, 0) + 1
        elif t == 'L':
            level.append(path)
        else:
            deferred.append(path)
    info['entries'] = scripts
    info['roots'] = list(index.roots)
    info['own_roots'] = sorted(own)
    info['files'] = sorted(set(level), key=_norm)
    info['deferred'] = sorted(set(deferred) - set(level), key=_norm)
    info['imported_by'] = why_of
    info['external'] = sorted(set(info['external']))
    if not any(_under(f, required) for f in info['files']):
        info['dynamic'].append('the system under test is not on the path')
    return info


_HASH = {}           # norm path -> (mtime_ns, size, content digest)


def _file_hash(path: str):
    """Content digest of one file, cached while its stat does not change.

    A parent checks every cached cell against the code on disk, and the
    cells share most of their files; this reads each file once per
    change instead of once per cell.
    """
    try:
        st = os.stat(path)
    except OSError:
        return None
    hit = _HASH.get(path)
    if hit and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
        return hit[2]
    try:
        with open(path, 'rb') as fh:
            blob = fh.read()
    except OSError:
        return None
    entry = (st.st_mtime_ns, st.st_size, hashlib.sha256(blob).digest())
    _HASH[path] = entry
    return entry[2]


def _label(f: str) -> str:
    """`<repository name>:<path inside it>` for a normalized path.

    The same for a link and its target, and whichever process computes
    it: the parent names the entry through a link, the worker through
    its target, and a label taken relative to a list of roots came out
    differently in the two.
    """
    r = _norm(_repo_root(f))
    return (f'{os.path.basename(r.rstrip("/"))}:{f[len(r) + 1:]}'
            if f.startswith(r + '/') else f)


def _digest(info: dict) -> str:
    h = hashlib.sha256()
    h.update(info['scheme'].encode('utf-8'))
    for f in sorted(set(map(_norm, info['files']))):
        if _rel_package(f) in INSTRUMENT_MODULES:
            continue
        digest = _file_hash(f)
        if digest is None:
            continue
        h.update(_label(f).encode('utf-8'))
        h.update(digest)
    return h.hexdigest()[:16]


def _whole_package(extra, entries=(), required: str = None) -> dict:
    """The conservative fallback: every module of the system under test
    plus the entry scripts, minus the measurer."""
    root = required or _own_package()
    files = [os.path.abspath(e) for e in entries]
    files.extend(_py_files(root))
    for e in extra:
        e = str(e)
        files.extend(_py_files(e) if os.path.isdir(e) else [e])
    return {'scheme': 'all', 'entries': list(map(os.path.abspath, entries)),
            'files': sorted(set(files)),
            'deferred': [], 'dynamic': ['fallback: whole package'],
            'dynamic_deferred': [], 'external': [], 'scripts': [],
            'imported_by': {}, 'external_repos': {}, 'roots': [root]}


def code_fingerprint(extra_paths=(), required: str = None) -> str:
    """Fingerprint of the STATIC half: the code every cell runs.

    Computed once per process for a given argument: a batch must see one
    code state from its first cell to its last, and a cell that ran
    under other code is stale by comparison, not by re-hashing.
    `static_changed()` answers whether the files behind it changed since.

    `required` is the directory of the system under test; it defaults to
    the package this measurer ships in.
    """
    global _LAST_KEY
    key = (tuple(sorted(map(str, extra_paths))),
           os.path.abspath(required) if required else None)
    if key not in _CACHE:
        entries = [p for p in extra_paths
                   if str(p).endswith('.py') and os.path.isfile(p)]
        other = [p for p in extra_paths if p not in entries]
        given = [f for f in os.environ.get(FILES_ENV, '').split(os.pathsep)
                 if f]
        if given:
            # the parent's prediction: hash exactly the files it hashed
            info = {'scheme': SCHEME, 'entries': [], 'files': given,
                    'deferred': [], 'dynamic': [], 'dynamic_deferred': [],
                    'external': [], 'scripts': [], 'imported_by': {},
                    'external_repos': {},
                    'roots': sorted({_repo_root(f) for f in given}, key=len,
                                    reverse=True)}
        else:
            info = path_closure(entries, other, required)
        if info['dynamic']:
            info = _whole_package(other, entries, required)
        _CACHE[key] = _digest(info)
        _INFO[key] = info
    _LAST_KEY = key
    return _CACHE[key]


def static_changed(extra_paths=(), required: str = None) -> bool:
    """Did any file of the static half change since it was taken?"""
    code_fingerprint(extra_paths, required)
    return _digest(_INFO[_LAST_KEY]) != _CACHE[_LAST_KEY]


def forget() -> None:
    """Drop the cached fingerprints (tests that edit files call this)."""
    _CACHE.clear()
    _INFO.clear()
    _HASH.clear()
    globals()['_LAST_KEY'] = None


def _last() -> dict:
    if _LAST_KEY is None:
        code_fingerprint()
    return _INFO[_LAST_KEY]


def fingerprint_info() -> dict:
    """What the last code_fingerprint() call hashed, and why."""
    info = {k: v for k, v in _last().items() if k != 'imported_by'}
    info['files'] = [_norm(f) for f in info['files']]
    info['deferred'] = [_norm(f) for f in info.get('deferred', [])]
    info['n_files'] = len(info['files'])
    info['n_deferred'] = len(info['deferred'])
    return info


def closure_files() -> list:
    """The static half, to hand to a worker (FILES_ENV)."""
    return list(fingerprint_info()['files'])


def loaded_project_files() -> list:
    """Project files THIS process has loaded — the measured half.

    'Project' is anything under a repository the fingerprint indexed;
    the interpreter's own modules and site-packages are the environment.
    """
    roots = _last().get('roots') or [_repo_root(_own_package())]
    out = set()
    for mod in list(sys.modules.values()):
        f = getattr(mod, '__file__', None)
        if f and str(f).endswith('.py') and any(_under(f, r) for r in roots):
            out.add(_norm(f))
    return sorted(out)


def cell_fingerprint(loaded=()) -> str:
    """Fingerprint of one cell: the static half plus what it loaded."""
    info = dict(_last())
    have = {_norm(f) for f in info['files']}
    extra = [f for f in (loaded or []) if _norm(f) not in have]
    if extra:
        info['files'] = list(info['files']) + list(extra)
    return _digest(info)


def gaps(loaded) -> list:
    """Loaded files the static analysis cannot reach, at either tier.

    A cell's fingerprint already covers them (it hashes what was loaded),
    so this is not a hole in the measurement; it is a hole in the
    prediction, which matters for the code a SUBPROCESS runs by path —
    that half is only ever predicted.
    """
    info = _last()
    known = {_norm(f) for f in info['files']}
    known |= {_norm(f) for f in info.get('deferred', [])}
    known.add(_norm(__file__))
    return sorted(set(map(_norm, loaded)) - known)
