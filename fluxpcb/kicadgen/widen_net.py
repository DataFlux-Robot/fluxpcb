# -*- coding: utf-8 -*-
"""widen_net — set track widths of named nets (T3, aiignite's
widen_net_traces action + the reroute_net.py FAT trick).

Usage:  widen_net.py PCB NETS WIDTH   (apply)
        widen_net.py PCB NETS --restore BACKUP.json
Prints one JSON line: {changed: n}
"""
import json
import sys
from pathlib import Path

import pcbnew

MM = 1000000
pcb = sys.argv[1]
nets = {s.strip() for s in sys.argv[2].split(",") if s.strip()}
arg3 = sys.argv[3] if len(sys.argv) > 3 else ""

b = pcbnew.LoadBoard(pcb)
changed = 0
if arg3 == "--restore":
    backup = json.loads(Path(sys.argv[4]).read_text(encoding="utf-8"))
    for it in b.GetTracks():
        try:
            if it.Type() == pcbnew.PCB_VIA_T:
                continue
            uid = it.m_Uuid.AsString()
            if uid in backup:
                it.SetWidth(int(backup[uid] * MM))
                changed += 1
        except (AttributeError, TypeError):
            continue
else:
    w = float(arg3 or 0.6)
    backup = {}
    bak_path = Path(str(pcb) + ".widths.json")
    if bak_path.exists():
        backup = json.loads(bak_path.read_text(encoding="utf-8"))
    for it in b.GetTracks():
        try:
            if it.Type() == pcbnew.PCB_VIA_T:
                continue
            if it.GetNetname() in nets:
                uid = it.m_Uuid.AsString()
                backup.setdefault(uid, it.GetWidth() / MM)
                it.SetWidth(int(w * MM))
                changed += 1
        except (AttributeError, TypeError):
            continue
    bak_path.write_text(json.dumps(backup), encoding="utf-8")
b.Save(pcb)
print(json.dumps({"changed": changed}))
