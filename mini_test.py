# -*- coding: utf-8 -*-
"""Minimal repro: 2-pad footprint + 1 segment joining pad centers -> DRC."""
import subprocess
from pathlib import Path

from fluxpcb.kicadgen.pcb import Board, Fp, parse, render, uid, _f

KICAD = r"C:\Users\zhang\AppData\Local\Programs\KiCad\10.0\bin\kicad-cli.exe"
D = Path("mini_test")
D.mkdir(exist_ok=True)

b = Board("mini", 20.0, 10.0, ["A"])
b.place(Fp("Resistor_SMD", "R_0603_1608Metric", "R1", "0R", 7.0, 5.0,
           pad_nets={"1": "A", "2": "A"}))
# R0603 pads local: (+0.75,0) and (-0.75,0)
(D / "mini.kicad_pcb").write_text(b.to_text(), encoding="utf-8", newline="\n")
# append a segment joining the two pad centers directly (bypass dsn2kicad)
txt = (D / "mini.kicad_pcb").read_text(encoding="utf-8")
seg = (f'\t(segment (start 6.25 5) (end 7.75 5) (width 0.2) (layer "F.Cu") '
       f'(net 1 "A") (uuid "{uid()}"))\n')
txt = txt.rstrip()[:-1] + seg + ")\n"
# net refs on segments must be number-only per demo? demo had (net 1) on segment
seg2 = (f'\t(segment (start 6.25 5) (end 7.75 5) (width 0.2) (layer "F.Cu") '
        f'(net 1) (uuid "{uid()}"))\n')
txt = txt.replace(seg, seg2)
(D / "mini.kicad_pcb").write_text(txt, encoding="utf-8", newline="\n")
import json
(D / "mini.kicad_pro").write_text(json.dumps({"board": {"design_settings": {}}}),
                                  encoding="utf-8")
p = subprocess.run([KICAD, "pcb", "drc", "--exit-code-violations",
                    "-o", str(D / "r.rpt"), str(D / "mini.kicad_pcb")],
                   capture_output=True, text=True, timeout=120)
print(p.returncode, (p.stdout + p.stderr)[:120])
print((D / "r.rpt").read_text(encoding="utf-8", errors="replace")[:800])
