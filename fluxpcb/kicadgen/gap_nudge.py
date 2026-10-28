# -*- coding: utf-8 -*-
"""gap_nudge — shift blocking track segments to widen a corridor
(the DaCapo7 sibling primitive: 挪线而非挪件).

"Translates violating tracks. Geometry only, no topology change;
endpoints move together with segments sharing them exactly; checks for
space before moving." — a 0.1-0.3mm perpendicular nudge of the tracks
lining a sealed corridor costs an order of magnitude less than moving
components, and the residual's true cause is often tracks hugging too
tight, not parts sitting wrong.

Usage:  gap_nudge.py PCB X Y DX DY DIST
  (X,Y) = the blocking point from the map's reject (the corridor pinch)
  (DX,DY) = the unit direction to open toward (perpendicular to corridor)
  DIST = 0.1..0.3mm

Prints one JSON line: {nudged, kept_connected, rejected, groups}
"""
import json
import sys

import pcbnew

MM = 1000000
FIND_R = 1.0          # mm: tracks near the blocking point
CHAIN_TOL = 0.02      # mm: shared-endpoint chain tolerance

pcb = sys.argv[1]
bx, by = float(sys.argv[2]), float(sys.argv[3])
dx, dy = float(sys.argv[4]), float(sys.argv[5])
dist = float(sys.argv[6])

b = pcbnew.LoadBoard(pcb)


def _near_pt(t, x, y, r):
    s, e = t.GetStart(), t.GetEnd()
    sx, sy, ex, ey = s.x / MM, s.y / MM, e.x / MM, e.y / MM
    # segment distance to point (cheap bounding check + true dist)
    import math
    l2 = (ex - sx) ** 2 + (ey - sy) ** 2
    if l2 == 0:
        return math.hypot(x - sx, y - sy) <= r
    tpar = max(0.0, min(1.0, ((x - sx) * (ex - sx) + (y - sy) * (ey - sy)) / l2))
    px, py = sx + (ex - sx) * tpar, sy + (ey - sy) * tpar
    return math.hypot(x - px, y - py) <= r


# group tracks of the same net that share endpoints (rigid chains)
candidates = []
for t in b.GetTracks():
    try:
        if t.Type() == pcbnew.PCB_VIA_T:
            continue
        if _near_pt(t, bx, by, FIND_R):
            candidates.append(t)
    except AttributeError:
        continue
if not candidates:
    print(json.dumps({"nudged": 0, "kept_connected": 0,
                      "rejected": 0, "groups": 0}))
    sys.exit(0)

# union chains by shared endpoints
import math
groups = []
used = set()
for i, t in enumerate(candidates):
    if i in used:
        continue
    grp = [t]
    used.add(i)
    changed = True
    while changed:
        changed = False
        for j, u in enumerate(candidates):
            if j in used:
                continue
            for g in grp:
                for pt1 in (g.GetStart(), g.GetEnd()):
                    for pt2 in (u.GetStart(), u.GetEnd()):
                        if math.hypot(pt1.x - pt2.x, pt1.y - pt2.y) \
                                < CHAIN_TOL * MM:
                            grp.append(u)
                            used.add(j)
                            changed = True
                            break
                    if changed:
                        break
                if changed:
                    break
    groups.append(grp)

# the unit direction (open the corridor toward it)
L = math.hypot(dx, dy) or 1.0
ux, uy = dx / L, dy / L
nudged = kept = rejected = 0
for grp in groups:
    # DaCapo7: group moves rigidly — shared endpoints move TOGETHER
    net_nc = grp[0].GetNetCode()
    moves = {}
    for t in grp:
        for getter, setter in ((t.GetStart, t.SetStart),
                               (t.GetEnd, t.SetEnd)):
            p = getter()
            key = (p.x, p.y)
            moves[key] = (p.x + ux * dist * MM, p.y + uy * dist * MM)
    # check space BEFORE moving: every moved segment must clear foreign
    # copper at the new geometry
    import sys as _s
    _s.path.insert(0, __import__("os").path.dirname(
        __import__("os").path.abspath(__file__)))
    import geomir
    new_segs = []
    for t in grp:
        s, e = t.GetStart(), t.GetEnd()
        ns = moves[(s.x, s.y)]
        ne = moves[(e.x, e.y)]
        w = t.GetWidth() / MM
        new_segs.append(((ns[0] / MM, ns[1] / MM),
                         (ne[0] / MM, ne[1] / MM), w))
    # full-board geometry for the check
    segs, rects = geomir.extract_objects(b, [pcbnew.F_Cu, pcbnew.B_Cu])
    ok = True
    for (p, q, w) in new_segs:
        li = 0  # chains are same-layer groups in practice
        for t in grp:
            li = 0 if t.GetLayer() == pcbnew.F_Cu else 1
            break
        v, _bl = geomir.verify_polyline([p, q], w, net_nc,
                                        segs[li], rects[li])
        if not v:
            ok = False
            break
    # pad containment: endpoints landing on pads must STAY inside
    # (topology unchanged is the primitive's contract)
    if ok:
        for t in grp:
            for getter in (t.GetStart, t.GetEnd):
                p0 = getter()
                for fp_ in b.GetFootprints():
                    for pad_ in fp_.Pads():
                        if pad_.GetNetCode() != net_nc:
                            continue
                        q0 = pad_.GetPosition()
                        bb = pad_.GetBoundingBox()
                        if (abs(p0.x - q0.x) <= bb.GetWidth() / 2
                                and abs(p0.y - q0.y) <= bb.GetHeight() / 2):
                            nm = moves[(p0.x, p0.y)]
                            if not (abs(nm[0] - q0.x) <= bb.GetWidth() / 2
                                    and abs(nm[1] - q0.y)
                                    <= bb.GetHeight() / 2):
                                ok = False
    if not ok:
        rejected += 1
        continue
    # commit: move every shared endpoint exactly once
    for t in grp:
        s, e = t.GetStart(), t.GetEnd()
        ns = moves[(s.x, s.y)]
        ne = moves[(e.x, e.y)]
        t.SetStart(pcbnew.VECTOR2I(int(ns[0]), int(ns[1])))
        t.SetEnd(pcbnew.VECTOR2I(int(ne[0]), int(ne[1])))
        nudged += 1
    kept += 1

b.Save(pcb)
print(json.dumps({"nudged": nudged, "kept_connected": kept,
                  "rejected": rejected, "groups": len(groups)}))
