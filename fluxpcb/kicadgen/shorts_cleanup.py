# Violation scrubber (runs inside KiCad Python): removes tracks/vias named
# in shorting / clearance / copper_edge_clearance DRC entries, and dedupes
# stacked vias. Pads are never removed — the routing loop re-closes pairs.
import re
import sys
import pcbnew

PCB = sys.argv[1]
RPT = sys.argv[2]
board = pcbnew.LoadBoard(PCB)
mm = 1000000

with open(RPT, errors="replace") as f:
    rpt = f.read()

# (kind, netA, netB, x1, y1, x2, y2) — both @-lines of each entry
entries = []
for m in re.finditer(
        r"\[(shorting_items|clearance|copper_edge_clearance|holes_co_located)\][^\n]*\n"
        r"(?:[^\n]*\n)?\s*@\(([\d.]+) mm, ([\d.]+) mm\): ([^\n]+)\n"
        r"\s*@\(([\d.]+) mm, ([\d.]+) mm\): ([^\n]+)", rpt):
    kind, x1, y1, d1, x2, y2, d2 = m.groups()
    nets = []
    for d in (d1, d2):
        n = re.search(r"\[(\S+)\]", d)
        nets.append(n.group(1) if n else "")
    entries.append((kind, nets[0], nets[1], float(x1), float(y1),
                    float(x2), float(y2)))

to_remove = []
removed_marks = set()
for kind, net1, net2, x1, y1, x2, y2 in entries:
    want = {n for n in (net1, net2) if n and not n.startswith("Rectangle")}
    best = None          # (dist, item) — remove ONLY the closest item, not
    best_d = 0.65        # everything within 2.5mm (the old blast radius
    for item in board.GetTracks():   # destroyed neighboring load-bearing
        try:                          # vias and made ratsnest WORSE)
            mark = (id(item))
            if mark in removed_marks:
                continue
            if item.Type() == pcbnew.PCB_VIA_T:
                if want and item.GetNetname() not in want:
                    continue
                p = item.GetPosition()
                d = min(abs(p.x / mm - x1) + abs(p.y / mm - y1),
                        abs(p.x / mm - x2) + abs(p.y / mm - y2))
                if d < best_d:
                    best_d, best = d, item
                continue
            tn = item.GetNetname()
            if want and tn not in want:
                continue
            s, e = item.GetStart(), item.GetEnd()
            s_x, s_y = s.x / mm, s.y / mm
            e_x, e_y = e.x / mm, e.y / mm
            d1 = min(abs(s_x - x1) + abs(s_y - y1), abs(e_x - x1) + abs(e_y - y1))
            d2 = min(abs(s_x - x2) + abs(s_y - y2), abs(e_x - x2) + abs(e_y - y2))
            mx, my = (s_x + e_x) / 2, (e_y + s_y) / 2
            d3 = min(abs(mx - x1) + abs(my - y1), abs(mx - x2) + abs(my - y2))
            d = min(d1, d2, d3)
            if d < best_d:
                best_d, best = d, item
        except AttributeError:
            continue
    if best is not None:
        to_remove.append(best)
        removed_marks.add(id(best))

for item in to_remove:
    board.Remove(item)

# dedupe stacked vias (same net, <0.1mm apart) — they trip holes_co_located
vias = {}
dedup = 0
for item in board.GetTracks():
    if item.Type() != pcbnew.PCB_VIA_T:
        continue
    try:
        p = item.GetPosition()
        key = (item.GetNetname(), round(p.x / 100000.0), round(p.y / 100000.0))
    except AttributeError:
        continue
    if key in vias:
        to_remove.append(item)
        dedup += 1
    else:
        vias[key] = item
for item in to_remove:
    try:
        board.Remove(item)
    except Exception:
        pass

board.Save(PCB)
print("VIOLATIONS_REMOVED={} VIA_DEDUP={}".format(len(to_remove) - dedup, dedup))
