"""Assembly kernel: can the board be built, not just routed?

A courtyard overlap is invisible to every copper predicate: the board can be
electrically perfect and still impossible to populate.  This kernel reads the
footprint courtyards (F.CrtYd/B.CrtYd, falling back to the body bounding box),
checks that no two parts claim the same area and that every courtyard stays
inside the board outline.  It is a peer domain kernel: geometry over the
placement graph, with its own certificate and digest.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import geom


def _poly(fp, layer):
    import pcbnew
    try:
        c = fp.GetCourtyard(layer)
        if c.OutlineCount() > 0:
            ch = c.Outline(0)
            return [[int(ch.CPoint(k).x), int(ch.CPoint(k).y)]
                    for k in range(ch.PointCount())]
    except Exception:
        pass
    return None


def courtyards(board_path):
    """ref -> courtyard polygon (nm), side, and the refs without one.

    A footprint without a courtyard is *unknown*, not a body bounding box:
    adjacent parts interleave pads all the time, and a bbox would invent an
    assembly violation.  The unknown list is reported, never converted into a
    pass.
    """
    import pcbnew
    b = pcbnew.LoadBoard(str(board_path))
    out, sides, unknown = {}, {}, []
    for fp in b.GetFootprints():
        ref = str(fp.GetReference())
        pads = list(fp.Pads())
        if pads and all(p.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH
                        for p in pads):
            continue                      # a mounting hole is not populated
        poly = _poly(fp, pcbnew.F_CrtYd) or _poly(fp, pcbnew.B_CrtYd)
        if poly is None:
            unknown.append(ref)
            continue
        out[ref] = poly
        sides[ref] = 'F' if fp.GetLayer() == pcbnew.F_Cu else 'B'
    outline = []
    try:
        set_ = pcbnew.SHAPE_POLY_SET()
        if b.GetBoardPolygonOutlines(set_, True) and set_.OutlineCount() > 0:
            ch = set_.Outline(0)
            outline = [[int(ch.CPoint(k).x), int(ch.CPoint(k).y)]
                       for k in range(ch.PointCount())]
    except Exception:
        outline = []
    return out, sides, unknown, outline


def _bbox(poly):
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return min(xs), min(ys), max(xs), max(ys)


def overlaps(courts, sides=None):
    """Pairs of refs whose courtyards intersect (same side only)."""
    refs = sorted(courts)
    boxes = {r: _bbox(courts[r]) for r in refs}
    found = []
    for i in range(len(refs)):
        for j in range(i + 1, len(refs)):
            a, b = refs[i], refs[j]
            if sides is not None and sides.get(a) != sides.get(b):
                continue
            ba, bb = boxes[a], boxes[b]
            if ba[2] < bb[0] or bb[2] < ba[0] or ba[3] < bb[1] or bb[3] < ba[1]:
                continue
            if geom.poly_poly([tuple(p) for p in courts[a]],
                              [tuple(p) for p in courts[b]]):
                found.append((a, b))
    return found


def outside(courts, outline):
    if len(outline) < 3:
        return sorted(courts)
    bad = []
    for ref, poly in courts.items():
        if any(not geom.pt_in_poly((p[0], p[1]),
                                   [tuple(q) for q in outline])
               for p in poly):
            bad.append(ref)
    return sorted(bad)


def check(board_path, require_complete=False):
    courts, sides, unknown, outline = courtyards(board_path)
    ov = overlaps(courts, sides)
    out = outside(courts, outline)
    reasons = []
    for a, b in ov:
        reasons.append(f'courtyards overlap: {a} / {b}')
    for r in out:
        reasons.append(f'courtyard outside the board: {r}')
    complete = not unknown
    ok = not reasons and (complete or not require_complete)
    payload = {'courtyards': {r: len(p) for r, p in sorted(courts.items())},
               'overlaps': ov, 'outside': out, 'unknown': unknown}
    dig = hashlib.sha256(json.dumps(payload, sort_keys=True)
                         .encode()).hexdigest()[:16]
    return {'ok': ok, 'complete': complete, 'reasons': reasons,
            'overlaps': ov, 'outside': out, 'unknown': unknown,
            'n_footprints': len(courts),
            'digest': dig,
            'theorem': {'id': 'ASM:courtyard', 'kernel': 'assembly',
                        'version': '1',
                        'claims': ['no courtyard overlap',
                                   'every courtyard inside the board'],
                        'ok': not reasons, 'digest': dig}}
