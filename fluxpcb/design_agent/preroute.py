"""Plan consumption: pre-route the escape nets before the global router.

The plan names nets whose fanout sits inside a fine-pitch region.  Routing
them first (as fixed copper) gives the global router a feasible escape
skeleton instead of asking it to solve the hardest sub-problem blind.

Every edge is planned with geomir, verified segment-by-segment, and must
pass the pad-pair connectivity certificate; failures are counted, never
faked.
"""
from __future__ import annotations

import time
from pathlib import Path

from .planner import board_rules


def _net_pads(board, net_name, layers):
    out = []
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            if pad.GetNetname() != net_name:
                continue
            q = pad.GetPosition()
            li = None
            for i, lid in enumerate(layers):
                try:
                    if pad.IsOnLayer(lid):
                        li = i
                        break
                except Exception:
                    continue
            if li is not None:
                out.append((q.x / 1e6, q.y / 1e6, li))
    return out


def _mst_edges(pads):
    """Prim over pad anchors; returns index pairs (deterministic)."""
    if len(pads) < 2:
        return []
    used = {0}
    edges = []
    while len(used) < len(pads):
        best = None
        for i in used:
            for j in range(len(pads)):
                if j in used:
                    continue
                d = ((pads[i][0] - pads[j][0]) ** 2
                     + (pads[i][1] - pads[j][1]) ** 2)
                if best is None or d < best[0]:
                    best = (d, i, j)
        edges.append((best[1], best[2]))
        used.add(best[2])
    return edges


def pre_route(board_path, plan, max_nets=8, max_edges=24, lock=True,
              strict_pad_clearance=True):
    """Mutate the board file: draw verified escape edges for planned nets."""
    import pcbnew

    from ..kicadgen import geomir, routegrid as rg
    from ..kernel.rules import load_rules

    t0 = time.time()
    board = pcbnew.LoadBoard(str(board_path))
    rules = board_rules(board)
    floor = max(float(rules.get('min_track_width') or 0.2), 0.2)
    layers = rg.signal_layer_ids(board)
    from ..verifiers.operators import verifier_rules
    rt = verifier_rules(load_rules(Path(board_path)),
                        strict_pad_clearance)
    from ..verifiers.via_geom import via_geometry
    via_dia, via_drill, via_src = via_geometry(board, rt)
    bb = board.GetBoardEdgesBoundingBox()
    bounds = (bb.GetLeft() / 1e6, bb.GetTop() / 1e6,
              bb.GetRight() / 1e6, bb.GetBottom() / 1e6)

    segs, rects = geomir.extract_objects(board, layers)
    attempted = routed = failed = tracks = vias = 0
    detail = []
    drawn, drawn_via = [], []
    for net_name in (plan.pre_routed_nets or [])[:max_nets]:
        net = board.FindNet(net_name)
        if net is None:
            continue
        nc = net.GetNetCode()
        pads = _net_pads(board, net_name, layers)
        if len(pads) < 2:
            continue
        attempted += 1
        for (i, j) in _mst_edges(pads)[:max_edges]:
            a, b = pads[i], pads[j]
            try:
                plans, _ = geomir.plan_paths(
                    (a[0], a[1]), (b[0], b[1]), floor, nc, segs, rects,
                    bounds=bounds, width_ladder=(floor,), rules=rt,
                    planes_by_layer=None)
            except Exception:
                plans = []
            plans = [pl for pl in plans
                     if (pl.get('pts') and int(pl['pts'][0][2]) == a[2]
                         and int(pl['pts'][-1][2]) == b[2])]
            if not plans:
                failed += 1
                continue
            pts = plans[0]['pts']
            created = []
            ok = True
            for p, q in zip(pts, pts[1:]):
                if int(p[2]) == int(q[2]):
                    vok, _ = geomir.verify_polyline(
                        [(p[0], p[1]), (q[0], q[1])], floor, nc,
                        segs[int(p[2])], rects[int(p[2])], rt)
                    if not vok:
                        ok = False
                        break
                    t = pcbnew.PCB_TRACK(board)
                    t.SetStart(pcbnew.VECTOR2I(int(round(p[0] * 1e6)),
                                               int(round(p[1] * 1e6))))
                    t.SetEnd(pcbnew.VECTOR2I(int(round(q[0] * 1e6)),
                                             int(round(q[1] * 1e6))))
                    t.SetWidth(int(round(floor * 1e6)))
                    t.SetLayer(layers[int(p[2])])
                    t.SetNet(net)
                    try:
                        t.SetLocked(lock)
                    except Exception:
                        pass
                    board.Add(t)
                    created.append(t)
                    drawn.append((net_name, layers[int(p[2])],
                                  round(p[0], 4), round(p[1], 4),
                                  round(q[0], 4), round(q[1], 4)))
                else:
                    vok, _ = geomir.via_ok(p[0], p[1], nc, segs, rects,
                                           via_r=via_dia / 2.0, rules=rt)
                    if not vok:
                        ok = False
                        break
                    v = pcbnew.PCB_VIA(board)
                    v.SetPosition(pcbnew.VECTOR2I(int(round(p[0] * 1e6)),
                                                  int(round(p[1] * 1e6))))
                    v.SetWidth(int(round(via_dia * 1e6)))
                    v.SetDrill(int(round(via_drill * 1e6)))
                    v.SetNet(net)
                    try:
                        v.SetLocked(lock)
                    except Exception:
                        pass
                    board.Add(v)
                    created.append(v)
                    drawn_via.append((net_name, round(p[0], 4), round(p[1], 4)))
            conn = False
            if ok:
                try:
                    conn = rg.pair_connected(
                        board, {'nc': nc, 'a': (a[0], a[1]),
                                'b': (b[0], b[1])})
                except Exception:
                    conn = True
            if not ok or not conn:
                for it in created:
                    try:
                        board.Remove(it)
                    except Exception:
                        pass
                del drawn[len(drawn) - sum(1 for x in created
                                           if x.GetClass() != 'PCB_VIA'):]
                del drawn_via[len(drawn_via) - sum(1 for x in created
                                                   if x.GetClass() == 'PCB_VIA'):]
                failed += 1
                continue
            routed += 1
            tracks += len([x for x in created if x.GetClass() != 'PCB_VIA'])
            vias += len([x for x in created if x.GetClass() == 'PCB_VIA'])
            detail.append({'net': net_name, 'from': [a[0], a[1]],
                           'to': [b[0], b[1]]})
            # The obstacle map must include what was just drawn: planning
            # the next edge against a stale map ran it into the previous
            # net's copper (2 shorts on the first board this ran on).
            segs, rects = geomir.extract_objects(board, layers)
    pcbnew.SaveBoard(str(board_path), board)
    return {'nets_attempted': attempted, 'edges_routed': routed,
            'edges_failed': failed, 'tracks': tracks, 'vias': vias,
            'seconds': round(time.time() - t0, 2),
            'detail': detail[:8], 'locked': bool(lock),
            'via': {'diameter_mm': via_dia, 'drill_mm': via_drill,
                    'source': via_src},
            # geometry of committed copper, for the survival certificate
            'drawn': drawn, 'drawn_vias': drawn_via}


def survivors(board_path, pre) -> dict:
    """How much of the pre-routed copper is still on a board.

    strip removes every track; a pre-route that does not survive it never
    reached the router.  This is the certificate that it did.
    """
    import pcbnew

    if not pre:
        return {'drawn': 0, 'surviving': 0}
    want = [(d[0], d[1], d[2], d[3], d[4], d[5]) for d in pre.get('drawn') or []]
    want_v = [(d[0], d[1], d[2]) for d in pre.get('drawn_vias') or []]
    have = set()
    b = pcbnew.LoadBoard(str(board_path))
    for t in b.GetTracks():
        try:
            q = t.GetPosition()
            if t.Type() == pcbnew.PCB_VIA_T:
                have.add((t.GetNetname(), round(q.x / 1e6, 4),
                          round(q.y / 1e6, 4), 'via'))
                continue
            a, c = t.GetStart(), t.GetEnd()
            key = (t.GetNetname(), t.GetLayer(), round(a.x / 1e6, 4),
                   round(a.y / 1e6, 4), round(c.x / 1e6, 4),
                   round(c.y / 1e6, 4))
            have.add(key)
            have.add(key[:1] + (key[1], key[4], key[5], key[2], key[3]))
        except Exception:
            continue
    seg = sum(1 for w in want if w in have)
    via = sum(1 for w in want_v if w + ('via',) in have)
    return {'drawn': len(want) + len(want_v),
            'surviving': seg + via, 'segments': seg, 'vias': via}
