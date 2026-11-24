#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Targeted Rule-Spec probes for rules the random mutation matrix cannot cross.

One case per subprocess (pcbnew's SWIG state does not survive repeated
LoadBoard calls).  Each probe moves exactly one object just past its rule
and records whether DRC fires the mapped type.

Usage:
  python work/pcbworld/pair_probe.py --boards 8
  python work/pcbworld/pair_probe.py --one <dir> <rule>
"""
import argparse
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

import pcbnew  # noqa: E402

from fluxpcb.repair_drc import save_preserving_project  # noqa: E402

KICAD_CLI = os.environ.get('KICAD_CLI', 'kicad-cli')
RULES = ('hole_to_hole', 'copper_edge_clearance')


def _drc(board):
    out = board + '.probe.json'
    subprocess.run([KICAD_CLI, 'pcb', 'drc', '--format', 'json',
                    '--severity-all', '--refill-zones', '-o', out, board],
                   capture_output=True, text=True, timeout=600)
    if not os.path.exists(out):
        return {}
    d = json.load(open(out, encoding='utf-8'))
    types = {}
    for v in d.get('violations', []):
        if not v.get('excluded'):
            types[v.get('type', '?')] = types.get(v.get('type', '?'), 0) + 1
    return types


def _thresholds(pro_path):
    try:
        d = json.load(open(pro_path, encoding='utf-8'))
        r = d.get('board', {}).get('design_settings', {}).get('rules', {})
        return float(r.get('min_hole_to_hole') or 0), float(
            r.get('min_copper_edge_clearance') or 0)
    except Exception:
        return 0.0, 0.0


def _probe_h2h(board, min_h2h):
    pads = []
    for fp in board.GetFootprints():
        for p in fp.Pads():
            if p.GetNetCode() <= 0:
                continue
            try:
                d = p.GetDrillSize()
                drill = max(d.x, d.y) / 1e6
            except Exception:
                drill = 0.0
            if drill > 0:
                pads.append((p, drill))
    best = None
    for i, (a, da) in enumerate(pads):
        pa = a.GetPosition()
        for b, db in pads[i + 1:]:
            if a.GetNetCode() == b.GetNetCode():
                continue
            pb = b.GetPosition()
            gap = math.hypot(pa.x - pb.x, pa.y - pb.y) / 1e6 - (da + db) / 2
            if best is None or gap < best[0]:
                best = (gap, a, b)
    if best is None:
        return None
    gap, a, b = best
    limit = min_h2h or 0.25
    if gap <= limit:
        return None  # already violated
    move = gap - limit + 0.02
    pa, pb = a.GetPosition(), b.GetPosition()
    L = math.hypot(pa.x - pb.x, pa.y - pb.y) or 1
    dx, dy = (pb.x - pa.x) / L * move * 1e6, (pb.y - pa.y) / L * move * 1e6
    a.SetPosition(pcbnew.VECTOR2I(int(pa.x + dx), int(pa.y + dy)))
    return {'gap_before_mm': round(gap, 4), 'move_mm': round(move, 4),
            'limit_mm': limit}


def _probe_edge(board, min_edge):
    bbox = board.GetBoardEdgesBoundingBox()
    if bbox.GetWidth() <= 0:
        return None
    best = None
    for t in board.GetTracks():
        if t.Type() != pcbnew.PCB_TRACE_T:
            continue
        s, e = t.GetStart(), t.GetEnd()
        for (x, y, d) in (
                (s.x / 1e6, s.y / 1e6, (s.x - bbox.GetLeft()) / 1e6),
                (s.x / 1e6, s.y / 1e6, (bbox.GetRight() - s.x) / 1e6),
                (s.x / 1e6, s.y / 1e6, (s.y - bbox.GetTop()) / 1e6),
                (s.x / 1e6, s.y / 1e6, (bbox.GetBottom() - s.y) / 1e6)):
            gap = d - t.GetWidth() / 2e6
            if best is None or gap < best[0]:
                best = (gap, t, x, y, d)
    if best is None:
        return None
    gap, t, x, y, d = best
    limit = min_edge or 0.3
    if gap <= limit:
        return None
    move = gap - limit + 0.02
    # move perpendicular to the nearest edge
    left = (x - bbox.GetLeft() / 1e6)
    right = (bbox.GetRight() / 1e6 - x)
    top = (y - bbox.GetTop() / 1e6)
    bottom = (bbox.GetBottom() / 1e6 - y)
    m = min(left, right, top, bottom)
    dx = dy = 0.0
    if m == left:
        dx = -move
    elif m == right:
        dx = move
    elif m == top:
        dy = -move
    else:
        dy = move
    s, e = t.GetStart(), t.GetEnd()
    t.SetStart(pcbnew.VECTOR2I(int(s.x + dx * 1e6), int(s.y + dy * 1e6)))
    t.SetEnd(pcbnew.VECTOR2I(int(e.x + dx * 1e6), int(e.y + dy * 1e6)))
    return {'gap_before_mm': round(gap, 4), 'move_mm': round(move, 4),
            'limit_mm': limit}


def one(d, rule):
    src = os.path.join(HERE, 'runs', d, f'{d}.kicad_pcb')
    if not os.path.exists(src):
        return {'dir': d, 'rule': rule, 'status': 'not_routed'}
    pro_src = os.path.join(HERE, 'boards', d, 'circuit.kicad_pro')
    tmp = tempfile.mkdtemp(prefix='probe_')
    b = os.path.join(tmp, 'b.kicad_pcb')
    pro = os.path.join(tmp, 'b.kicad_pro')
    shutil.copy(src, b)
    # The probe needs a rule that actually exists in the judge's table:
    # most boards ship min_edge=0 and min_h2h may be 0.  Tighten only the
    # rule under test, in a copy of the project.
    pro_data = json.load(open(pro_src, encoding='utf-8'))
    ds = pro_data.setdefault('board', {}).setdefault('design_settings', {})
    rules = ds.setdefault('rules', {})
    sev = ds.setdefault('rule_severities', {})
    if rule == 'hole_to_hole':
        if not rules.get('min_hole_to_hole'):
            rules['min_hole_to_hole'] = 0.25
        sev['hole_to_hole'] = 'error'
    else:
        rules['min_copper_edge_clearance'] = 0.3
        sev['copper_edge_clearance'] = 'error'
    json.dump(pro_data, open(pro, 'w', encoding='utf-8'), indent=1)
    min_h2h = float(rules.get('min_hole_to_hole') or 0)
    min_edge = float(rules.get('min_copper_edge_clearance') or 0)
    board = pcbnew.LoadBoard(b)
    info = (_probe_h2h(board, min_h2h) if rule == 'hole_to_hole'
            else _probe_edge(board, min_edge))
    out = {'dir': d, 'rule': rule}
    if info is None:
        out['status'] = 'not_applicable'
    else:
        save_preserving_project(board, b)
        types = _drc(b)
        out.update(info)
        out['types'] = types
        out['killed'] = bool(types.get(rule)) or (
            rule == 'copper_edge_clearance'
            and bool(types.get('edge_clearance')))
        out['status'] = 'ok'
    print('__PROBE__' + json.dumps(out), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--one', nargs=2, default=None, metavar=('DIR', 'RULE'))
    ap.add_argument('--boards', type=int, default=8)
    args = ap.parse_args()
    if args.one:
        one(*args.one)
        return
    boards = json.load(open(os.path.join(HERE, 'boards_full.json')))
    have = [b['dir'] for b in boards
            if os.path.exists(os.path.join(HERE, 'runs', b['dir'],
                                           f"{b['dir']}.kicad_pcb"))]
    have = have[:args.boards]
    out_path = os.path.join(HERE, 'pair_probe.jsonl')
    with open(out_path, 'w', encoding='utf-8') as f:
        for d in have:
            for rule in RULES:
                r = subprocess.run(
                    [sys.executable, os.path.abspath(__file__),
                     '--one', d, rule], capture_output=True, text=True,
                    timeout=600)
                row = None
                for line in (r.stdout or '').splitlines():
                    if line.startswith('__PROBE__'):
                        row = json.loads(line[len('__PROBE__'):])
                if row is None:
                    row = {'dir': d, 'rule': rule, 'status': 'error',
                           'error': (r.stderr or '')[-200:]}
                f.write(json.dumps(row) + '\n')
                f.flush()
    rows = [json.loads(l) for l in open(out_path, encoding='utf-8')]
    s = {}
    for rule in RULES:
        sub = [r for r in rows if r['rule'] == rule and r.get('status') == 'ok']
        s[rule] = {'ok': len(sub),
                   'killed': sum(1 for r in sub if r.get('killed')),
                   'MS': round(sum(1 for r in sub if r.get('killed')) /
                               max(1, len(sub)), 3)}
    json.dump(s, open(os.path.join(HERE, 'pair_probe_summary.json'), 'w'),
              indent=1)
    print(json.dumps(s, indent=1))


if __name__ == '__main__':
    main()
