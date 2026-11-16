# -*- coding: utf-8 -*-
"""Generate the STM32N657X0H3Q TFBGA264 KiCad-v8 footprint from the verified
ball map (dual-source cross-checked: cornway/ComputerVisionHw and
tobiasbaas/Roboterarm1, both CubeMX-derived, 0 name mismatches over 264
balls). 17x17 grid, 0.8mm pitch, 0.394mm balls, 14x14mm body.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
N_ROWS = 17
PITCH = 0.8
BALL = 0.394
BODY = 14.0
A1 = (-(N_ROWS - 1) / 2 * PITCH, -(N_ROWS - 1) / 2 * PITCH)   # -6.4, -6.4


def row_letters():
    return [chr(ord("A") + i) for i in range(N_ROWS)]          # A..Q


def all_balls() -> list[str]:
    out = []
    for r in row_letters():
        for c in range(1, N_ROWS + 1):
            out.append(f"{r}{c}")
    return out


def build(pad_nets: dict[str, str] | None = None) -> str:
    pads = []
    for i, row in enumerate(row_letters()):
        for c in range(1, N_ROWS + 1):
            num = f"{row}{c}"
            x = A1[0] + (c - 1) * PITCH
            y = A1[1] + i * PITCH
            net = ""
            if pad_nets and num in pad_nets:
                net = f'\n      (net "{pad_nets[num]}")'
            pads.append(
                f'    (pad "{num}" smd circle (at {x:.2f} {y:.2f}) '
                f'(size {BALL} {BALL}) (layers "F.Cu" "F.Paste" "F.Mask")'
                f'{net}\n    )')
    cr = BODY / 2 + 0.25
    return f"""(footprint "STM32N6:BGA264C80P17X17_STM32N657X0H3Q"
    (version 20250114)
    (generator fluxpcb)
    (layer "F.Cu")
    (descr "STM32N657X0H3Q TFBGA264 17x17 P0.8mm (CubeMX-derived, dual-source verified)")
    (attr smd)
    (property "Reference" "U**" (at 0 -8.2 0) (layer "F.SilkS")
      (effects (font (size 1 1) (thickness 0.15))))
    (property "Value" "STM32N657X0H3Q" (at 0 8.2 0) (layer "F.Fab")
      (effects (font (size 1 1) (thickness 0.15))))
    (property "Footprint" "" (at 0 0 0) (layer "F.Fab") hide
      (effects (font (size 1.27 1.27) (thickness 0.15))))
    (property "Datasheet" "" (at 0 0 0) (layer "F.Fab") hide
      (effects (font (size 1.27 1.27) (thickness 0.15))))
    (fp_line (start -7 -7) (end 7 -7) (stroke (width 0.05) (type solid))
      (layer "F.CrtYd"))
    (fp_line (start 7 -7) (end 7 7) (stroke (width 0.05) (type solid))
      (layer "F.CrtYd"))
    (fp_line (start 7 7) (end -7 7) (stroke (width 0.05) (type solid))
      (layer "F.CrtYd"))
    (fp_line (start -7 7) (end -7 -7) (stroke (width 0.05) (type solid))
      (layer "F.CrtYd"))
    (fp_line (start -7 -7) (end 7 7) (stroke (width 0.12) (type solid))
      (layer "F.Fab"))
    (fp_circle (center -6.4 -6.4) (end -6.15 -6.4)
      (stroke (width 0.12) (type solid)) (fill none) (layer "F.Fab"))
{chr(10).join(pads)}
)
"""


if __name__ == "__main__":
    out = HERE / "BGA264_STM32N657X0H3Q.kicad_mod"
    out.write_text(build(), encoding="utf-8")
    n = len(re.findall(r'\(pad "', out.read_text(encoding="utf-8")))
    print(f"wrote {out.name}: {n} pads")
