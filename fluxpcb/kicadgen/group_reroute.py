"""Group reroute — resolving a deadlock single-net rip-up cannot.

PathFinder's "second-order congestion": a net that is itself unblocked
may still have to move so another net can pass.  Single-net rip-up fails
here because the blocking net, once ripped, finds no path either.

The tool:
  1. computes the conflict closure (kicadgen/conflict.py): the greedy
     smallest set of foreign tracks whose removal opens a legal path;
  2. rips that set and routes the target through the freed corridor;
  3. reroutes every ripped connection, trying several net ORDERS
     (FCCM'25: changing order/topology is what resolves these, not cost
     tuning);
  4. keeps the result only if every new segment passes the exact
     verifier; otherwise restores the ripped tracks with their original
     geometry.  Copper outside the conflict set is never touched.

Statuses are typed: ok / precondition_failed / infeasible / error.
"""
from __future__ import annotations

import random

import pcbnew

from . import geomir, routegrid as rg
from .conflict import conflict_closure, without


def _route(board, a, b, nc, layer, layers, width, rt, bounds):
    """A verified same-layer plan for one connection, or None."""
    segs, rects = geomir.extract_objects(board, layers)
    try:
        plans, _ = geomir.plan_paths(
            a, b, width, nc, segs, rects, bounds=bounds,
            width_ladder=(width,), rules=rt, planes_by_layer=None)
    except Exception:
        return None
    same = [pl for pl in plans
            if int(pl['pts'][0][2]) == layer
            and int(pl['pts'][-1][2]) == layer]
    return same[0]['pts'] if same else None


def _draw(board, pts, layers, w_iu, nc, made):
    """Append tracks for a same-layer plan; False on a layer change."""
    for p, q in zip(pts, pts[1:]):
        if int(p[2]) != int(q[2]):
            return False                 # this tool is single-layer
        li = int(p[2])
        t = pcbnew.PCB_TRACK(board)
        t.SetStart(pcbnew.VECTOR2I(int(round(p[0] * 1e6)),
                                   int(round(p[1] * 1e6))))
        t.SetEnd(pcbnew.VECTOR2I(int(round(q[0] * 1e6)),
                                 int(round(q[1] * 1e6))))
        t.SetWidth(w_iu)
        t.SetLayer(layers[li])
        t.SetNetCode(nc)
        board.Add(t)
        made.append(t)
    return True


def _verified(board, made, layers, rt):
    """Exact verification of every new segment against all other copper."""
    segs, rects = geomir.extract_objects(board, layers)
    for t in made:
        s, e = t.GetStart(), t.GetEnd()
        li = layers.index(t.GetLayer())
        nc = t.GetNetCode()
        # the board now contains every new track: drop the ones being
        # judged (same net AND same geometry) so a track never blocks
        # itself or a sibling segment of the same new path
        mine = {(x.GetStart().x, x.GetStart().y, x.GetEnd().x, x.GetEnd().y)
                for x in made if x.GetNetCode() == nc}

        def is_new(g):
            gn, ga, gb, _hw = g
            key = (int(round(ga[0] * 1e6)), int(round(ga[1] * 1e6)),
                   int(round(gb[0] * 1e6)), int(round(gb[1] * 1e6)))
            keyr = (key[2], key[3], key[0], key[1])
            return gn == nc and (key in mine or keyr in mine)

        segs_wo = [[g for g in segs[k] if not (k == li and is_new(g))]
                   for k in range(len(segs))]
        vok, blk = geomir.verify_polyline(
            [(s.x / 1e6, s.y / 1e6), (e.x / 1e6, e.y / 1e6)],
            t.GetWidth() / 1e6, nc, segs_wo[li], rects[li], rt)
        if not vok:
            return False, blk
    return True, None


def group_reroute(board, a, b, net_name, layer, trials=40, max_set=4,
                  seed=7, bounds=None):
    """Rip the conflict set, route the target, reroute the set as a group."""
    from pathlib import Path

    from ..kernel.rules import load_rules

    try:
        rt = load_rules(Path(board.GetFileName()))
        layers = rg.signal_layer_ids(board)
        net = board.FindNet(net_name)
        if net is None:
            return {'status': 'error', 'why': f'net {net_name} missing'}
        nc = net.GetNetCode()
        width = max(float(rt.get('track_width') or 0.0),
                    float(rt.get('min_track_width') or 0.0), 0.0)
        w_iu = int(round(width * 1e6))
        segs, rects = geomir.extract_objects(board, layers)
        cc = conflict_closure(geomir, board, a, b, nc, layer, layers, segs,
                              rects, width, rt, max_set=max_set,
                              bounds=bounds)
        if cc['status'] != 'closed':
            # typed precondition failure: more rip-ups than allowed, or a
            # blocker that is not a track (a pad: topology change needed)
            return {'status': 'precondition_failed',
                    'set_size': len(cc.get('set') or []),
                    'why': cc.get('why') or 'conflict set does not open a path'}
        rip = cc['set']
        by_uuid = {t.m_Uuid.AsString(): t for t in board.GetTracks()}
        victims = []
        for u, vnc, (p, q) in rip:
            t = by_uuid.get(u)
            if t is None:
                return {'status': 'error', 'why': f'ripped track {u} gone'}
            victims.append({'uuid': u, 'net': vnc, 'a': p, 'b': q,
                            'layer': layers.index(t.GetLayer()),
                            'width': t.GetWidth() / 1e6,
                            'orig': t})
        rng = random.Random(seed)
        for trial in range(trials):
            for v in victims:
                board.Remove(v['orig'])
            made, ok = [], True
            target_pts = _route(board, a, b, nc, layer, layers, width, rt,
                               bounds)
            if target_pts is None:
                ok = False
            else:
                ok = _draw(board, target_pts, layers, w_iu, nc, made)
                order = list(victims)
                rng.shuffle(order)
                for v in order:
                    vw_iu = int(round(max(v['width'], width) * 1e6))
                    pts = _route(board, v['a'], v['b'], v['net'], v['layer'],
                                 layers, max(v['width'], width), rt, bounds)
                    if pts is None:
                        ok = False
                        break
                    if not _draw(board, pts, layers, vw_iu, v['net'], made):
                        ok = False
                        break
            if ok:
                ok, blk = _verified(board, made, layers, rt)
            if ok:
                return {'status': 'ok', 'set_size': len(rip),
                        'trial': trial, 'added': len(made), 'width': width}
            # restore: new copper out, ripped tracks back with their
            # original geometry
            for t in made:
                board.Remove(t)
            for v in victims:
                o = v['orig']
                t = pcbnew.PCB_TRACK(board)
                t.SetStart(o.GetStart())
                t.SetEnd(o.GetEnd())
                t.SetWidth(o.GetWidth())
                t.SetLayer(o.GetLayer())
                t.SetNetCode(v['net'])
                board.Add(t)
                v['orig'] = t
        return {'status': 'infeasible', 'set_size': len(rip),
                'trials': trials}
    except Exception as e:
        return {'status': 'error', 'why': f'{type(e).__name__}: {e}'}
