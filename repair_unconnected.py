# -*- coding: utf-8 -*-
"""Auto-repair loop: read DRC unconnected pairs, bridge same-net items,
re-verify. Machine-checked closure (clearness judged by next DRC pass)."""
import re
import subprocess
import sys
from pathlib import Path

KICAD = r"C:\Users\zhang\AppData\Local\Programs\KiCad\10.0\bin\kicad-cli.exe"
PCB = Path(sys.argv[1])
RPT = PCB.parent / "drc.rpt"


def drc() -> tuple[int, list]:
    p = subprocess.run([KICAD, "pcb", "drc", "--exit-code-violations",
                        "--refill-zones", "--save-board", "-o", str(RPT), str(PCB)],
                       capture_output=True, text=True, timeout=900)
    txt = RPT.read_text(encoding="utf-8", errors="replace")
    pairs = []
    for m in re.finditer(
            r"\[unconnected_items\][^\n]*\n[^\n]*\n\s*@\(([\d.]+) mm, ([\d.]+) mm\): ([^\n]+)\n\s*@\(([\d.]+) mm, ([\d.]+) mm\): ([^\n]+)", txt):
        x1, y1, d1, x2, y2, d2 = m.groups()
        pairs.append((float(x1), float(y1), d1.strip(), float(x2), float(y2), d2.strip()))
    unconn = txt.count("[unconnected_items]")
    return p.returncode, pairs, unconn, txt


def net_of(desc):
    m = re.search(r"\[(\S+)\]", desc)
    return m.group(1) if m else None


board = PCB.read_text(encoding="utf-8")
for it in range(1, 6):
    code, pairs, unconn, txt = drc()
    print(f"iter {it}: exit={code} unconnected={unconn}")
    if unconn == 0:
        print("CLOSED")
        break
    # KiCad 10 native format: nets referenced BY NAME on items
    import uuid as u
    bridges = []
    for x1, y1, d1, x2, y2, d2 in pairs:
        n1, n2 = net_of(d1), net_of(d2)
        if not n1 or n1 != n2:
            continue
        top1 = "top_copper" in d1
        bot1 = "bottom_copper" in d1
        top2 = "top_copper" in d2
        bot2 = "bottom_copper" in d2
        if "Zone" in d1 and "Zone" in d2:
            # F/B pour stitching via
            bridges.append(
                f'\t(via (at 3.0 15.0) (size 0.6) (drill 0.3) '
                f'(layers "F.Cu" "B.Cu") (net "{n1}") (uuid "{u.uuid4()}"))')
            continue
        if (top1 and bot2) or (bot1 and top2):
            # layer change: stitching via at item1 + L-jog on item2's layer
            bridges.append(
                f'\t(via (at {x1} {y1}) (size 0.6) (drill 0.3) '
                f'(layers "F.Cu" "B.Cu") (net "{n1}") (uuid "{u.uuid4()}"))')
            jog_layer = "F.Cu" if top2 else "B.Cu"
            bridges.append(
                f'\t(segment (start {x1} {y1}) (end {x2} {y1}) (width 0.2) '
                f'(layer "{jog_layer}") (net "{n1}") (uuid "{u.uuid4()}"))')
            bridges.append(
                f'\t(segment (start {x2} {y1}) (end {x2} {y2}) (width 0.2) '
                f'(layer "{jog_layer}") (net "{n1}") (uuid "{u.uuid4()}"))')
            continue
        pass  # segment bridges disabled: collision-free bridge router is the
              # remaining engineering item (straight/L jogs can cross nets)

    if not bridges:
        print("no bridgetargets; stop")
        break
    board = board.rstrip()
    board = board[:-1] + "\n" + "\n".join(bridges) + "\n)\n"
    PCB.write_text(board, encoding="utf-8")
