# -*- coding: utf-8 -*-
"""short_repair — DRC short-item repair (M-round M3, kicad-tools pattern).

The PREVENTION side lives in commit_gate (routers never emit cross-net
shorts anymore); this is the FALLBACK for shorts that still slip through
legacy copper, injection paths or hand edits. Pattern from
rjwalters/kicad-tools different_net_short.py:

  parse DRC shorting pairs -> participation count per net -> repair by
  type (micro-stub: delete the higher-participation item; via: try a
  candidate-ladder relocate first, delete if boxed in) -> nets whose
  copper was removed are reported for local reroute (the caller routes
  them via the surgical reroute_nets).

Runs as its own process (KiCad 10 SWIG containers poison after churn).
Usage:  short_repair.py PCB RPT
Prints one JSON line: {removed, relocated, nets_for_reroute, left}
"""
import json
import re
import sys

import pcbnew

MM = 1000000
STUB_LEN = 0.3           # items shorter than this are micro-stubs
TOL = 0.08               # item position match tolerance (mm)

pcb, rpt = sys.argv[1], sys.argv[2]
b = pcbnew.LoadBoard(pcb)

# ------------------------------------------------------------- parse
txt = open(rpt, encoding="utf-8", errors="replace").read()
entries = []
pat = re.compile(
    r"网络 (\S+) 和 (\S+)\)[\s\S]{0,200}?"
    r"@\(([\d.]+) mm, ([\d.]+) mm\): (Track|Via) \[\S+\] on (\S+?), "
    r"length ([\d.]+) mm")
for m in pat.finditer(txt):
    a, bb, x1, y1, kind1, lay1, len1 = m.groups()
    # the paired second item follows in the same block
    tail = txt[m.end():m.end() + 200]
    m2 = re.search(r"@\(([\d.]+) mm, ([\d.]+) mm\): (Track|Via) "
                   r"\[(\S+)\] on (\S+?), length ([\d.]+) mm", tail)
    second = None
    if m2:
        second = (float(m2.group(1)), float(m2.group(2)), m2.group(3),
                  m2.group(4), float(m2.group(6)))
    entries.append({"nets": (a, bb),
                    "items": [(float(x1), float(y1), kind1,
                               float(len1))] +
                             ([(second[0], second[1], second[2],
                                second[4])] if second else []),
                    })
# dedup (KiCad reports each short twice, once per direction)
seen = set()
uniq = []
for e in entries:
    key = tuple(sorted(e["nets"])) + tuple(
        sorted(round(i[0], 2) for i in e["items"]))
    if key not in seen:
        seen.add(key)
        uniq.append(e)
entries = uniq

participation = {}
for e in entries:
    for n in e["nets"]:
        participation[n] = participation.get(n, 0) + 1


# ------------------------------------------------------------- find items
def find_item(x, y, kind, net):
    best, bd = None, TOL
    netobj = b.FindNet(net)
    nc = netobj.GetNetCode() if netobj else -1
    for it in b.GetTracks():
        try:
            if it.GetNetCode() != nc:
                continue
            if kind == "Via" and it.Type() != pcbnew.PCB_VIA_T:
                continue
            if kind == "Track" and it.Type() == pcbnew.PCB_VIA_T:
                continue
            q = it.GetPosition()
            d = max(abs(q.x / MM - x), abs(q.y / MM - y))
            if d < bd:
                best, bd = it, d
        except (AttributeError, TypeError):
            continue
    return best


def item_len(it):
    try:
        if it.Type() == pcbnew.PCB_VIA_T:
            return 0.0
        s, e = it.GetStart(), it.GetEnd()
        return ((s.x - e.x) ** 2 + (s.y - e.y) ** 2) ** 0.5 / MM
    except (AttributeError, TypeError):
        return 99.0


# ------------------------------------------------------------- repair
removed = relocated = 0
reroute = set()
removed_ids = set()

for e in entries:
    live = [i for i in e["items"]]
    if not live:
        continue
    # choose the victim: the item whose net participates in more shorts;
    # tie -> the shorter item (delete the stub, keep the long route)
    def score(i):
        (x, y, kind, ln) = i
        # find which net this item belongs to by geometry below; for
        # scoring use min distance to either reported net's items later
        return ln
    items = []
    for (x, y, kind, ln) in live:
        for net in e["nets"]:
            it = find_item(x, y, kind, net)
            if it is not None and it.m_Uuid.GetAsString() not in removed_ids:
                items.append((it, net, ln))
                break
    if not items:
        continue
    items.sort(key=lambda t: (participation.get(t[1], 0), -t[2]))
    victim, vnet, _ = items[0]
    # v1 strategy: DELETE the victim (via relocation would tear the
    # track endpoints attached to it); the victim's net goes to the
    # reroute list and the surgical reroute_nets pass re-closes it
    try:
        removed_ids.add(victim.m_Uuid.GetAsString())
        b.Remove(victim)
        removed += 1
        reroute.add(vnet)
    except (AttributeError, TypeError):
        continue

b.Save(pcb)
print(json.dumps({"pairs": len(entries), "removed": removed,
                  "relocated": relocated,
                  "nets_for_reroute": sorted(reroute)},
                 ensure_ascii=False))
