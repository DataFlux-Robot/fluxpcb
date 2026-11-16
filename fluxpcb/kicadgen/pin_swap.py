# -*- coding: utf-8 -*-
"""pin_swap — LLM driver executor: swap two pads' nets on a footprint.

The sealed-BGA-ball decision: when route_diag says a ball can never
escape in its channel, exchanging its net with a neighbor that has a
free lane changes the ratsnest to something routable. Board-only
surgery — the SCHEMATIC still carries the old mapping, so this is
gated by FLUXNT_ALLOW_PIN_SWAP in the driver (netlist_topology will
flag the drift on the next full pipeline run; the schematic-consistent
path is regeneration with the mcu_db banned-set).

Usage:  pin_swap.py PCB REF PAD_A PAD_B
Prints one JSON line: {"swapped": bool, "ripped": n, "nets": [a, b]}
"""
import json
import sys

import pcbnew

MM = 1000000

pcb, ref, pa, pb = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
b = pcbnew.LoadBoard(pcb)

fp = None
for f in b.GetFootprints():
    if f.GetReference() == ref:
        fp = f
        break
if fp is None:
    print(json.dumps({"swapped": False, "error": f"{ref} not found"}))
    sys.exit(0)

padA = padB = None
for p in fp.Pads():
    name = p.GetPadName() if hasattr(p, "GetPadName") else str(
        p.GetNumber() if hasattr(p, "GetNumber") else "")
    if name == pa:
        padA = p
    elif name == pb:
        padB = p
if padA is None or padB is None:
    print(json.dumps({"swapped": False,
                      "error": f"pads {pa}/{pb} not found on {ref}"}))
    sys.exit(0)

na, nb = padA.GetNet(), padB.GetNet()
nca, ncb = padA.GetNetCode(), padB.GetNetCode()
if nca == ncb or not nca or not ncb:
    print(json.dumps({"swapped": False, "error": "same/empty net"}))
    sys.exit(0)

names = sorted({padA.GetNetname(), padB.GetNetname()})
ripped = 0
for it in list(b.GetTracks()):
    try:
        if it.GetNetCode() in (nca, ncb):
            b.Remove(it)
            ripped += 1
    except AttributeError:
        continue

padA.SetNet(nb)
padB.SetNet(na)
b.Save(pcb)
print(json.dumps({"swapped": True, "ripped": ripped, "nets": names}))
