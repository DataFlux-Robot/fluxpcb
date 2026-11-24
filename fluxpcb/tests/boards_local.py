"""Real boards for the tests that need pcbnew.

The boards are not in git — open-source designs redistributed by
PCBench; this repository carries scripts and result summaries only.
They live under FLUXPCB_FIXTURES (default: the bench's ``fixtures``
directory), in the state a benchmark run left them, and
``manifest.json`` there says which defect each carries.

A missing fixture set SKIPS the test that needed it.  It never turns
the test into a pass.
"""
import json
import os
import shutil
import subprocess
import sys
from collections import Counter

import pytest

DEFAULT_ROOT = 'C:/zcode_pcbworld/fixtures'


def root():
    r = os.environ.get('FLUXPCB_FIXTURES', DEFAULT_ROOT)
    if not os.path.isfile(os.path.join(r, 'manifest.json')):
        pytest.skip('board fixtures are not installed (FLUXPCB_FIXTURES)')
    return r


def manifest():
    with open(os.path.join(root(), 'manifest.json'), encoding='utf-8') as fh:
        return json.load(fh)


def kicad_cli():
    """The kicad-cli that goes with this interpreter, or skip."""
    cli = os.environ.get('KICAD_CLI') or shutil.which('kicad-cli')
    if not cli:
        beside = os.path.join(os.path.dirname(sys.executable), 'kicad-cli.exe')
        cli = beside if os.path.exists(beside) else None
    if not cli:
        pytest.skip('kicad-cli not found')
    os.environ['KICAD_CLI'] = cli
    return cli


def _copy(src, dst):
    os.makedirs(dst, exist_ok=True)
    for f in os.listdir(src):
        if f.endswith(('.kicad_pcb', '.kicad_pro')):
            shutil.copy(os.path.join(src, f), os.path.join(dst, f))


def board(key, tmp_path):
    """A copy of fixture `key` beside its project; the pcb's path."""
    entry = next(b for b in manifest()['boards'] if b['key'] == key)
    dst = os.path.join(str(tmp_path), key)
    _copy(os.path.join(root(), 'boards', key), dst)
    return os.path.join(dst, entry['board'] + '.kicad_pcb')


def control(name, tmp_path):
    """A copy of a sealed known-bad control board; the pcb's path."""
    dst = os.path.join(str(tmp_path), 'ctl', name)
    _copy(os.path.join(root(), 'controls', name), dst)
    return os.path.join(dst, 'circuit.reference.kicad_pcb')


def control_names():
    return list(manifest()['controls'])


def drc(pcb):
    """The real DRC on a saved board (zones refilled): (error findings,
    unconnected items), non-excluded."""
    out = pcb + '.drc.json'
    subprocess.run([kicad_cli(), 'pcb', 'drc', '--format', 'json',
                    '--severity-all', '--refill-zones', '--output', out, pcb],
                   capture_output=True, text=True, timeout=900)
    with open(out, encoding='utf-8') as fh:
        d = json.load(fh)
    errs = [v for v in d.get('violations', [])
            if v.get('severity') == 'error' and not v.get('excluded')]
    unconn = [u for u in d.get('unconnected_items', []) if not u.get('excluded')]
    return errs, unconn


def by_type(findings):
    return dict(Counter(v.get('type') for v in findings))


def load(pcb):
    import pcbnew
    return pcbnew.LoadBoard(pcb)


def save(board, pcb):
    from fluxpcb.repair_drc import save_preserving_project
    save_preserving_project(board, pcb)


def violations_of(findings):
    from fluxpcb.repair_drc import parse_violations
    return parse_violations({'violations': findings})
