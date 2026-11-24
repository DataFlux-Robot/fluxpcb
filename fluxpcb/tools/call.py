"""Tool call executor.

Two execution modes, one envelope:

  inline      the tool's fn is a callable resolved by dotted path (pure
              geometry tools run in the meta process);
  subprocess  the tool's driver script runs under KICAD_PYTHON with
              `board <args.json>` and prints one `__TOOL__{...}` line —
              the meta process never imports pcbnew.

Every call is wrapped in a timeout and returns
{'ok', 'result', 'error', 'seconds', 'tool', 'digest'}; a crash is an
error, never an exception the loop has to catch.
"""
from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def _resolve(fn: str):
    module, _, attr = fn.rpartition('.')
    return getattr(importlib.import_module(module), attr)


def run_inline(spec, arguments: dict, timeout_s: float) -> dict:
    t0 = time.time()
    try:
        fn = _resolve(spec.fn)
        # convention: an inline tool takes ONE dict of arguments
        result = fn(dict(arguments))
        return {'ok': True, 'result': result,
                'seconds': round(time.time() - t0, 2)}
    except Exception as e:
        return {'ok': False, 'error': f'{type(e).__name__}: {e}',
                'seconds': round(time.time() - t0, 2)}


def run_subprocess(spec, arguments: dict, ctx: dict,
                   timeout_s: float) -> dict:
    """Run <KICAD_PYTHON> <driver> <board> <args.json>; parse __TOOL__."""
    t0 = time.time()
    board = ctx.get('board')
    kicad_py = ctx.get('kicad_python') or os.environ.get('KICAD_PYTHON', sys.executable)
    driver = spec.driver
    if not os.path.isabs(driver):
        driver = os.path.join(HERE, 'drivers', driver)
    if not os.path.exists(driver):
        return {'ok': False, 'error': f'driver missing: {driver}',
                'seconds': round(time.time() - t0, 2)}
    if not board or not os.path.exists(board):
        return {'ok': False, 'error': f'board missing: {board}',
                'seconds': round(time.time() - t0, 2)}
    tmp = tempfile.mkdtemp(prefix='tool_')
    args_path = os.path.join(tmp, 'args.json')
    with open(args_path, 'w', encoding='utf-8') as fh:
        json.dump(arguments, fh)
    cmd = [kicad_py, driver, board, args_path]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding='utf-8', errors='replace',
                           timeout=timeout_s)
        for line in (r.stdout or '').splitlines():
            if line.startswith('__TOOL__'):
                body = json.loads(line[len('__TOOL__'):])
                return dict(body, seconds=round(time.time() - t0, 2))
        return {'ok': False, 'error': ('no __TOOL__ line; stderr: '
                                       + (r.stderr or '')[-300:]),
                'seconds': round(time.time() - t0, 2)}
    except subprocess.TimeoutExpired:
        return {'ok': False, 'error': f'timeout after {timeout_s}s',
                'seconds': round(time.time() - t0, 2)}
    except Exception as e:
        return {'ok': False, 'error': f'{type(e).__name__}: {e}',
                'seconds': round(time.time() - t0, 2)}


def call(spec, arguments: dict, ctx: dict = None,
         timeout_s: float = 300) -> dict:
    """One envelope for both modes."""
    ctx = ctx or {}
    try:
        out = (run_inline(spec, arguments, timeout_s)
               if spec.fn else run_subprocess(spec, arguments, ctx, timeout_s))
    except Exception as e:
        out = {'ok': False, 'error': f'{type(e).__name__}: {e}'}
    out['tool'] = spec.name
    out['digest'] = spec.digest()
    return out


def emit_line(payload: dict) -> None:
    """Drivers call this: print one __TOOL__ line and exit."""
    sys.stdout.write('__TOOL__' + json.dumps(payload, default=str) + '\n')
    sys.stdout.flush()
