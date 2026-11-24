"""Conflict closure and minimal rip-up sets (pure orchestration).

A failing open pair is "sealed" when every candidate path is blocked.
The question a counterfactual needs answered is: WHICH copper blocks it,
and what is the smallest set of it whose removal opens a legal path?

    conflict_closure(...)   greedy minimal rip-up set, each step removing
                            the foreign track nearest the reported blocker
                            and re-planning against the remaining copper
    probe_removal(...)      EXECUTE the removal on the real board and
                            verify a path appears: the executed
                            counterfactual an error model may store

Everything here reuses geomir's planner and verifier: one geometric truth.
"""
from __future__ import annotations

import math


def _same_seg(seg, net, s, e, tol=0.002):
    """Is `seg` (from extract_objects) the track with these endpoints?"""
    sn, (ax, ay), (bx, by), _hw = seg
    if sn != net:
        return False
    fwd = (math.hypot(ax - s[0], ay - s[1]) < tol
           and math.hypot(bx - e[0], by - e[1]) < tol)
    rev = (math.hypot(ax - e[0], ay - e[1]) < tol
           and math.hypot(bx - s[0], by - s[1]) < tol)
    return fwd or rev


def without(segs, rects, removed):
    """Obstacle lists minus the removed tracks [(net, s, e)]."""
    keep = []
    for li, lst in enumerate(segs):
        keep.append([g for g in lst if not any(
            _same_seg(g, nc, s, e) for (nc, s, e) in removed)])
    return keep, rects


def nearest_track(board, layer_id, at, nc, radius=1.5, want_net=None):
    """The foreign track nearest to a point on a layer, within `radius`.

    geomir's blocker carries the netcode and a point on the blocker's
    MIDPOINT, so the distance is point-to-segment, not point-to-endpoint
    (by endpoint the 0.55 mm picks a neighbouring track on another net).
    `want_net` (the blocker's netcode) breaks ties in the blocker's favour.
    """
    best = None
    for t in board.GetTracks():
        try:
            tnc = t.GetNetCode()
            if tnc == nc or t.Type() == pcbnew_via_t():
                continue
            if t.GetLayer() != layer_id and not t.IsOnLayer(layer_id):
                continue
            s, e = t.GetStart(), t.GetEnd()
            d = _pt_seg(at, (s.x / 1e6, s.y / 1e6), (e.x / 1e6, e.y / 1e6))
            # prefer the net the verifier reported, then distance
            key = (0 if (want_net is not None and tnc == want_net) else 1, d)
            if d < radius and (best is None or key < best[0]):
                best = (key, t)
        except Exception:
            continue
    return best[1] if best else None


def _pt_seg(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    l2 = dx * dx + dy * dy
    if l2 == 0:
        return math.hypot(p[0] - a[0], p[1] - a[1])
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


def pcbnew_via_t():
    import pcbnew
    return pcbnew.PCB_VIA_T


def conflict_closure(geomir, board, a, b, nc, layer, layers, segs, rects,
                     width, rules, max_set=4, bounds=None):
    """Greedily grow the rip-up set until a legal path exists.

    Returns a typed result:
      {'status': 'closed', 'set': [...], 'plan': pts}   a path exists once
             'precondition_failed'                        no set <= max_set
             'error'
    The set is greedy, not provably minimal; it is an upper bound on the
    true minimum, and it is EXECUTED (see probe_removal) before any error
    model may cite it.
    """
    try:
        removed, tried, plan = [], 0, None
        cur_segs, cur_rects = segs, rects
        while len(removed) <= max_set:
            plans, rejects = geomir.plan_paths(
                a, b, width, nc, cur_segs, cur_rects, bounds=bounds,
                width_ladder=(width,), rules=rules, planes_by_layer=None)
            same = [pl for pl in plans
                    if int(pl['pts'][0][2]) == layer
                    and int(pl['pts'][-1][2]) == layer]
            if same:
                return {'status': 'closed', 'set': removed, 'plan': same[0],
                        'rejected': rejects[:3]}
            if not rejects:
                return {'status': 'precondition_failed', 'set': removed,
                        'why': 'no candidate polylines at all'}
            blk = (rejects[0].get('blocked_by') or {})
            li = layer
            t = nearest_track(board, layers[li], blk.get('at') or a, nc,
                              want_net=blk.get('net'))
            if t is None or any(t.m_Uuid.AsString() == u for u, _, _ in removed):
                return {'status': 'precondition_failed', 'set': removed,
                        'why': f'blocker not resolvable to a track: {blk}'}
            s, e = t.GetStart(), t.GetEnd()
            item = (t.m_Uuid.AsString(), t.GetNetCode(),
                    ((s.x / 1e6, s.y / 1e6), (e.x / 1e6, e.y / 1e6)))
            removed.append(item)
            cur_segs, cur_rects = without(segs, rects,
                                          [(n, p, q) for _, n, (p, q) in removed])
            tried += 1
        return {'status': 'precondition_failed', 'set': removed,
                'why': f'no path within {max_set} rip-ups'}
    except Exception as e:
        return {'status': 'error', 'why': f'{type(e).__name__}: {e}'}


def probe_removal(geomir, board, a, b, nc, layer, layers, width, rules,
                  rip_set, bounds=None):
    """EXECUTE the removal on a copy and confirm a path appears.

    Returns the executed counterfactual record an error model may store:
      {'executed': True, 'opened': bool, 'set_size': n, 'plan': pts|None,
       'blocker_uuids': [...]}
    """
    removed_items = []
    for u, net, (p, q) in rip_set:
        for t in board.GetTracks():
            if t.m_Uuid.AsString() == u:
                removed_items.append(t)
                break
    for t in removed_items:
        board.Remove(t)
    try:
        segs, rects = geomir.extract_objects(board, layers)
        plans, rejects = geomir.plan_paths(
            a, b, width, nc, segs, rects, bounds=bounds,
            width_ladder=(width,), rules=rules, planes_by_layer=None)
        same = [pl for pl in plans
                if int(pl['pts'][0][2]) == layer
                and int(pl['pts'][-1][2]) == layer]
        return {'executed': True, 'opened': bool(same),
                'set_size': len(rip_set),
                'plan': (same[0]['pts'] if same else None),
                'blocker_uuids': [u for u, _, _ in rip_set]}
    except Exception as e:
        return {'executed': True, 'opened': False,
                'why': f'{type(e).__name__}: {e}'}
    finally:
        for t in removed_items:
            board.Add(t)
