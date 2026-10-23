# -*- coding: utf-8 -*-
"""Free B.Cu under BGA footprints — separate process by necessity.

KiCad 10's Python wrappers poison the process after board.Add/Remove
churn: subsequent LoadBoard/GetTracks/GetFootprints calls return raw
SwigPyObjects. This script runs alone: fresh load, collect BGA boxes,
remove B.Cu TRACKS (vias stay — zones anti-pad them), save, exit.

Why: Freerouting routes escape lanes on B.Cu straight under the ball
field; those tracks block the dog-bone via landings for balls FR left
unrouted. Affected nets re-open and the session re-routes them on the
freed copper.
"""
import json
import sys

import pcbnew

MM = 1000000

pcb = sys.argv[1]
b = pcbnew.LoadBoard(pcb)

boxes = []
for fp in b.GetFootprints():
    try:
        pads = [p for p in fp.Pads() if p.GetAttribute() != 3
                and p.IsOnLayer(pcbnew.F_Cu)]
    except (TypeError, AttributeError):
        continue
    if len(pads) < 60:
        continue
    bb = fp.GetBoundingBox()
    boxes.append(((bb.GetX() - 500000) / MM, (bb.GetY() - 500000) / MM,
                  (bb.GetX() + bb.GetWidth() + 500000) / MM,
                  (bb.GetY() + bb.GetHeight() + 500000) / MM))

victims = []
for it in b.GetTracks():
    try:
        if it.Type() == pcbnew.PCB_VIA_T or it.GetLayer() != pcbnew.B_Cu:
            continue
        s, e = it.GetStart(), it.GetEnd()
    except AttributeError:
        continue
    mx, my = (s.x + e.x) / 2 / MM, (s.y + e.y) / 2 / MM
    if any(x0 <= mx <= x1 and y0 <= my <= y1 for (x0, y0, x1, y1) in boxes):
        victims.append(it)
for it in victims:
    b.Remove(it)
b.Save(pcb)
print(json.dumps({"boxes": len(boxes), "bcup_cleared": len(victims)}))
