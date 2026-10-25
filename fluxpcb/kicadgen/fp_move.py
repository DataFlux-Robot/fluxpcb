# -*- coding: utf-8 -*-
"""fp_move — LLM driver executor: nudge a footprint, rip its nets' copper.

Separate process by necessity (KiCad 10 SWIG containers poison after
Add/Remove churn). Moving a routed footprint must also remove the tracks
of every net that has a pad on it — stale track endpoints would dangle.

Usage:  fp_move.py PCB REF DX_MM DY_MM
Prints one JSON line: {"moved": bool, "ripped": n, "nets": [...]}
"""
import json
import sys

import pcbnew

MM = 1000000

pcb, ref, dx, dy = (sys.argv[1], sys.argv[2],
                    float(sys.argv[3]), float(sys.argv[4]))
b = pcbnew.LoadBoard(pcb)

fp = None
for f in b.GetFootprints():
    if f.GetReference() == ref:
        fp = f
        break
if fp is None:
    print(json.dumps({"moved": False, "error": f"footprint {ref} not found"}))
    sys.exit(0)

nets = {p.GetNetCode() for p in fp.Pads() if p.GetNetCode()}
net_names = sorted({p.GetNetname() for p in fp.Pads() if p.GetNetname()})

ripped = 0
for it in list(b.GetTracks()):
    try:
        if it.GetNetCode() in nets:
            b.Remove(it)
            ripped += 1
    except AttributeError:
        continue

q = fp.GetPosition()
fp.SetPosition(pcbnew.VECTOR2I(q.x + int(dx * MM), q.y + int(dy * MM)))
b.Save(pcb)
print(json.dumps({"moved": True, "ripped": ripped, "nets": net_names,
                  "to": [round(q.x / MM + dx, 2), round(q.y / MM + dy, 2)]}))
