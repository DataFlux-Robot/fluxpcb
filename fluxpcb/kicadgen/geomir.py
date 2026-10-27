# -*- coding: utf-8 -*-
"""geomir — the board's geometric IR (M1): the MAP the decision layer
has been missing.

The essence defect (M-round): the loop passed aggregate counters and
class LABELS to the decider, while closing a stubborn open requires
OBJECT-LEVEL spatial understanding — which island, which free corridor,
which exact line. The reference project's 30-minute finish was one
map-reasoned intervention ("draw from (1.93,7.2) to (1.22,2.54) along
the left edge"). This module is that map, as a queryable IR:

  islands_of(net)      -> islands with pad members + anchors + bbox
  free_path(a,b,...)   -> candidate polylines VERIFIED against world
                          geometry (same rules as commit_gate)
  obstacles_near(p, r) -> what is blocking, whose net, how big
  plan_net(net)        -> the full decision package: islands + verified
                          paths + blockers + summary text + SVG window

Pure planning core (candidate_polylines / verify_polyline / summarize /
render_plan) is pcbnew-free and unit-testable; board extraction is a
thin adapter. CLI:  geomir.py PCB NET  ->  one JSON plan on stdout,
neighborhood SVG at <PCB>.<NET>.plan.svg.

State contract (C0): every plan carries the board fingerprint — the
map, like every number, is bound to the state it depicts.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

CLEAR = 0.17          # mm — same rule as routegrid/commit_gate


# ===================================================== pure planning core
def candidate_polylines(a, b, spans=None, bounds=None):
    """Ordered candidate polylines from a=(x,y) to b=(x,y): the two L
    shapes, then Z shapes with perpendicular mid-offsets both ways
    (the manual_links/i2c1_reroute fold patterns), then BOARD-EDGE
    corridor detours (bounds=(x0,y0,x1,y1) — the proven pattern: the
    reference project's one manual fix routed exactly along the left
    board edge; the perimeter strip is the primary free-space resource
    on a crowded board). Pure geometry — the VERIFIER picks what
    survives."""
    (x1, y1), (x2, y2) = a, b
    spans = spans or [2.0, 4.0, 7.0, 11.0]
    out = []
    # L shapes
    out.append([(x1, y1), (x2, y1), (x2, y2)])
    out.append([(x1, y1), (x1, y2), (x2, y2)])
    # edge corridors: run to the perimeter strip, along it, back in
    if bounds:
        x0, y0, x1b, y1b = bounds
        for ex, ey in ((x0 + 0.8, None), (x1b - 0.8, None),
                       (None, y0 + 0.8), (None, y1b - 0.8)):
            if ex is not None:
                out.append([(x1, y1), (ex, y1), (ex, y2), (x2, y2)])
                out.append([(x1, y1), (ex, y1), (ex, y2), (x2, y2)])
            else:
                out.append([(x1, y1), (x1, ey), (x2, ey), (x2, y2)])
                out.append([(x1, y1), (x1, ey), (x2, ey), (x2, y2)])
    # Z shapes: midpoint offset perpendicular to a->b
    dx, dy = x2 - x1, y2 - y1
    L = math.hypot(dx, dy) or 1.0
    px, py = -dy / L, dx / L           # unit perpendicular
    for s in spans:
        for sgn in (1, -1):
            mx, my = (x1 + x2) / 2 + px * s * sgn, \
                (y1 + y2) / 2 + py * s * sgn
            out.append([(x1, y1), (mx, my), (x2, y2)])
    # staircases (two perpendicular jogs near each end — captures the
    # "slide along the free channel then cross" pattern of edge detours)
    for s in spans:
        for sgn in (1, -1):
            jx, jy = px * s * sgn, py * s * sgn
            out.append([(x1, y1), (x1 + jx, y1 + jy),
                        (x2 + jx, y2 + jy), (x2, y2)])
    return out


def verify_polyline(pts, width, nc, segs, rects):
    """World-geometry verification of one polyline against fixed copper
    lists (same rules as commit_gate — the single geometric truth).

    segs: [(net, (x1,y1), (x2,y2), half_w)]   rects: [(net, rect)]
    Returns (ok, blocker) where blocker describes the first violation
    for the fix-it feedback ("violated: X, by how much").
    """
    from routegrid import seg_seg_dist, pt_rect_dist
    own = width / 2
    for (p, q) in zip(pts, pts[1:]):
        pad = own + CLEAR + 0.05
        x0, x1 = min(p[0], q[0]) - pad, max(p[0], q[0]) + pad
        y0, y1 = min(p[1], q[1]) - pad, max(p[1], q[1]) + pad
        for net, s, e, hw in segs:
            if net == nc:
                continue
            if seg_seg_dist(p, q, s, e) < CLEAR + hw + own:
                return False, {"type": "track", "net": net,
                               "at": [round((s[0] + e[0]) / 2, 2),
                                      round((s[1] + e[1]) / 2, 2)],
                               "short_by_mm": round(
                                   CLEAR + hw + own -
                                   seg_seg_dist(p, q, s, e), 3)}
        for net, rect in rects:
            if net == nc:
                continue
            n = max(2, int(math.hypot(q[0] - p[0], q[1] - p[1]) / 0.1))
            for k in range(n + 1):
                t = k / n
                x, y = p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t
                d = pt_rect_dist(x, y, rect)
                if d < CLEAR + own:
                    return False, {"type": "pad", "net": net,
                                   "at": [round(rect[0], 2),
                                          round(rect[1], 2)],
                                   "short_by_mm": round(
                                       CLEAR + own - d, 3)}
    return True, None


def best_paths(a, b, width, nc, segs, rects, limit=3, bounds=None):
    """Verified candidate paths sorted by length — the actionable part
    of the fix-it: each carries its pts (the decision is a PATH)."""
    verified, rejects = [], []
    for cand in candidate_polylines(a, b, bounds=bounds):
        ok, blocker = verify_polyline(cand, width, nc, segs, rects)
        if ok:
            length = sum(math.dist(p, q)
                         for p, q in zip(cand, cand[1:]))
            verified.append({"pts": [[round(x, 3), round(y, 3)]
                                     for x, y in cand],
                             "length_mm": round(length, 2)})
        elif len(rejects) < 3:
            rejects.append({"pts": [[round(x, 3), round(y, 3)]
                                    for x, y in cand],
                            "blocked_by": blocker})
    verified.sort(key=lambda v: v["length_mm"])
    return verified[:limit], rejects[:3]


def summarize(net, islands, plans, rejects):
    """The one-paragraph map digest the decider reads (point 4's final
    form: failure info = map + executable path candidates)."""
    parts = [f"{net}: {len(islands)}岛"]
    for i, isl in enumerate(islands):
        parts.append(f"岛{i + 1}({isl['anchor'][0]:.2f},{isl['anchor'][1]:.2f}"
                     f",{len(isl['pads'])}pad)")
    if plans:
        p0 = plans[0]
        pts = "->".join(f"({p[0]:.1f},{p[1]:.1f},L{p[2] if len(p) > 2 else 0})"
                        for p in p0["pts"])
        parts.append(f"可行路径{len(plans)}条,最短{p0['length_mm']}mm: {pts}")
    elif rejects:
        bb = rejects[0].get("blocked_by") or {}
        parts.append(f"全部候选被挡: {bb.get('net','?')}@{bb.get('at')} "
                     f"差{bb.get('short_by_mm')}mm")
    return "; ".join(parts)


def render_plan(plan, out_path, window=None):
    """Neighborhood SVG: islands green, obstacles gray, verified paths
    blue, rejected best red — the map the LLM looks at."""
    w_px = 420
    if window is None:
        allpts = [tuple(p[:2]) for isl in plan["islands"]
                  for p in isl["pads"]]
        for pl in plan["paths"]:
            allpts += [tuple(p[:2]) for p in pl["pts"]]
        for r in plan.get("rejects", []):
            allpts += [tuple(p) for p in r["pts"]]
        if not allpts:
            allpts = [(0, 0), (1, 1)]
        x0 = min(p[0] for p in allpts) - 3
        x1 = max(p[0] for p in allpts) + 3
        y0 = min(p[1] for p in allpts) - 3
        y1 = max(p[1] for p in allpts) + 3
    else:
        x0, y0, x1, y1 = window
    span = max(x1 - x0, y1 - y0) or 1
    scale = w_px / span
    h_px = int((y1 - y0) * scale) + 30

    def px(x):
        return round((x - x0) * scale + 10, 1)

    def py(y):
        return round((y - y0) * scale + 10, 1)
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w_px + 20}" '
         f'height="{h_px}" font-family="monospace" font-size="10">']
    s.append(f'<rect x="0" y="0" width="{w_px + 20}" height="{h_px}" '
             f'fill="white"/>')
    s.append(f'<text x="8" y="14">{plan["net"]} — '
             f'{plan["summary"][:110]}</text>')
    for (net, (ax, ay), (bx, by), _hw) in plan.get("segs", []):
        s.append(f'<line x1="{px(ax)}" y1="{py(ay)}" x2="{px(bx)}" '
                 f'y2="{py(by)}" stroke="#bbb" stroke-width="2"/>')
    for (net, rect) in plan.get("rects", []):
        cx, cy, hw, hh, _a = rect
        s.append(f'<rect x="{px(cx - hw)}" y="{py(cy - hh)}" '
                 f'width="{hw * 2 * scale:.1f}" height="{hh * 2 * scale:.1f}" '
                 f'fill="#ddd" stroke="#999"/>')
    for isl in plan["islands"]:
        for (x, y) in isl["pads"]:
            s.append(f'<circle cx="{px(x)}" cy="{py(y)}" r="4" '
                     f'fill="#2a2" stroke="none" opacity="0.85"/>')
    for pl in plan["paths"]:
        d = " ".join(f"{px(x)},{py(y)}" for x, y in pl["pts"])
        s.append(f'<polyline points="{d}" fill="none" stroke="#26c" '
                 f'stroke-width="2.5"/>')
    for r in plan.get("rejects", []):
        d = " ".join(f"{px(x)},{py(y)}" for x, y in r["pts"])
        s.append(f'<polyline points="{d}" fill="none" stroke="#c22" '
                 f'stroke-width="1.5" stroke-dasharray="4,3"/>')
    s.append("</svg>")
    Path(out_path).write_text("\n".join(s), encoding="utf-8")
    return str(out_path)




def plan_paths(a, b, width, nc, segs_by_layer, rects_by_layer,
               bounds=None, limit=4):
    """Layer-resolved path search — THE single-net repair shape
    (i2c1_reroute: F.Cu -> via -> B.Cu -> via -> F.Cu):
      * plain polylines on EACH layer (a layer-0 path only fights
        layer-0 obstacles — dodges congestion on the other side)
      * via-detour candidates: hop layers near the anchors, run on the
        quiet layer, hop back (via sites offset 1-3mm from anchors)
    Returns paths with pts as (x, y, layer) — direct_link's spec shape.
    """
    verified, rejects = [], []
    nlay = len(segs_by_layer)
    for li in range(nlay):
        for cand in candidate_polylines(a, b, bounds=bounds):
            ok, blocker = verify_polyline(
                cand, width, nc, segs_by_layer[li], rects_by_layer[li])
            if ok:
                length = sum(math.dist(p, q)
                             for p, q in zip(cand, cand[1:]))
                verified.append({"pts": [[round(x, 3), round(y, 3), li]
                                         for x, y in cand],
                                 "length_mm": round(length, 2),
                                 "mode": f"layer{li}"})
            elif len(rejects) < 3:
                rejects.append({"pts": [[round(x, 3), round(y, 3), li]
                                        for x, y in cand],
                                "blocked_by": blocker})
    # via-detour: F(0) -> via -> quiet layer -> via -> F(0)
    if nlay >= 2:
        for quiet in range(1, nlay):
            for da in (1.5, 3.0):
                for db in (1.5, 3.0):
                    va = (a[0] + da, a[1] + da * 0.3)
                    vb = (b[0] - db, b[1] - db * 0.3)
                    cand = [(a[0], a[1], 0), (va[0], va[1], 0),
                            (va[0], va[1], quiet), (vb[0], vb[1], quiet),
                            (vb[0], vb[1], 0), (b[0], b[1], 0)]
                    bad = None
                    for (p, q) in zip(cand, cand[1:]):
                        if p[2] != q[2]:
                            continue          # via hop: checked below
                        li = p[2]
                        ok, blocker = verify_polyline(
                            [(p[0], p[1]), (q[0], q[1])], width, nc,
                            segs_by_layer[li], rects_by_layer[li])
                        if not ok:
                            bad = blocker
                            break
                    if bad is None:
                        length = sum(
                            math.dist((p[0], p[1]), (q[0], q[1]))
                            for p, q in zip(cand, cand[1:]))
                        verified.append({
                            "pts": [[round(x, 3), round(y, 3), li]
                                    for x, y, li in cand],
                            "length_mm": round(length, 2),
                            "mode": f"via-detour-L{quiet}"})
                    elif len(rejects) < 4:
                        rejects.append({"pts": [], "blocked_by": bad})
    verified.sort(key=lambda v: v["length_mm"])
    return verified[:limit], rejects[:4]


# ==================================================== board adapter layer
def extract_objects(board, layer_ids):
    """Plain-geometry extraction, PER LAYER (consumed by verify_polyline
    — the same lists commit_gate feeds, i.e. ONE geometric truth).

    Returns (segs_by_layer, rects_by_layer): each is a list indexed by
    router-layer with (net, geom, ...) tuples — layer-resolved so a
    path on B.Cu only fights B.Cu obstacles (the i2c1_reroute F-via-B
    pattern is how a single stubborn net dodges congestion)."""
    import routegrid as rg
    segs = [[] for _ in layer_ids]
    rects = [[] for _ in layer_ids]
    MM = 1000000
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            try:
                nc = pad.GetNetCode()
                cx, cy, hw, hh, ang = rg.pad_rect(pad)
                for li, lid in enumerate(layer_ids):
                    if pad.IsOnLayer(lid):
                        rects[li].append((nc, (cx, cy, hw, hh, ang)))
            except (AttributeError, TypeError):
                continue
    for it in board.GetTracks():
        try:
            nc = it.GetNetCode()
            if it.Type() == pcbnew_via_t(board):
                q = it.GetPosition()
                x, y = q.x / MM, q.y / MM
                r = it.GetWidth(0) / MM / 2
                for li in range(len(layer_ids)):
                    segs[li].append((nc, (x, y), (x, y), r))
            else:
                lid = it.GetLayer()
                if lid not in layer_ids:
                    continue
                li = layer_ids.index(lid)
                s, e = it.GetStart(), it.GetEnd()
                w = it.GetWidth() / MM
                segs[li].append((nc, (s.x / MM, s.y / MM),
                                 (e.x / MM, e.y / MM), w / 2))
        except (AttributeError, TypeError):
            continue
    return segs, rects


def pcbnew_via_t(board):
    import pcbnew
    return pcbnew.PCB_VIA_T


def islands_of(board, nc):
    """Islands of a net: position-keyed union-find over pads+track
    anchors (same method as route_jumper.open_islands / conn_audit)."""
    class UF:
        def __init__(self):
            self.p = {}

        def find(self, x):
            self.p.setdefault(x, x)
            while self.p[x] != x:
                self.p[x] = self.p[self.p[x]]
                x = self.p[x]
            return x

        def union(self, a, b):
            ra, rb = self.find(a), self.find(b)
            if ra != rb:
                self.p[ra] = rb

    MM = 1000000
    board.BuildConnectivity()
    conn = board.GetConnectivity()
    uf = UF()
    pads = []
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            if pad.GetNetCode() != nc:
                continue
            q = pad.GetPosition()
            k = (q.x, q.y)
            pads.append((pad, k))
            uf.find(k)
            try:
                for nb in conn.GetConnectedItems(pad):
                    if nb.GetNetCode() != nc:
                        continue
                    try:
                        nq = nb.GetPosition()
                        uf.union(k, (nq.x, nq.y))
                    except AttributeError:
                        s, e = nb.GetStart(), nb.GetEnd()
                        uf.union(k, (s.x, s.y))
                        uf.union(k, (e.x, e.y))
            except (AttributeError, TypeError):
                pass
    groups = {}
    for pad, k in pads:
        groups.setdefault(uf.find(k), []).append(
            (k[0] / MM, k[1] / MM))
    islands = []
    for pts in groups.values():
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        islands.append({
            "pads": [[round(x, 3), round(y, 3)] for x, y in pts],
            "anchor": [round(sum(xs) / len(xs), 3),
                       round(sum(ys) / len(ys), 3)],
            "bbox": [round(min(xs), 2), round(min(ys), 2),
                     round(max(xs), 2), round(max(ys), 2)],
        })
    islands.sort(key=lambda i: -len(i["pads"]))
    return islands


def plan_net(board, net_name, width=0.2):
    """THE decision package for one stubborn net: islands + verified
    paths between island anchors + what blocks the rest + summary."""
    net = board.FindNet(net_name)
    if net is None:
        return {"net": net_name, "error": "net not found"}
    nc = net.GetNetCode()
    import pcbnew
    lids = [pcbnew.F_Cu, pcbnew.B_Cu]
    try:
        import routegrid as rg
        lids = rg.signal_layer_ids(board)
    except Exception:
        pass
    segs, rects = extract_objects(board, lids)
    islands = islands_of(board, nc)
    paths, rejects = [], []
    try:
        bb = board.GetBoardEdgesBoundingBox()
        MM = 1000000
        bounds = (bb.GetX() / MM, bb.GetY() / MM,
                  (bb.GetX() + bb.GetWidth()) / MM,
                  (bb.GetY() + bb.GetHeight()) / MM)
    except Exception:
        bounds = None
    if len(islands) >= 2:
        for i in range(1, len(islands)):
            a = tuple(islands[0]["anchor"])
            b = tuple(islands[i]["anchor"])
            ok, rej = plan_paths(a, b, width, nc, segs, rects,
                                 bounds=bounds)
            for p in ok:
                p["between"] = [0, i]
            paths += ok
            rejects += rej
    # neighborhood window obstacles (the map around the failure)
    win_segs = [s for s in segs if s[0] != nc][:400]
    win_rects = [r for r in rects if r[0] != nc][:400]
    # blocker netcodes -> names (the decider needs to ACT on blockers:
    # the reference pattern is "reroute the blocker away, then link")
    nc2name = {}
    try:
        for _nc, _ni in board.GetNetInfo().NetsByNetcode().items():
            nc2name[int(_nc)] = _ni.GetNetname()
    except Exception:
        pass
    for r in rejects:
        bl = r.get("blocked_by")
        if bl and isinstance(bl.get("net"), int):
            bl["net_name"] = nc2name.get(bl["net"], str(bl["net"]))
    summary = summarize(net_name, islands, paths, rejects)
    return {"net": net_name, "netcode": nc, "islands": islands,
            "paths": paths, "rejects": rejects,
            "blockers": sorted({r["blocked_by"].get("net_name")
                                for r in rejects
                                if r.get("blocked_by")}) - {net_name, "0"},
            "segs": win_segs, "rects": win_rects,
            "summary": summary}


# ==================================================== CLI
if __name__ == "__main__":
    import sys
    import pcbnew
    pcb, net_name = sys.argv[1], sys.argv[2]
    out_prefix = sys.argv[3] if len(sys.argv) > 3 else pcb
    b = pcbnew.LoadBoard(pcb)
    plan = plan_net(b, net_name)
    svg = f"{out_prefix}.{net_name}.plan.svg"
    try:
        render_plan(plan, svg)
        plan["svg"] = svg
    except Exception as e:
        plan["svg_error"] = str(e)[:80]
    print(json.dumps(plan, ensure_ascii=False))
