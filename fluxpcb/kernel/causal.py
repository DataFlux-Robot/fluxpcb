# -*- coding: utf-8 -*-
"""Board State Kernel — causal.py: failure taxonomy + upstream edges.

Facet 4 (the reverse edges). The old state machine was a one-way DAG:
routing failures could only trigger "route again harder", while the
true causes lived upstream (the capacity math proved all 41 signal
opens on fc are pin-assignment crossing conflicts, decided at
allocation time). This module classifies every residual open by GEOMETRY
into a cause class and maps each class to its upstream action —
failure states become legal transitions back into regeneration.

Classes (from the K7-K20 incident record + the escape-capacity study):
  escape-crossing  — one anchor inside a BGA field, the other outside:
                     pin-assignment planarity conflict
  power-fragment   — rail net fragmented by zone refill/carving
  placement-blocked— route_diag reports footprint blockers
  local-congestion — both anchors close together (channel squeeze)
"""
from __future__ import annotations

import re
from pathlib import Path

POWER_RE = re.compile(
    r"^(3V3|5V|VDD|VCC|VBUS|VSYS|VBAT|GND|AGND|PGND)", re.I)


def _parse_pairs(pairs_lines: list[str]):
    """net|x1|y1|l1|x2|y2|l2 -> [(net, (x1,y1), (x2,y2)), ...]"""
    out = []
    for l in pairs_lines:
        parts = l.strip().split("|")
        if len(parts) < 7:
            continue
        try:
            out.append((parts[0],
                        (float(parts[1]), float(parts[2])),
                        (float(parts[4]), float(parts[5]))))
        except ValueError:
            continue    # ok-silent: 坏行跳过, 归因表尽力而为
    return out


def bga_boxes_from_pairs(pairs, min_span: float = 8.0) -> list:
    """Cluster anchor points into dense boxes — a 240-ball BGA field
    manifests as many anchors packed in a ~12mm region. Host-side
    heuristic (no KiCad dependency): mark 4mm cells holding >= 4
    anchors, merge ADJACENT dense cells into connected components (a
    real BGA spans several cells), keep components with >= 8 anchors
    and sufficient span."""
    CELL = 4.0
    cells = {}
    for _row in pairs:
        if isinstance(_row, str):
            _parts = _row.split("|")
            if len(_parts) < 7:
                continue
            _net = _parts[0]
            a = (float(_parts[1]), float(_parts[2]))
            b = (float(_parts[4]), float(_parts[5]))
        else:
            _net, a, b = _row
        for p in (a, b):
            cells.setdefault((int(p[0] / CELL), int(p[1] / CELL)),
                             []).append(p)
    dense = {c: pts for c, pts in cells.items() if len(pts) >= 4}
    # connected components over 8-neighborhood
    comps = []
    seen = set()
    for c in dense:
        if c in seen:
            continue
        stack, comp = [c], []
        seen.add(c)
        while stack:
            cur = stack.pop()
            comp.append(cur)
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    n = (cur[0] + dx, cur[1] + dy)
                    if n in dense and n not in seen:
                        seen.add(n)
                        stack.append(n)
        comps.append(comp)
    boxes = []
    for comp in comps:
        pts = [p for c in comp for p in dense[c]]
        if len(pts) < 8:
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        x0, x1 = min(xs) - CELL, max(xs) + CELL
        y0, y1 = min(ys) - CELL, max(ys) + CELL
        if (x1 - x0) >= min_span and (y1 - y0) >= min_span:
            boxes.append((x0, y0, x1, y1))
    return boxes


def _in(box, p) -> bool:
    return box[0] <= p[0] <= box[2] and box[1] <= p[1] <= box[3]


def classify(pairs_lines: list[str], diag_verdicts: list | None = None,
             local_mm: float = 8.0) -> dict:
    """-> {class: [nets]}; each class maps to an upstream action in
    ACTION_FOR (the driver consumes this directly)."""
    pairs = _parse_pairs(pairs_lines)
    boxes = bga_boxes_from_pairs(pairs)
    diag_verdicts = diag_verdicts or []
    out = {"escape-crossing": [], "power-fragment": [],
           "placement-blocked": [], "local-congestion": [],
           "unclassified": []}
    blocked_nets = {v.get("net")
                    for v in diag_verdicts
                    if v.get("blockers")}
    for net, a, b in pairs:
        if POWER_RE.match(net):
            out["power-fragment"].append(net)
            continue
        in_bga = [a, b], boxes
        a_in = any(_in(bx, a) for bx in boxes)
        b_in = any(_in(bx, b) for bx in boxes)
        if a_in != b_in:
            out["escape-crossing"].append(net)
            continue
        if net in blocked_nets:
            out["placement-blocked"].append(net)
            continue
        if ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5 < local_mm:
            out["local-congestion"].append(net)
            continue
        out["unclassified"].append(net)
    return {k: sorted(set(v)) for k, v in out.items() if v}


# the reverse edges: failure class -> the upstream move that removes
# the CAUSE (not the symptom)
ACTION_FOR = {
    # ①: 扇出重构是 escape-crossing 的结构解;
    # reassign_pins 降为 intel 里的备选(换球位)
    "escape-crossing": "fanout_repair",
    "power-fragment": "power_stitch",
    "placement-blocked": "adjust_placement",
    "local-congestion": "reroute_nets",
    "unclassified": "reroute_nets",
}
