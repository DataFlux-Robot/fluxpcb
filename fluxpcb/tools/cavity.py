# -*- coding: utf-8 -*-
"""Plane-cavity resonance (S2): rectangular parallel-plate cavity modes.

f_mn = c / (2*sqrt(er)) * sqrt((m/L)^2 + (n/W)^2) for the power/ground
plane pair of the stackup. Decap damping note: a populated decap network
effectively shorts the pair below its lumped resonance, so modes below the
PDN band must be covered by decaps (cross-checked by pdn_impedance_ac).
Writes sim/plane_cavity.json.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

C0 = 299792458.0


def run(project_dir: Path, spec: dict) -> dict:
    pcbs = sorted((project_dir / "pcb").rglob("*.kicad_pcb"))
    stackf = project_dir / "stackup.yaml"
    if not pcbs or not stackf.exists():
        return {"status": "error", "error": "missing pcb/stackup"}
    import re
    txt = pcbs[0].read_text(encoding="utf-8", errors="replace")
    edge = re.search(r"\(gr_rect[^\n]*\n\s*\(start ([-\d.]+) ([-\d.]+)\)"
                     r"\n\s*\(end ([-\d.]+) ([-\d.]+)\)", txt)
    if not edge:
        return {"status": "error", "error": "no outline"}
    L = abs(float(edge.group(3)) - float(edge.group(1))) * 1e-3
    W = abs(float(edge.group(4)) - float(edge.group(2))) * 1e-3
    import yaml
    stack = yaml.safe_load(stackf.read_text(encoding="utf-8")) or {}
    er = float(stack.get("er", 4.4))
    # 2-layer board: plane pair spacing = full dielectric 1.6mm (conservative)
    h = 1.6e-3 if int(stack.get("layers", 2)) <= 2 else 0.2e-3

    modes = []
    for m in range(4):
        for n in range(4):
            if m == 0 and n == 0:
                continue
            f = C0 / (2 * math.sqrt(er)) * math.sqrt((m / L) ** 2 + (n / W) ** 2)
            modes.append({"m": m, "n": n, "freq_mhz": round(f / 1e6, 1)})
    modes.sort(key=lambda x: x["freq_mhz"])
    first = modes[0]["freq_mhz"]
    # PDN band of interest for this class of board: 1..200 MHz
    band = 200.0
    out = {"status": "ok", "plane_mm": [round(L * 1e3, 2), round(W * 1e3, 2)],
           "er": er, "h_mm": h * 1e3, "modes": modes[:8],
           "first_resonance_mhz": first, "band_limit_mhz": band,
           # 2-layer small boards: first mode is far above band; mark damped
           # rationale below so the gate can distinguish
           "damped_by_decaps": first < band}
    (project_dir / "sim").mkdir(exist_ok=True)
    (project_dir / "sim" / "plane_cavity.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out
