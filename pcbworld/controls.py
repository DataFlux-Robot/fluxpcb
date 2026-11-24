#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Sealed known-bad controls for the gate.

A candidate change is judged by a scorer.  If the change blinded the scorer
— or let a repair "fix" something by moving the goalposts — every board
would look clean and the gate would wave it through.  So the gate also
runs the change over boards that are KNOWN bad for a reason no routing can
remove: a pad moved into a neighbour of another net.  Routing moves tracks,
never pads, so the violation survives every arm; any arm that calls such a
board clean is not measuring.

Each control records what was changed and the DRC types that make it bad,
and is never edited afterwards.
"""
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, '..', 'flux-neurotransmitter'))

CONTROLS = os.path.join(HERE, 'controls')
KICAD_CLI = os.environ.get('KICAD_CLI', 'kicad-cli')
PAD_TYPES = ('clearance', 'shorting_items')


def _is_pad(desc: str) -> bool:
    d = (desc or '').lower()
    return '焊盘' in d or 'pad' in d


def drc(board: str, out: str) -> dict:
    subprocess.run([KICAD_CLI, 'pcb', 'drc', '--format', 'json',
                    '--severity-error', '--refill-zones', '-o', out, board],
                   capture_output=True, text=True, timeout=600)
    if not os.path.exists(out):
        return {'ok': False, 'errors': None, 'pad_errors': []}
    d = json.load(open(out, encoding='utf-8'))
    pad_errors = []
    for v in d.get('violations') or []:
        if v.get('severity') != 'error' or v.get('excluded'):
            continue
        if v.get('type') in PAD_TYPES and (v.get('items') or []) and all(
                _is_pad(it.get('description') or '') for it in v['items']):
            pad_errors.append(v.get('type'))
    return {'ok': True,
            'errors': sum(1 for v in d.get('violations') or []
                          if v.get('severity') == 'error' and not v.get('excluded')),
            'pad_errors': pad_errors}


def _closest_pair(pads):
    best = None
    for i, a in enumerate(pads):
        pa = a.GetPosition()
        for b in pads[i + 1:]:
            if b.GetNetname() == a.GetNetname():
                continue
            pb = b.GetPosition()
            d = math.hypot(pa.x - pb.x, pa.y - pb.y) / 1e6
            if best is None or d < best[0]:
                best = (d, a, b)
    return best


def _shift(src: str, out: str, fraction: float) -> dict:
    """Move the pad of one net closest to another net's pad into it."""
    import pcbnew
    board = pcbnew.LoadBoard(src)
    pads = [p for fp in board.GetFootprints() for p in fp.Pads()
            if p.GetNetname()]
    if len(pads) < 2:
        return {'ok': False, 'reason': 'fewer than two connected pads'}
    dist, a, b = _closest_pair(pads)
    pa, pb = a.GetPosition(), b.GetPosition()
    step = min(3.0, dist * fraction)          # mm
    scale = step / dist
    a.SetPosition(pcbnew.VECTOR2I(
        int(round(pa.x + (pb.x - pa.x) * scale)),
        int(round(pa.y + (pb.y - pa.y) * scale))))
    pcbnew.SaveBoard(out, board)
    return {'ok': True, 'pad': a.GetNumber(),
            'footprint': a.GetParentFootprint().GetReference(),
            'net': a.GetNetname(), 'toward_net': b.GetNetname(),
            'moved_mm': round(step, 4), 'was_mm': round(dist, 4)}


def verify(board: str, pro: str) -> dict:
    tmp = tempfile.mkdtemp(prefix='ctl_')
    b = os.path.join(tmp, 't.kicad_pcb')
    shutil.copy(board, b)
    shutil.copy(pro, os.path.join(tmp, 't.kicad_pro'))
    return drc(b, os.path.join(tmp, 't.drc.json'))


def build(sources: list, n: int = 8) -> list:
    """Make `n` controls out of clean source boards; returns the index."""
    os.makedirs(CONTROLS, exist_ok=True)
    index = []
    for board in sources:
        if len(index) >= n:
            break
        src_dir = os.path.join(HERE, 'boards', board['dir'])
        src = os.path.join(src_dir, 'circuit.reference.kicad_pcb')
        pro = os.path.join(src_dir, 'circuit.kicad_pro')
        if not (os.path.exists(src) and os.path.exists(pro)):
            continue
        made = None
        # land the pad ON its neighbour: a partial move only violates when
        # the two were already close, and then most boards yield no control
        for fraction in (1.0, 0.97, 1.03):
            tmp = tempfile.mkdtemp(prefix='ctlm_')
            cand = os.path.join(tmp, 'circuit.reference.kicad_pcb')
            info = _shift(src, cand, fraction)
            if not info.get('ok'):
                break
            check = verify(cand, pro)
            if check.get('ok') and check['pad_errors']:
                made = (cand, dict(info, verified=check))
                break
        if not made:
            continue
        name = f'ctl_{len(index):02d}'
        out_dir = os.path.join(CONTROLS, name)
        os.makedirs(out_dir, exist_ok=True)
        dst = os.path.join(out_dir, 'circuit.reference.kicad_pcb')
        shutil.copy(made[0], dst)
        shutil.copy(pro, os.path.join(out_dir, 'circuit.kicad_pro'))
        with open(dst, 'rb') as fh:
            digest = hashlib.sha256(fh.read()).hexdigest()
        index.append(dict(name=name, source=board['dir'], dir=f'../controls/{name}',
                          set='control', sha256=digest, **made[1]))
        print(f"control {name}: {board['dir'][:30]} pad {made[1]['pad']} "
              f"moved {made[1]['moved_mm']} mm -> pad errors "
              f"{made[1]['verified']['pad_errors'][:3]}", flush=True)
    with open(os.path.join(CONTROLS, 'index.json'), 'w', encoding='utf-8') as fh:
        json.dump(index, fh, indent=1)
    return index


def load_or_build(sources: list, n: int = 8) -> list:
    path = os.path.join(CONTROLS, 'index.json')
    if os.path.exists(path):
        try:
            return json.load(open(path, encoding='utf-8'))
        except ValueError:
            pass
    return build(sources, n)


def _sha(path: str):
    try:
        with open(path, 'rb') as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return None


def materialize(index: list) -> list:
    """Expose the sealed controls as ordinary board directories.

    The bench resolves every input as boards/<dir>/circuit.reference.kicad_pcb.
    Addressing the sealed files through a '../controls/x' path instead made
    os.path.join fold the '..' into file names, scattered outputs into a
    nested directory and hung under concurrency.  A verified copy under a
    clearly marked name avoids all of that; the sealed original stays the
    source of truth and the copy is re-made whenever its hash differs.
    """
    out = []
    for c in index:
        name = '__control__' + c['name']
        dst_dir = os.path.join(HERE, 'boards', name)
        src_dir = os.path.join(CONTROLS, c['name'])
        board = os.path.join(dst_dir, 'circuit.reference.kicad_pcb')
        if _sha(board) != c.get('sha256'):
            os.makedirs(dst_dir, exist_ok=True)
            shutil.copy(os.path.join(src_dir, 'circuit.reference.kicad_pcb'), board)
            shutil.copy(os.path.join(src_dir, 'circuit.kicad_pro'),
                        os.path.join(dst_dir, 'circuit.kicad_pro'))
        out.append(dict(c, dir=name, set='control'))
    return out


def integrity(index: list) -> list:
    """Controls whose sealed file no longer matches its hash."""
    bad = []
    for c in index:
        p = os.path.join(HERE, 'controls', c['name'], 'circuit.reference.kicad_pcb')
        try:
            with open(p, 'rb') as fh:
                digest = hashlib.sha256(fh.read()).hexdigest()
        except OSError:
            digest = None
        if digest != c.get('sha256'):
            bad.append(c['name'])
    return bad


if __name__ == '__main__':
    cores = os.path.join(HERE, 'arms', 'gen0', 'coreset.json')
    srcs = []
    if os.path.exists(cores):
        srcs = [b for b in json.load(open(cores, encoding='utf-8'))
                if b.get('coreset_role') == 'guard']
    idx = load_or_build(srcs, 8)
    print(json.dumps({'controls': len(idx), 'tampered': integrity(idx)}))
