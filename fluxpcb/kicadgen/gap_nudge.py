# -*- coding: utf-8 -*-
"""gap_nudge — shift blocking track segments to widen a corridor
(the DaCapo7 sibling primitive: 挪线而非挪件).

"Translates violating tracks. Geometry only, no topology change;
endpoints move together with segments sharing them exactly; checks for
space before moving." — a 0.1-0.3mm perpendicular nudge of the tracks
lining a sealed corridor costs an order of magnitude less than moving
components, and the residual's true cause is often tracks hugging too
tight, not parts sitting wrong.

v2 connectivity contract (the P6 lesson: 4 chains nudged = 10 phantom
opens — the primitive "topology unchanged" LIED):
  1. rigid chains union by ANY same-net contact — endpoint-endpoint
     AND endpoint-on-segment (T-junction stubs move with the stem;
     a stub left behind is a manufactured open),
  2. anchor preservation — a moved endpoint that touches same-net
     copper NOT in the group (via barrel, pad, foreign chain end)
     must STILL touch it after the move, else the group is rejected,
  3. multi-layer truth — extraction covers ALL signal layers and each
     segment is verified on ITS OWN layer (the v1 two-layer/one-index
     shortcut breaks silently on 4-layer boards).

Usage:  gap_nudge.py PCB X Y DX DY DIST
  (X,Y) = the blocking point from the map's reject (the corridor pinch)
  (DX,DY) = the unit direction to open toward (perpendicular to corridor)
  DIST = 0.1..0.3mm

Prints one JSON line: {nudged, kept_connected, rejected, groups}
"""
import json
import math
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


def _seg_pt_dist(t, x, y):
    s, e = t.GetStart(), t.GetEnd()
    sx, sy, ex, ey = s.x / MM, s.y / MM, e.x / MM, e.y / MM
    l2 = (ex - sx) ** 2 + (ey - sy) ** 2
    if l2 == 0:
        return math.hypot(x - sx, y - sy)
    tpar = max(0.0, min(1.0, ((x - sx) * (ex - sx) +
                              (y - sy) * (ey - sy)) / l2))
    px, py = sx + (ex - sx) * tpar, sy + (ey - sy) * tpar
    return math.hypot(x - px, y - py)


def _near_pt(t, x, y, r):
    return _seg_pt_dist(t, x, y) <= r


# ---- candidates: same-net tracks near the pinch -----------------
net_nc0 = None
candidates = []
for t in b.GetTracks():
    try:
        if t.Type() == pcbnew.PCB_VIA_T:
            continue
        if _near_pt(t, bx, by, FIND_R):
            candidates.append(t)
            net_nc0 = t.GetNetCode()
    except AttributeError:
        continue    # ok-silent: 畸形对象逐个跳过
if not candidates:
    print(json.dumps({"nudged": 0, "kept_connected": 0,
                      "rejected": 0, "groups": 0}))
    sys.exit(0)
# only same-net chains move together (the pinch may host several nets)
candidates = [t for t in candidates if t.GetNetCode() == net_nc0]

# ---- rigid chains: union by ANY contact (v2: T-junctions join) ----
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
            contact = False
            us, ue = u.GetStart(), u.GetEnd()
            for g in grp:
                gpts = [(g.GetStart().x / MM, g.GetStart().y / MM),
                        (g.GetEnd().x / MM, g.GetEnd().y / MM)]
                upt = [(us.x / MM, us.y / MM), (ue.x / MM, ue.y / MM)]
                if min(_seg_pt_dist(g, *upt[0]),
                       _seg_pt_dist(g, *upt[1]),
                       _seg_pt_dist(u, *gpts[0]),
                       _seg_pt_dist(u, *gpts[1])) <= CHAIN_TOL:
                    contact = True
                    break
            if contact:
                grp.append(u)
                used.add(j)
                changed = True
    groups.append(grp)

# ---- the unit direction (open the corridor toward it) -----------
L = math.hypot(dx, dy) or 1.0
ux, uy = dx / L, dy / L

sys.path.insert(0, __import__("os").path.dirname(
    __import__("os").path.abspath(__file__)))
import geomir  # noqa: E402
import routegrid as rg  # noqa: E402

lids = rg.all_copper_ids(b)   # 验证视图全铜层(via_ok 契约)
segs, rects = geomir.extract_objects(b, lids)
li_of = {ly: k for k, ly in enumerate(lids)}

# same-net anchors that do NOT move: vias + tracks outside the groups
moved_nc = net_nc0
other_tracks = [t for t in b.GetTracks()
                if t.Type() != pcbnew.PCB_VIA_T
                and t.GetNetCode() == moved_nc
                and t not in candidates]
vias = [v for v in b.GetTracks()
        if v.Type() == pcbnew.PCB_VIA_T and v.GetNetCode() == moved_nc]

nudged = kept = rejected = 0
causes = []
for grp in groups:
    moves = {}
    grpset = set(id(t) for t in grp)
    for t in grp:
        for getter in (t.GetStart, t.GetEnd):
            p = getter()
            key = (p.x, p.y)
            moves[key] = (p.x + ux * dist * MM, p.y + uy * dist * MM)

    # (2) anchor preservation: every same-net copper a moved endpoint
    # touches and that does NOT move must still be touched after
    def _touches(px, py):
        """px,py in nm -> does this point sit on any fixed anchor?"""
        hit = []
        for v in vias:
            c = v.GetPosition()
            # KiCad 10: PCB_VIA::GetWidth asserts without a layer arg —
            # derive the pad radius from the bounding box (cross-version)
            bbv = v.GetBoundingBox()
            r = max(bbv.GetWidth(), bbv.GetHeight()) / 2.0
            if math.hypot(px - c.x, py - c.y) <= r:
                hit.append(("via", v))
        for fp_ in b.GetFootprints():
            for pad_ in fp_.Pads():
                if pad_.GetNetCode() != moved_nc:
                    continue
                bb = pad_.GetBoundingBox()
                q0 = pad_.GetPosition()
                if abs(px - q0.x) <= bb.GetWidth() / 2 and \
                        abs(py - q0.y) <= bb.GetHeight() / 2:
                    hit.append(("pad", pad_))
        for t2 in other_tracks:
            if id(t2) in grpset:
                continue
            if _seg_pt_dist(t2, px / MM, py / MM) <= CHAIN_TOL:
                hit.append(("track", t2))
        return hit

    anchors_ok = True
    reject_cause = ""
    for t in grp:
        for getter in (t.GetStart, t.GetEnd):
            p0 = getter()
            old_hit = _touches(p0.x, p0.y)
            if not old_hit:
                continue                       # free end — nothing to keep
            nm = moves[(p0.x, p0.y)]
            new_hit_ids = {id(o) for (_k, o) in _touches(nm[0], nm[1])}
            for (_k, o) in old_hit:
                if id(o) not in new_hit_ids:
                    anchors_ok = False
                    reject_cause = ("anchor:%s脱离" % _k)
                    break
            if not anchors_ok:
                break
        if not anchors_ok:
            break

    # (3) space check on the NEW geometry, per real layer
    if anchors_ok:
        for t in grp:
            s, e = t.GetStart(), t.GetEnd()
            ns = moves[(s.x, s.y)]
            ne = moves[(e.x, e.y)]
            w = t.GetWidth() / MM
            li = li_of.get(t.GetLayer(), 0)
            v, _bl = geomir.verify_polyline(
                [(ns[0] / MM, ns[1] / MM), (ne[0] / MM, ne[1] / MM)],
                w, moved_nc, segs[li], rects[li])
            if not v:
                anchors_ok = False
                reject_cause = ("space:%s" % json.dumps(
                    (_bl or {}).get("type", "?")))
                break

    if not anchors_ok:
        rejected += 1
        causes.append(reject_cause or "unknown")
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
                  "rejected": rejected, "groups": len(groups),
                  "causes": causes}))
