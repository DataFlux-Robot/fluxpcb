"""IPC-7351 land-pattern sanity: pad size against the nominal for the package.

The inspector used to flag a pad only when it was smaller than 0.2 mm, which
is a manufacturability floor, not a land-pattern check.  This module compares
the pad dimensions of the packages the BOM actually uses against IPC-7351
nominal density B, with a tolerance band, and says which package it does not
know (never a silent pass).

    check_footprint(name, pads) -> {'package','expected_mm','pads','deviations'}
    check_board(board, tol=0.25) -> {'checked','skipped','findings'}
"""
from __future__ import annotations

# nominal density B: (pad length, pad width) mm, from IPC-7351B tables for
# the common two-terminal and 3-pin packages
LAND = {
    '0201': (0.40, 0.30), '0402': (0.55, 0.50), '0603': (0.85, 0.80),
    '0805': (1.05, 1.00), '1206': (1.35, 1.15), '1210': (1.35, 1.30),
    'sot-23': (0.90, 0.60), 'sot-223': (2.00, 1.50),
    'soic-8': (1.55, 0.60), 'soic-14': (1.55, 0.60),
    'tssop-20': (1.35, 0.45), 'qfn-32': (0.80, 0.30),
}


def package_of(name):
    """'R_0603_1608Metric' -> '0603'; 'Package_TO_SOT_SMD:SOT-23' -> 'sot-23'."""
    n = (name or '').lower()
    for key in sorted(LAND, key=len, reverse=True):
        if key in n:
            return key
    return None


def check_footprint(name, pads, tol=0.25):
    """Compare the largest pad against the nominal; report the delta."""
    pkg = package_of(name)
    if pkg is None:
        return {'package': None, 'skipped': True, 'why': 'unknown package'}
    exp_l, exp_w = LAND[pkg]
    sizes = [(float(p[0]), float(p[1])) for p in pads if p and p[0] and p[1]]
    if not sizes:
        return {'package': pkg, 'skipped': True, 'why': 'no pads'}
    got_l = max(s[0] for s in sizes)
    got_w = max(s[1] for s in sizes)
    dl = (got_l - exp_l) / exp_l
    dw = (got_w - exp_w) / exp_w
    bad = abs(dl) > tol or abs(dw) > tol
    return {'package': pkg, 'expected_mm': [exp_l, exp_w],
            'got_mm': [round(got_l, 3), round(got_w, 3)],
            'deviation': [round(dl, 3), round(dw, 3)],
            'skipped': False, 'bad': bad,
            'why': ('pad length/width off IPC-7351 density B by more than '
                    f'{int(tol * 100)}%') if bad else 'within tolerance'}


def check_board(board, tol=0.25):
    """Every footprint with a known package; unknown ones are counted."""
    findings, checked, skipped = [], 0, []
    for fp in board.GetFootprints():
        pads = []
        for pad in fp.Pads():
            s = pad.GetSize()
            pads.append((s.x / 1e6, s.y / 1e6))
        r = check_footprint(fp.GetFPID().GetLibItemName() or fp.GetReference(),
                            pads, tol)
        if r.get('skipped'):
            skipped.append(fp.GetReference())
            continue
        checked += 1
        if r.get('bad'):
            findings.append({'rule': 'ipc7351_land_pattern',
                             'severity': 'warning',
                             'ref': fp.GetReference(),
                             'package': r['package'],
                             'expected_mm': r['expected_mm'],
                             'got_mm': r['got_mm'],
                             'description': (f"{fp.GetReference()}: {r['package']} "
                                             f"pad {r['got_mm']} vs nominal "
                                             f"{r['expected_mm']} mm")})
    return {'checked': checked, 'skipped': skipped, 'findings': findings}
