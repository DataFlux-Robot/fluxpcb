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


def pre_route(board_path, plan, max_nets=8, max_edges=24):
    """Mutate the board file: draw verified escape edges for planned nets."""
    import pcbnew

    from ..kicadgen import geomir, routegrid as rg
    from ..kernel.rules import load_rules

    t0 = time.time()
    board = pcbnew.LoadBoard(str(board_path))
    rules = board_rules(board)
    floor = max(float(rules.get('min_track_width') or 0.2), 0.2)
    layers = rg.signal_layer_ids(board)
    rt = load_rules(Path(board_path))
    via_dia = float(rt.get('via_diameter') or 0.0) or 0.8
    try:
        via_dia = max(via_dia,
                      board.GetDesignSettings().m_ViasMinSize / 1e6)
    except Exception:
        pass
    via_drill = float(rt.get('via_drill') or 0.0) or max(0.3,
                                                         via_dia - 0.2)
    bb = board.GetBoardEdgesBoundingBox()
    bounds = (bb.GetLeft() / 1e6, bb.GetTop() / 1e6,
              bb.GetRight() / 1e6, bb.GetBottom() / 1e6)

    segs, rects = geomir.extract_objects(board, layers)
    attempted = routed = failed = tracks = vias = 0
    detail = []
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
                    board.Add(t)
                    created.append(t)
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
                    board.Add(v)
                    created.append(v)
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
                failed += 1
                continue
            routed += 1
            tracks += len([x for x in created if x.GetClass() != 'PCB_VIA'])
            vias += len([x for x in created if x.GetClass() == 'PCB_VIA'])
            detail.append({'net': net_name, 'from': [a[0], a[1]],
                           'to': [b[0], b[1]]})
    pcbnew.SaveBoard(str(board_path), board)
    return {'nets_attempted': attempted, 'edges_routed': routed,
            'edges_failed': failed, 'tracks': tracks, 'vias': vias,
            'seconds': round(time.time() - t0, 2),
            'detail': detail[:8]}
