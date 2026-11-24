"""Tool gate — the admission ladder a new tool must climb.

L0 static   : compiles; AST allows only whitelisted imports; no board
              name or absolute-coordinate literal (the generality
              invariant); declares a run(args) entry point.
L1 smoke    : runs on a sealed mini fixture and must return an ok envelope.
L2 effect   : on-policy transactional evaluation with repeats — supplied
              by the campaign driver as `evaluate(spec) -> verdict`.
L3 controls : sealed known-bad boards must NOT be judged clean.
L4 held-out : component 2's confirm on boards never used to choose.

Every level records its evidence; a gate that was not run does not pass.
"""
from __future__ import annotations

import ast
import hashlib
import importlib
import importlib.util
import inspect
import json
import os
import re
import sys

ALLOWED_IMPORTS = (
    'math', 'json', 'itertools', 'dataclasses', 'pathlib', 'heapq',
    'typing', 'collections', 're', 'functools', 'copy', 'time',
    'pcbnew', 'fluxpcb', 'routegrid', 'geomir', 'maze', 'whynot',
)
BANNED_IMPORTS = (
    'subprocess', 'socket', 'urllib', 'requests', 'shutil', 'os', 'sys',
    'ctypes', 'multiprocessing', 'threading', 'pickle', 'importlib',
)
BOARD_NAME_RE = re.compile(r'00\d{2}_|h743|d3[abc]|esp32|rp2040|nrf24',
                           re.IGNORECASE)
COORD_RE = re.compile(r'\b\d{3,}\.\d{2,}\b')   # absolute-mm literal guard


def check_static(source: str, name: str) -> dict:
    """L0.  Returns {'ok', 'problems': [...], 'parsed': bool}."""
    problems = []
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return {'ok': False, 'problems': [f'syntax: {e}'], 'parsed': False}
    funcs = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    if 'run' not in funcs:
        problems.append('no run(args) entry point')
    # comments are not in the AST: scan the raw text for the generality
    # invariant too (a board name anywhere is a board-specific tool)
    for m in BOARD_NAME_RE.finditer(source):
        problems.append(f'board-name literal: {source[max(0, m.start()-20):m.end()+8]!r}')
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                _check_import(a.name, problems)
        elif isinstance(node, ast.ImportFrom):
            _check_import(node.module or '', problems)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if BOARD_NAME_RE.search(node.value):
                problems.append(f'board-name literal: {node.value[:40]!r}')
            if COORD_RE.search(node.value):
                problems.append(f'absolute-coordinate literal: {node.value[:40]!r}')
        elif isinstance(node, ast.Constant) and isinstance(node.value, float):
            # an absolute mm coordinate has a fractional part and a
            # board-sized magnitude (148.5, 107.05); sentinels (9e9),
            # unit factors (1e6) and whole-number parameters are not
            v = abs(node.value)
            if 20.0 < v < 5000.0 and v != int(v):
                problems.append(f'absolute-coordinate constant {node.value}')
    if name and not re.fullmatch(r'[a-z][a-z0-9_]*', name):
        problems.append(f'bad tool name {name!r}')
    return {'ok': not problems, 'problems': problems, 'parsed': True}


def _check_import(module, problems):
    top = module.split('.')[0]
    if top in BANNED_IMPORTS:
        problems.append(f'banned import {top!r}')
    elif not any(top == a.split('.')[0] for a in ALLOWED_IMPORTS):
        problems.append(f'not whitelisted import {top!r}')


# ---------------------------------------------------------------- name gate
def _closest(name: str, valid) -> str:
    """The valid symbol closest to a hallucinated one (simple ratio)."""
    import difflib
    hits = difflib.get_close_matches(name, sorted(valid), n=1, cutoff=0.3)
    return hits[0] if hits else ''


# top-level names tools import the primitives by -> package path
TOP_ALIASES = {
    'routegrid': 'fluxpcb.kicadgen.routegrid',
    'geomir': 'fluxpcb.kicadgen.geomir',
    'maze': 'fluxpcb.kicadgen.maze',
    'whynot': 'fluxpcb.kicadgen.whynot',
}


def _load(module: str):
    """Import by the name a tool uses; resolve top-level aliases."""
    import importlib
    target = TOP_ALIASES.get(module, module)
    try:
        return importlib.import_module(target)
    except Exception:
        return None


def contract_pack() -> dict:
    """Every symbol a tool may use, keyed by how the code reaches it.

    Top-level modules in ALLOWED_IMPORTS and their public attributes.  The
    primitives are imported by their top-level names (routegrid, geomir,
    maze, whynot) which only resolve inside the kicadgen directory, so
    they are loaded through the package and recorded under the alias.
    Built by introspection so it cannot drift from the code.
    """
    import inspect
    pack = {'modules': {}, 'aliases': dict(TOP_ALIASES)}
    for mod in ALLOWED_IMPORTS:
        m = _load(mod)
        if m is None:
            # pcbnew & friends live in KiCad's interpreter; keep the name
            pack['modules'][mod] = None
            continue
        pack['modules'][mod] = {n for n, _ in inspect.getmembers(m)
                                if not n.startswith('_')}
    pkg = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for sub in ('kicadgen', 'tools', 'verifiers', 'meta', 'knowledge'):
        d = os.path.join(pkg, sub)
        if not os.path.isdir(d):
            continue
        for f in sorted(os.listdir(d)):
            if f.endswith('.py'):
                pack.setdefault('submodules', {}).setdefault(sub, set()).add(f[:-3])
    return pack


def name_gate(source: str, pack: dict = None) -> dict:
    """L0b: does the code only name symbols that exist?

    A proposer that sees tool names instead of signatures invents
    functions (`routegrid.build_obstacles`).  This resolves every
    `module.name` and `from module import name` against the introspected
    contract and returns each offender with the closest valid symbol, so
    one repair round can fix it.
    """
    pack = pack or contract_pack()
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return {'ok': False, 'problems': [f'syntax: {e}']}
    bound = {}            # local name -> module it refers to
    from_names = {}       # local name -> (module, original name)
    problems = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                top = a.name.split('.')[0]
                if top in pack['modules']:
                    bound[a.asname or top] = a.name
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ''
            top = mod.split('.')[0]
            if top not in pack['modules']:
                continue                       # the import gate reports it
            valid = pack['modules'].get(top)
            for a in node.names:
                if a.name == '*':
                    continue
                if valid is not None and a.name not in valid and mod == top:
                    # fluxpcb subpackages are resolved below
                    if not (top == 'fluxpcb' and _is_fluxpcb_path(a.name)):
                        problems.append({'symbol': f'{mod}.{a.name}',
                                         'closest': _closest(a.name, valid)})
                from_names[a.asname or a.name] = (mod, a.name)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute) or not isinstance(node.value, ast.Name):
            continue
        local = node.value.id
        if local not in bound and local not in from_names:
            continue
        if local in bound:
            module = bound[local]
            attr = node.attr
            valid = _module_attrs(module, pack)
            if valid is not None and attr not in valid:
                problems.append({'symbol': f'{module}.{attr}',
                                 'closest': _closest(attr, valid)})
        else:
            mod, name = from_names[local]
            valid = _module_attrs(f'{mod}.{name}', pack)
            if valid is not None and node.attr not in valid:
                problems.append({'symbol': f'{mod}.{name}.{node.attr}',
                                 'closest': _closest(node.attr, valid)})
    # dedupe, keep order
    seen, out = set(), []
    for p in problems:
        k = p['symbol']
        if k not in seen:
            seen.add(k)
            out.append(p)
    return {'ok': not out, 'problems': out}


def _is_fluxpcb_path(name: str) -> bool:
    return name in ('kicadgen', 'tools', 'verifiers', 'meta', 'knowledge',
                   'router', 'design_agent', 'harness', 'contract')


def _module_attrs(module: str, pack: dict) -> set:
    """Public names of an imported module (None when it cannot be loaded)."""
    if module in pack['modules'] and pack['modules'][module] is not None:
        return pack['modules'][module]
    m = _load(module)
    if m is None:
        return None
    return {n for n, _ in inspect.getmembers(m) if not n.startswith('_')}


# ---------------------------------------------------------------- sandbox
class Sandbox:
    """Loads candidate tool code by module name only, from its own dir."""

    def __init__(self, root: str):
        self.root = os.path.abspath(root)
        os.makedirs(self.root, exist_ok=True)

    def write(self, tool_id: str, source: str) -> str:
        d = os.path.join(self.root, tool_id)
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, 'tool.py')
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write(source)
        return path

    def load(self, tool_id: str):
        path = os.path.join(self.root, tool_id, 'tool.py')
        if not os.path.exists(path):
            raise FileNotFoundError(path)
        # tools may import the primitives by their top-level names
        # (routegrid / geomir / maze), as the whitelist promises
        pkg = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for p in (pkg, os.path.join(pkg, 'kicadgen')):
            if p not in sys.path:
                sys.path.insert(0, p)
        spec = importlib.util.spec_from_file_location(f'sandbox_{tool_id}', path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        return mod

    def check(self, tool_id: str) -> dict:
        try:
            with open(os.path.join(self.root, tool_id, 'tool.py'),
                      encoding='utf-8') as fh:
                source = fh.read()
        except OSError as e:
            return {'ok': False, 'problems': [f'read: {e}']}
        return check_static(source, tool_id)


# ---------------------------------------------------------------- ladder
def ladder(source: str, tool_id: str, smoke=None, effect=None,
           controls=None, confirm=None) -> dict:
    """Run every supplied level; missing levels are 'not_run'."""
    levels = {}
    l0 = check_static(source, tool_id)
    levels['l0_static'] = {'pass': l0['ok'], 'problems': l0['problems']}
    if not l0['ok']:
        return _verdict(levels)
    gate = name_gate(source)
    levels['l0_names'] = {'pass': gate['ok'], 'problems': gate['problems']}
    if not gate['ok']:
        return _verdict(levels)
    try:
        sb = Sandbox(_sandbox_root())
        sb.write(tool_id, source)
        mod = sb.load(tool_id)
        levels['sandbox'] = {'pass': True, 'module': mod.__name__}
    except Exception as e:
        levels['sandbox'] = {'pass': False, 'error': f'{type(e).__name__}: {e}'}
        return _verdict(levels)
    for name, ev in (('l1_smoke', smoke), ('l2_effect', effect),
                     ('l3_controls', controls), ('l4_confirm', confirm)):
        if ev is None:
            levels[name] = {'pass': None}
            continue
        try:
            verdict = ev() if callable(ev) else ev
        except Exception as e:
            verdict = {'pass': False, 'error': f'{type(e).__name__}: {e}'}
        levels[name] = dict(verdict)
    return _verdict(levels)


def _verdict(levels):
    judged = [v['pass'] for k, v in levels.items()
              if isinstance(v, dict) and v.get('pass') is not None]
    not_run = [k for k, v in levels.items()
               if isinstance(v, dict) and v.get('pass') is None]
    failed = next((k for k, v in levels.items()
                   if isinstance(v, dict) and v.get('pass') is False), None)
    return {'levels': levels,
            'admitted': bool(judged) and not not_run and all(judged),
            'blocked': failed or (not_run[0] if not_run else None),
            'not_run': not_run}


def _sandbox_root() -> str:
    return os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), 'sandbox')


def digest_of(source: str) -> str:
    return hashlib.sha256(source.encode('utf-8')).hexdigest()[:16]
