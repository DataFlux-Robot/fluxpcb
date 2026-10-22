# -*- coding: utf-8 -*-
"""Hierarchical placement validation (SKY-001-style decomposition flavor).

Analytic layout screen: bbox overlap / board edge / connector rules from
REAL footprint pad geometry — milliseconds, before any board generation
or DRC. Replacement for the 13-minute route-per-nudge loop.
"""
from __future__ import annotations

import math
import re
from pathlib import Path


def fp_bbox(fp_path: Path) -> tuple[float, float, float, float]:
    """(dx0, dy0, dx1, dy1) bbox OFFSETS from the footprint origin.

    From pad centers + one max pad radius + 0.3mm keepout. Handles both
    origin-at-center (BGA/SMD) and origin-at-pad1 (pin headers): offsets
    stay as-is, so pad1-origin parts yield asymmetric boxes near (0,0)."""
    txt = fp_path.read_text(encoding="utf-8", errors="replace")
    xs, ys, r = [], [], 0.3
    for m in re.finditer(
            r'\(\s*pad\s*"[^"]+"[^\n]*\n?\s*\(\s*at\s+([-\d.]+) ([-\d.]+)', txt):
        xs.append(float(m.group(1)))
        ys.append(float(m.group(2)))
    for m in re.finditer(r'\(\s*size\s+([\d.]+) ([\d.]+)\)', txt):
        r = max(r, max(float(m.group(1)), float(m.group(2))) / 2)
    if not xs:
        return (-0.5, -0.5, 0.5, 0.5)
    return (min(xs) - r, min(ys) - r, max(xs) + r, max(ys) + r)


def cluster_boxes(place: dict, footprints: dict) -> dict:
    """ref -> global bbox for every placed part (rotation-aware)."""
    out = {}
    for ref, (x, y, rot) in place.items():
        if ref not in footprints:
            out[ref] = (x - 1, y - 1, x + 1, y + 1)
            continue
        dx0, dy0, dx1, dy1 = fp_bbox(footprints[ref])
        a = math.radians(rot)
        ca, sa = math.cos(a), math.sin(a)
        corners = [(dx0, dy0), (dx1, dy0), (dx1, dy1), (dx0, dy1)]
        gx = [x + c[0] * ca + c[1] * sa for c in corners]
        gy = [y - c[0] * sa + c[1] * ca for c in corners]
        out[ref] = (min(gx), min(gy), max(gx), max(gy))
    return out


def validate(place: dict, footprints: dict,
             board_w: float, board_h: float,
             edge_keep: float = 0.3,
             connector_refs: tuple = ("J4",),
             overlap_eps_mm2: float = 0.05) -> list[str]:
    """Analytic layout validation — the fast pre-DRC screen.

    edge_keep matches the fab rule (0.3mm) plus a hair; overlap requires
    actual penetration (>overlap_eps) rather than bbox tangency."""
    issues: list[str] = []
    boxes = cluster_boxes(place, footprints)
    refs = sorted(boxes)

    for i, a in enumerate(refs):
        for b in refs[i + 1:]:
            A, B = boxes[a], boxes[b]
            ox = min(A[2], B[2]) - max(A[0], B[0])
            oy = min(A[3], B[3]) - max(A[1], B[1])
            if ox > 0.02 and oy > 0.02 and ox * oy > overlap_eps_mm2:
                issues.append(f"重叠: {a} <-> {b} ({ox:.1f}x{oy:.1f}mm)")

    for r in refs:
        x0, y0, x1, y1 = boxes[r]
        if x0 < edge_keep or y0 < edge_keep \
                or x1 > board_w - edge_keep or y1 > board_h - edge_keep:
            issues.append(f"边缘距离不足: {r} "
                          f"bbox=({x0:.1f},{y0:.1f})-({x1:.1f},{y1:.1f})")

    for r in connector_refs:
        if r in boxes:
            x0, y0, x1, y1 = boxes[r]
            d = min(x0, y0, board_w - x1, board_h - y1)
            if d > 4.0:
                issues.append(f"连接器 {r} 离板边 {d:.1f}mm (>4mm)")
    return issues
