# -*- coding: utf-8 -*-
"""rip_nets — surgical executor: remove all tracks/vias of named nets.

Reference-project pattern (esp32s3_deskpet reroute_net.py): fix a net by
ripping exactly its copper and letting the incremental Freerouting pass
re-route it with everything else protected. Separate process (KiCad 10
SWIG containers poison after Remove churn).

Usage:  rip_nets.py PCB NET1,NET2,...
Prints one JSON line: {"ripped": n}
"""
import json
import sys

import pcbnew

pcb = sys.argv[1]
nets = {s.strip() for s in sys.argv[2].split(",") if s.strip()}
b = pcbnew.LoadBoard(pcb)

ripped = 0
for it in list(b.GetTracks()):
    try:
        if it.GetNetname() in nets:
            b.Remove(it)
            ripped += 1
    except AttributeError:
        continue
b.Save(pcb)
print(json.dumps({"ripped": ripped, "nets": sorted(nets)}))
