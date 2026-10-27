# -*- coding: utf-8 -*-
"""direct_link — the directed intervention executor (M3).

The manual_links.py pattern, systematized: draw ONE exact polyline
(the decision is a PATH on the geomir map), endpoints landing on the
net's own island anchors so connectivity is kept by construction,
then refill+save. This is the loop's answer to a stubborn open when
the map shows a free path — a 10-line intervention instead of a
global re-route war.

Usage:  direct_link.py PCB SPEC.json
SPEC = {"net": "EN", "pts": [[x,y,layer],...], "width": 0.2}
Prints one JSON line: {drawn, segs, error?}
"""
import json
import sys
from pathlib import Path

import pcbnew

MM = 1000000

pcb_path = sys.argv[1]
spec = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
net_name = spec["net"]
width = float(spec.get("width", 0.2))
pts = spec["pts"]

b = pcbnew.LoadBoard(pcb_path)
net = b.FindNet(net_name)
if net is None:
    print(json.dumps({"drawn": 0, "error": f"net {net_name} missing"}))
    sys.exit(0)

# T1b plan->execute re-verification: the plan was verified against the
# board AT PLANNING TIME; between plan and draw the board may have
# changed (T2/T3 runs: stale plans drew +5 shorts). RE-VERIFY the
# whole polyline against the CURRENT board before placing one item —
# the C0 contract applies to plans too.
try:
    import geomir
    _segs, _rects = geomir.extract_objects(b, [pcbnew.F_Cu, pcbnew.B_Cu])
    _nc = net.GetNetCode()
    _bad = None
    for (_p, _q) in zip(pts, pts[1:]):
        if _p[2] != _q[2]:
            ok, bl = geomir.via_ok(_p[0], _p[1], _nc, _segs, _rects)
            if not ok:
                _bad = f"via@{_p[:2]}: {bl}"
                break
            continue
        ok, bl = geomir.verify_polyline(
            [(_p[0], _p[1]), (_q[0], _q[1])], width, _nc,
            _segs[_p[2]], _rects[_p[2]])
        if not ok:
            _bad = f"seg@{_p[:2]}->{_q[:2]}: {bl}"
            break
    if _bad is not None:
        print(json.dumps({"drawn": 0,
                          "error": "stale-plan rejected: " + _bad}))
        sys.exit(0)
except Exception as _e:
    print(json.dumps({"drawn": 0,
                      "error": "reverify failed: " + str(_e)[:80]}))
    sys.exit(0)


def layer_id(k):
    # 0 = F.Cu, 1 = B.Cu (conn_audit pair convention); inner as 2..
    if k == 0:
        return pcbnew.F_Cu
    if k == 1:
        return pcbnew.B_Cu
    lid = getattr(pcbnew, "In%d_Cu" % (k - 1), None)
    return lid if lid is not None else pcbnew.F_Cu


items = []
prev = None
for (x, y, li) in pts:
    if prev is not None:
        px, py, pli = prev
        if pli != li:
            v = pcbnew.PCB_VIA(b)
            v.SetPosition(pcbnew.VECTOR2I(int(px * MM), int(py * MM)))
            v.SetWidth(int(0.6 * MM))
            v.SetDrill(int(0.3 * MM))
            v.SetNet(net)
            b.Add(v)
            items.append(v)
        if abs(px - x) > 0.01 or abs(py - y) > 0.01:
            t = pcbnew.PCB_TRACK(b)
            t.SetStart(pcbnew.VECTOR2I(int(px * MM), int(py * MM)))
            t.SetEnd(pcbnew.VECTOR2I(int(x * MM), int(y * MM)))
            t.SetWidth(int(width * MM))
            t.SetLayer(layer_id(li))
            t.SetNet(net)
            b.Add(t)
            items.append(t)
    prev = (x, y, li)

try:
    pcbnew.ZONE_FILLER(b).Fill(b.Zones())
except Exception:
    pass
b.Save(pcb_path)
print(json.dumps({"drawn": len(items), "net": net_name,
                  "segs": len(pts) - 1}))
