# -*- coding: utf-8 -*-
"""SES (Specctra session) -> KiCad injector (K5 knowhow: AvatarSD/KiSkill
DSN/SES closed loop, charlesmmorais SES import).

Freerouting writes .ses even when its DSN batch-save path NPEs. The SES
format carries the routing as network-out lists with wire/polygon paths —
we parse those into KiCad segments/vias and inject via text (same proven
mechanism as dsn2kicad).
"""
from __future__ import annotations

import re
import uuid as uuidlib
from pathlib import Path


def uid():
    return str(uuidlib.uuid4())


def _f(v: float) -> str:
    s = f"{v:.6f}".rstrip("0").rstrip(".")
    return s if s else "0"


LAYER_MAP = {
    "top_copper": "F.Cu", "bottom_copper": "B.Cu",
    "inner1": "In1.Cu", "inner2": "In2.Cu",
    "inner3": "In3.Cu", "inner4": "In4.Cu",
    "In1.Cu": "In1.Cu", "In2.Cu": "In2.Cu",
    "In3.Cu": "In3.Cu", "In4.Cu": "In4.Cu",
}


def parse_ses(ses_path: Path) -> dict:
    """Extract per-net wire paths and vias from a Specctra session file."""
    txt = ses_path.read_text(encoding="utf-8", errors="replace")
    nets: dict[str, list] = {}
    vias: list[tuple] = []
    # session structure: (network_out ... (net <name> (wire (path ...)) (via ...)))
    # non-greedy per-net body: stop at the next (net ... or end
    for net_m in re.finditer(
            r"\(net\s+([^\s()]+)([\s\S]*?)(?=\(net\s|\Z)", txt):
        net_name = net_m.group(1).strip('"')
        body = net_m.group(0)
        paths = []
        for w in re.finditer(
                r"\(wire[^()]*\(path[^()]*\(layer\s+([^)\s]+)\)([^;]*?)\)\)", body):
            layer = LAYER_MAP.get(w.group(1), "F.Cu")
            pts = re.findall(r"\(\s*(-?[\d.]+)\s+(-?[\d.]+)", w.group(2))
            pts = [(float(a) / 1000, float(b) / 1000) for a, b in pts]
            if len(pts) >= 2:
                paths.append((layer, pts))
        if paths:
            nets[net_name] = paths
        for v in re.finditer(
                r"\(via[\s\S]{0,80}?\(\s*(-?[\d.]+)\s+(-?[\d.]+)\s*\)", body):
            vias.append((net_name, float(v.group(1)) / 1000,
                         float(v.group(2)) / 1000))
    return {"nets": nets, "vias": vias}


def inject_ses(ses_path: Path, pcb_path: Path) -> dict:
    """Parse SES and append segments/vias to the board (name-based nets)."""
    data = parse_ses(ses_path)
    segs_txt, vias_txt = [], []
    n_seg = 0
    for net, paths in data["nets"].items():
        for layer, pts in paths:
            for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
                if abs(x2 - x1) < 1e-4 and abs(y2 - y1) < 1e-4:
                    continue
                segs_txt.append(
                    f'\t(segment\n\t\t(start {_f(x1)} {_f(-y1)})\n'
                    f'\t\t(end {_f(x2)} {_f(-y2)})\n\t\t(width 0.2)\n'
                    f'\t\t(layer "{layer}")\n\t\t(net "{net}")\n'
                    f'\t\t(uuid "{uid()}"))')
                n_seg += 1
    n_via = 0
    for net, x, y in data["vias"]:
        vias_txt.append(
            f'\t(via\n\t\t(at {_f(x)} {_f(-y)})\n\t\t(size 0.6)\n'
            f'\t\t(drill 0.3)\n\t\t(layers "F.Cu" "B.Cu")\n'
            f'\t\t(net "{net}")\n\t\t(uuid "{uid()}"))')
        n_via += 1
    inject = "\n".join(segs_txt + vias_txt)
    if inject:
        board_txt = pcb_path.read_text(encoding="utf-8", errors="replace")
        board_txt = board_txt.rstrip()
        assert board_txt.endswith(")")
        pcb_path.write_text(board_txt[:-1] + inject + "\n)\n", encoding="utf-8")
    return {"segments": n_seg, "vias": n_via, "nets": len(data["nets"])}
