# Remove tracks causing shorts (runs inside KiCad Python)
import re
import sys
import pcbnew

PCB = sys.argv[1]
RPT = sys.argv[2]
board = pcbnew.LoadBoard(PCB)
mm = 1000000

with open(RPT) as f:
    rpt = f.read()

shorts = []
for m in re.finditer(
        r"\[shorting_items\][^\n]*\n[^\n]*\n\s*@\(([\d.]+) mm, ([\d.]+) mm\): ([^\n]+)\n"
        r"\s*@\(([\d.]+) mm, ([\d.]+) mm\): ([^\n]+)", rpt):
    g = m.groups()
    d1, d2 = g[2].strip(), g[5].strip()
    n1 = re.search(r"\[(\S+)\]", d1)
    n2 = re.search(r"\[(\S+)\]", d2)
    net1 = n1.group(1) if n1 else ""
    net2 = n2.group(1) if n2 else ""
    x1, y1 = float(g[0]), float(g[1])
    x2, y2 = float(g[3]), float(g[4])
    layer = 1 if "bottom" in d1 else 0
    shorts.append((net1, net2, x1, y1, x2, y2, layer))

to_remove = []
for net1, net2, x1, y1, x2, y2, layer in shorts:
    lid = pcbnew.B_Cu if layer == 1 else pcbnew.F_Cu
    for track in board.GetTracks():
        if isinstance(track, pcbnew.PCB_VIA):
            continue
        if track.GetLayer() != lid:
            continue
        tn = track.GetNetname()
        if tn not in (net1, net2):
            continue
        try:
            s = track.GetStart()
            e = track.GetEnd()
            s_x, s_y = s.x / mm, s.y / mm
            e_x, e_y = e.x / mm, e.y / mm
        except (AttributeError, TypeError):
            continue
        d1 = min(abs(s_x - x1) + abs(s_y - y1), abs(e_x - x1) + abs(e_y - y1))
        d2 = min(abs(s_x - x2) + abs(s_y - y2), abs(e_x - x2) + abs(e_y - y2))
        mx, my = (s_x + e_x) / 2, (s_y + e_y) / 2
        d3 = abs(mx - x1) + abs(my - y1)
        if min(d1, d2, d3) < 2.0:
            to_remove.append(track)
            break

for track in to_remove:
    board.Remove(track)

board.Save(PCB)
print("SHORTS_REMOVED={}".format(len(to_remove)))
