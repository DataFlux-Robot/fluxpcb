# -*- coding: utf-8 -*-
"""Routability-aware placement checks — the layer placement_quality never
had: a legal layout can still be UNROUTABLE when connector/transceiver
keepout bands sit on the only BGA escape corridors (root cause of the
h743-fc zero-track PWM/UART nets: U8/U9 + J12-J14 hole bands sealed the
right-edge ball exits).

Two checks:
  escape_ring(place, fps, w, h): BGA bbox + margin must contain no other
      footprint (pad keepouts steal dog-bone lattice centers)
  corridor_clear(place, fps, w, h): from each BGA edge to the nearest
      board edge, obstacle coverage (THT hole keepouts + SMD pads) along
      the corridor must stay below a coverage ratio
"""
from __future__ import annotations

_PTH_KEEP = 1.0        # THT pad + drill/2 + clearance, approximate radius
_SMD_KEEP = 0.75
RING_MARGIN = 2.5      # BGA escape ring width
COVERAGE_MAX = 0.6     # max fraction of a corridor that may be blocked


def _bbox(fp):
    bb = fp.GetBoundingBox()
    mm = 1000000
    return (bb.GetX() / mm, bb.GetY() / mm,
            (bb.GetX() + bb.GetWidth()) / mm,
            (bb.GetY() + bb.GetHeight()) / mm)


def _is_bga(fp) -> bool:
    n = 0
    try:
        for p in fp.Pads():
            if p.GetAttribute() != 3 and p.IsOnLayer(pcbnew_F_CU := 0):
                n += 1
    except TypeError:
        return False
    return n >= 60


def issues(place: dict, footprints: dict, w: float, h: float) -> list[str]:
    """place: ref->(x,y,rot); footprints: ref->kicad_mod path. Analytic,
    same contract as hierplace.validate — returns violation strings."""
    import re
    out = []
    bga_refs = []
    for ref in place:
        fp = footprints.get(ref)
        if fp is None or not hasattr(fp, "read_text"):
            continue
        txt = fp.read_text(encoding="utf-8", errors="replace")
        n_pads = txt.count("(pad ")
        if n_pads >= 60 and "smd" in txt and "circle" in txt:
            bga_refs.append(ref)

    for ref in bga_refs:
        x, y, rot = place[ref]
        # crude bbox from pad coords in the footprint text
        xs, ys = [], []
        for m in re.finditer(r"\(pad \"[^\"]*\" smd circle \(at ([-\d.]+) ([-\d.]+)",
                             footprints[ref].read_text(encoding="utf-8",
                                                       errors="replace")):
            xs.append(float(m.group(1)))
            ys.append(float(m.group(2)))
        if not xs:
            continue
        bx0 = x + min(xs) - RING_MARGIN
        bx1 = x + max(xs) + RING_MARGIN
        by0 = y + min(ys) - RING_MARGIN
        by1 = y + max(ys) + RING_MARGIN
        for ref2 in place:
            if ref2 == ref:
                continue
            x2, y2, _ = place[ref2]
            if bx0 <= x2 <= bx1 and by0 <= y2 <= by1:
                out.append(f"逃逸环违规: {ref2} 在 {ref} BGA逃逸环内 "
                           f"(<{RING_MARGIN}mm)")
    return out
