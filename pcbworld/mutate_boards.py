#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Mutation matrix: does the composed gate set detect the failure class?

For each (board, operator, delta) apply one minimal fault and run the same
KiCad DRC + connectivity check the pipeline uses.  A mutant is "killed" if
the gate set reports an error or an open.  Per-class mutation score is
killed / generated; survivors are either equivalent or a blind spot.

Operators:
  thin       reduce one track width by delta          -> min width
  move       translate one track toward a neighbour   -> clearance
  delete     remove one track segment                 -> connectivity
  short      add a track between two different nets   -> short
  shift_pad  move one pad by delta                    -> connectivity/clearance
  via_drill  increase one via drill by delta          -> annular/hole (often
                                                         out of model)

Usage:
  python work/pcbworld/mutate_boards.py --max-new 40
"""
import argparse
import json
import math
import os
import random
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'scripts'))
KICAD_CLI = os.environ.get('KICAD_CLI', 'kicad-cli')

import pcbnew  # noqa: E402

sys.path.insert(0, os.path.join(HERE, '..', '..', '..', 'flux-neurotransmitter'))
from fluxpcb.repair_drc import save_preserving_project  # noqa: E402

DELTAS = [0.010, 0.060, 0.120]  # mm
OPS = ['thin', 'move', 'delete', 'short', 'shift_pad', 'via_drill']


def load_boards():
    boards = json.load(open(os.path.join(HERE, 'boards_full.json')))
    have = []
    for b in boards:
        p = os.path.join(HERE, 'runs', b['dir'], f"{b['dir']}.kicad_pcb")
        if os.path.exists(p):
            have.append(b)
    # stratified: 4 d3a, 2 d3b, 2 d3c
    out = []
    for s, n in (('d3a', 4), ('d3b', 2), ('d3c', 2)):
        out += [b for b in have if b['set'] == s][:n]
    return out


def tracks(board):
    return [t for t in board.GetTracks() if t.Type() == pcbnew.PCB_TRACE_T]


def vias(board):
    return [t for t in board.GetTracks() if t.Type() == pcbnew.PCB_VIA_T]


def min_width_mm(board):
    try:
        return board.GetDesignSettings().m_TrackMinWidth / 1e6
    except Exception:
        return 0.2


def rule_thresholds(board, pro_path=None):
    ds = board.GetDesignSettings()
    clr = 0.0
    if pro_path and os.path.exists(pro_path):
        try:
            pro = json.load(open(pro_path, encoding='utf-8'))
            clr = max([float(c.get('clearance') or 0)
                       for c in pro.get('net_settings', {}).get('classes', [])] or [0.0])
        except Exception:
            pass
    return {'min_width': ds.m_TrackMinWidth / 1e6,
            'min_clearance': clr or ds.m_MinClearance / 1e6,
            'min_annular': ds.m_ViasMinAnnularWidth / 1e6,
            'min_hole_clearance': ds.m_HoleClearance / 1e6,
            'min_hole_to_hole': ds.m_HoleToHoleMin / 1e6,
            'min_edge': ds.m_CopperEdgeClearance / 1e6}


def seg_dist(a, b, c, d):
    # min distance between segments ab and cd (2D)
    def pt_seg(px, py, x1, y1, x2, y2):
        dx, dy = x2 - x1, y2 - y1
        L2 = dx * dx + dy * dy
        if L2 == 0:
            return math.hypot(px - x1, py - y1)
        t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / L2))
        return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))
    return min(pt_seg(a[0], a[1], c[0], c[1], d[0], d[1]),
               pt_seg(c[0], c[1], a[0], a[1], b[0], b[1]),
               pt_seg(a[0], a[1], c[0], c[1], d[0], d[1]),
               pt_seg(b[0], b[1], c[0], c[1], d[0], d[1]))


def mutate(board, op, delta, rng):
    """Apply one minimal fault; return a description or None if not applicable."""
    if op == 'thin':
        # Pick tracks wide enough that the decrement is positive; the
        # mutation is allowed to cross the DRC minimum (that is the test).
        ts = [t for t in tracks(board) if t.GetWidth() / 1e6 > delta + 1e-6]
        if not ts:
            return None
        # mutate near the rule boundary: pick among the narrowest decile
        ts.sort(key=lambda x: x.GetWidth())
        t = rng.choice(ts[:max(1, len(ts) // 10)])
        w = t.GetWidth()
        t.SetWidth(max(1000, int(w - delta * 1e6)))
        return {'op': op, 'delta': delta, 'from_mm': w / 1e6,
                'to_mm': t.GetWidth() / 1e6}
    if op == 'move':
        ts = tracks(board)
        if len(ts) < 2:
            return None
        def endpoints(o):
            s0, e0 = o.GetStart(), o.GetEnd()
            return (s0.x / 1e6, s0.y / 1e6, e0.x / 1e6, e0.y / 1e6)
        # pick the track whose nearest other-net neighbour is closest
        best = None
        for cand in ts:
            c = endpoints(cand)
            for o in ts:
                if o is cand or o.GetNetCode() == cand.GetNetCode():
                    continue
                if o.GetLayer() != cand.GetLayer():
                    continue
                oe = endpoints(o)
                d = seg_dist((c[0], c[1]), (c[2], c[3]),
                             (oe[0], oe[1]), (oe[2], oe[3]))
                if best is None or d < best[0]:
                    best = (d, cand, o)
        if best is None:
            return None
        t = best[1]
        _, _, o = best
        mid = t.GetStart() + t.GetEnd()
        mx, my = mid.x / 2e6, mid.y / 2e6
        ox, oy = (o.GetStart().x + o.GetEnd().x) / 2e6, (o.GetStart().y + o.GetEnd().y) / 2e6
        dx, dy = ox - mx, oy - my
        L = math.hypot(dx, dy) or 1.0
        sx, sy = dx / L * delta * 1e6, dy / L * delta * 1e6
        t.SetStart(pcbnew.VECTOR2I(int(t.GetStart().x + sx), int(t.GetStart().y + sy)))
        t.SetEnd(pcbnew.VECTOR2I(int(t.GetEnd().x + sx), int(t.GetEnd().y + sy)))
        s1, e1 = t.GetStart(), t.GetEnd()
        best_after = 9e9
        for o in ts:
            if o is t or o.GetNetCode() == t.GetNetCode() or o.GetLayer() != t.GetLayer():
                continue
            os_, oe = o.GetStart(), o.GetEnd()
            d = seg_dist((s1.x / 1e6, s1.y / 1e6), (e1.x / 1e6, e1.y / 1e6),
                         (os_.x / 1e6, os_.y / 1e6), (oe.x / 1e6, oe.y / 1e6))
            best_after = min(best_after, d - (t.GetWidth() + o.GetWidth()) / 2e6)
        return {'op': op, 'delta': delta,
                'clearance_after_mm': round(best_after, 4)}
    if op == 'delete':
        ts = tracks(board)
        if not ts:
            return None
        t = rng.choice(ts)
        board.Remove(t)
        return {'op': op, 'delta': None}
    if op == 'short':
        pads = [p for fp in board.GetFootprints() for p in fp.Pads()
                if p.GetNetCode() > 0 and p.IsOnLayer(pcbnew.F_Cu)]
        rng.shuffle(pads)
        for i in range(min(30, len(pads))):
            a = pads[i]
            for b in pads[i + 1:i + 12]:
                if a.GetNetCode() == b.GetNetCode():
                    continue
                d = math.hypot((a.GetPosition().x - b.GetPosition().x) / 1e6,
                               (a.GetPosition().y - b.GetPosition().y) / 1e6)
                if d > 6.0:
                    continue
                t = pcbnew.PCB_TRACK(board)
                t.SetStart(a.GetPosition())
                t.SetEnd(b.GetPosition())
                t.SetWidth(int(min_width_mm(board) * 1e6))
                t.SetLayer(pcbnew.F_Cu)
                board.Add(t)
                return {'op': op, 'delta': None, 'nets': [a.GetNetname(), b.GetNetname()],
                        'dist_mm': round(d, 2)}
        return None
    if op == 'shift_pad':
        pads = [p for fp in board.GetFootprints() for p in fp.Pads()
                if p.GetNetCode() > 0]
        if not pads:
            return None
        def drill(p):
            try:
                d = p.GetDrillSize()
                return max(d.x, d.y) / 1e6
            except Exception:
                return 0.0
        # pick the PTH pad whose nearest other-net pad is closest
        best = None
        for cand in pads:
            if drill(cand) <= 0:
                continue
            c = cand.GetPosition()
            for o in pads:
                if o is cand or o.GetNetCode() == cand.GetNetCode():
                    continue
                if drill(o) <= 0:
                    continue
                q = o.GetPosition()
                d = math.hypot(c.x - q.x, c.y - q.y) / 1e6
                if best is None or d < best[0]:
                    best = (d, cand, o)
        if best is None:
            return None
        _, p, o = best
        q = o.GetPosition()
        pos = p.GetPosition()
        p.SetPosition(pcbnew.VECTOR2I(int(pos.x + delta * 1e6),
                                      int(pos.y + delta * 1e6)))
        after = math.hypot(p.GetPosition().x - q.x, p.GetPosition().y - q.y) / 1e6
        hole_gap = after - (drill(p) + drill(o)) / 2.0
        return {'op': op, 'delta': delta,
                'hole_to_hole_after_mm': round(hole_gap, 4)}
    if op == 'via_drill':
        vs = vias(board)
        if not vs:
            return None
        def annulus(x):
            try:
                return (x.GetWidth(pcbnew.F_Cu) - x.GetDrill()) / 2e6
            except Exception:
                return 1e9
        vs.sort(key=annulus)
        v = vs[0]
        d0 = v.GetDrill()
        v.SetDrill(int(d0 + delta * 1e6))
        try:
            ann = (v.GetWidth(pcbnew.F_Cu) - v.GetDrill()) / 2e6
        except Exception:
            ann = None
        return {'op': op, 'delta': delta, 'from_mm': d0 / 1e6,
                'to_mm': v.GetDrill() / 1e6,
                'annular_after_mm': None if ann is None else round(ann, 4)}
    return None


def drc_counts(pcb):
    rep = pcb + '.mut.drc.json'
    subprocess.run([KICAD_CLI, 'pcb', 'drc', '--format', 'json',
                    '--severity-error', '--refill-zones', '-o', rep, pcb],
                   capture_output=True, text=True, timeout=600)
    if not os.path.exists(rep):
        return None
    d = json.load(open(rep, encoding='utf-8'))
    by = {}
    for v in d.get('violations', []):
        if v.get('severity') == 'error' and not v.get('excluded'):
            by[v.get('type', '?')] = by.get(v.get('type', '?'), 0) + 1
    un = len([u for u in d.get('unconnected_items', []) if not u.get('excluded')])
    return {'errors': sum(by.values()), 'types': by, 'unconnected': un}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--max-new', type=int, default=0)
    ap.add_argument('--seed', type=int, default=7)
    ap.add_argument('--ops', default='',
                    help='comma-separated operator filter')
    ap.add_argument('--one', nargs=3, default=None,
                    metavar=('DIR', 'OP', 'DELTA'))
    args = ap.parse_args()

    if args.one:
        d, op, delta = args.one
        delta = None if delta == 'None' else float(delta)
        src = os.path.join(HERE, 'runs', d, f'{d}.kicad_pcb')
        pro = os.path.join(HERE, 'boards', d, 'circuit.kicad_pro')
        tmp = tempfile.mkdtemp(prefix='mut1_')
        p = os.path.join(tmp, 'm.kicad_pcb')
        shutil.copy(src, p)
        shutil.copy(pro, os.path.join(tmp, 'm.kicad_pro'))
        row = {'dir': d, 'op': op, 'delta': delta}
        try:
            board = pcbnew.LoadBoard(p)
            th = rule_thresholds(board, pro)
            row['rules'] = th
            info = mutate(board, op, delta, random.Random(args.seed))
            if info is None:
                row['status'] = 'not_applicable'
            else:
                save_preserving_project(board, p)
                shutil.copy(pro, os.path.join(tmp, 'm.kicad_pro'))
                counts = drc_counts(p)
                row.update(info)
                row['drc'] = counts
                row['killed'] = bool(counts and (counts['errors'] > 0
                                                 or counts['unconnected'] > 0))
                if op == 'thin':
                    row['crossed'] = info['to_mm'] < th['min_width']
                elif op == 'move':
                    row['crossed'] = (info.get('clearance_after_mm', 9e9)
                                      < th['min_clearance'])
                elif op == 'via_drill':
                    row['crossed'] = (th['min_annular'] > 0
                                      and (info.get('annular_after_mm') or 9e9)
                                      < th['min_annular'])
                elif op == 'shift_pad':
                    row['crossed'] = (
                        (th.get('min_hole_to_hole', 0) > 0
                         and (info.get('hole_to_hole_after_mm') or 9e9)
                         < th['min_hole_to_hole']))
                row['status'] = 'ok'
        except Exception as e:
            row['status'] = 'error'
            row['error'] = str(e)[:200]
        print('__MUT__' + json.dumps(row), flush=True)
        return
    out_path = os.path.join(HERE, 'mutation_results.jsonl')
    done = set()
    if os.path.exists(out_path):
        for line in open(out_path, encoding='utf-8'):
            try:
                r = json.loads(line)
                done.add((r['dir'], r['op'], r['delta']))
            except Exception:
                pass
    boards = load_boards()
    rng = random.Random(args.seed)
    ops = [o for o in (args.ops.split(',') if args.ops else OPS) if o in OPS]
    todo = [(b, op, d) for b in boards for op in ops for d in
            ([None] if op in ('delete', 'short') else DELTAS)]
    todo = [x for x in todo if (x[0]['dir'], x[1], x[2]) not in done]
    if args.max_new:
        todo = todo[:args.max_new]
    print(f'boards={len(boards)} mutants to run={len(todo)}', flush=True)

    with open(out_path, 'a', encoding='utf-8') as out:
        for i, (b, op, delta) in enumerate(todo, 1):
            d = b['dir']
            cmd = [sys.executable, os.path.abspath(__file__), '--one', d, op,
                   'None' if delta is None else str(delta), '--seed', str(args.seed)]
            r = subprocess.run(cmd, capture_output=True, text=True,
                               timeout=900)
            row = None
            for line in (r.stdout or '').splitlines():
                if line.startswith('__MUT__'):
                    row = json.loads(line[len('__MUT__'):])
            if row is None:
                row = {'dir': d, 'set': b['set'], 'op': op, 'delta': delta,
                       'status': 'error',
                       'error': (r.stderr or '')[-200:]}
            else:
                row['set'] = b['set']
            out.write(json.dumps(row) + chr(10))
            out.flush()
            if i % 10 == 0:
                print(f'  {i}/{len(todo)}', flush=True)
    print('done', flush=True)


if __name__ == '__main__':
    main()
