"""IPC-7351 land-pattern sanity: pad size against the per-package range.

The inspector used to flag a pad only when it was smaller than 0.2 mm, which
is a manufacturability floor, not a land-pattern check.  This module compares
the pads of the packages the BOM actually uses against a **range** per
package — IPC-7351 densities A..C plus the common library variants — because
a nominal single value flagged every library footprint on board 0626
(SOIC-8-N pads are 1.14 mm long, the reflow nominal is 1.55).  A range with
the long/short sides sorted also survives a rotated footprint.

Hand-soldering footprints are skipped: their pads are intentionally larger
than reflow density B, and comparing them produced 78 findings on 0626.

    check_footprint(name, pads) -> {'package','expected_mm','got_mm',...}
    check_board(board, strict=False) -> {'checked','skipped','findings'}
"""
from __future__ import annotations

# (min_length, max_length, min_width, max_width) mm, long side first
LAND = {
    '0201': (0.32, 0.48, 0.24, 0.36),
    '0402': (0.45, 0.65, 0.40, 0.60),
    '0603': (0.70, 1.05, 0.70, 0.95),
    '0805': (0.90, 1.30, 0.85, 1.15),
    '1206': (1.15, 1.65, 1.00, 1.40),
    '1210': (1.15, 1.65, 1.15, 1.55),
    'sot-23': (0.80, 1.10, 0.50, 0.85),
    'sot-223': (1.80, 2.30, 1.30, 1.70),
    'soic-8': (1.00, 2.10, 0.45, 0.75),
    'soic-14': (1.00, 2.10, 0.45, 0.75),
    'tssop-20': (0.90, 1.50, 0.35, 0.55),
    'qfn-32': (0.65, 0.95, 0.25, 0.40),
}


def package_of(name):
    """'R_0603_1608Metric' -> '0603'; 'Package_TO_SOT_SMD:SOT-23' -> 'sot-23'."""
    n = str(name or '').lower()
    for key in sorted(LAND, key=len, reverse=True):
        if key in n:
            return key
    return None


def check_footprint(name, pads, strict=False):
    """Compare every pad's long/short sides against the package range.

    `pads` is a list of (x, y) sizes in mm; the long side is max(x, y) so a
    rotated footprint is not misread.  `strict` narrows nothing — it is kept
    for callers that want to treat any miss as an error.
    """
    pkg = package_of(name)
    if pkg is None:
        return {'package': None, 'skipped': True, 'why': 'unknown package'}
    if 'hand' in str(name or '').lower():
        return {'package': pkg, 'skipped': True,
                'why': 'hand-soldering footprint (pads intentionally larger '
                       'than reflow density B)'}
    lo_l, hi_l, lo_w, hi_w = LAND[pkg]
    worst = None
    for (x, y) in pads or []:
        if not x or not y:
            continue
        long_side = max(float(x), float(y))
        short_side = min(float(x), float(y))
        if not (lo_l <= long_side <= hi_l and lo_w <= short_side <= hi_w):
            if worst is None or abs(long_side - hi_l) > abs(worst[0] - hi_l):
                worst = (long_side, short_side)
    if worst is None:
        return {'package': pkg, 'expected_mm': [lo_l, hi_l, lo_w, hi_w],
                'skipped': False, 'bad': False, 'why': 'within range'}
    return {'package': pkg, 'expected_mm': [lo_l, hi_l, lo_w, hi_w],
            'got_mm': [round(worst[0], 3), round(worst[1], 3)],
            'skipped': False, 'bad': True,
            'why': 'pad long/short side outside the IPC-7351 range'}


def check_board(board, strict=False):
    """Every footprint with a known package; unknown ones are counted."""
    findings, checked, skipped = [], 0, []
    for fp in board.GetFootprints():
        pads = []
        for pad in fp.Pads():
            s = pad.GetSize()
            pads.append((s.x / 1e6, s.y / 1e6))
        # pcbnew hands back UTF8 wrappers, not str: coerce before matching
        name = str(fp.GetFPID().GetLibItemName() or '') or str(fp.GetReference())
        r = check_footprint(name, pads, strict)
        if r.get('skipped'):
            skipped.append(str(fp.GetReference()))
            continue
        checked += 1
        if r.get('bad'):
            findings.append({'rule': 'ipc7351_land_pattern',
                             'severity': 'warning',
                             'ref': str(fp.GetReference()),
                             'package': r['package'],
                             'expected_mm': r['expected_mm'],
                             'got_mm': r['got_mm'],
                             'description': (f"{str(fp.GetReference())}: "
                                             f"{r['package']} pad {r['got_mm']} "
                                             f"outside {r['expected_mm']} mm")})
    return {'checked': checked, 'skipped': skipped, 'findings': findings}
