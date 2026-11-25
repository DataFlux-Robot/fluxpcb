"""Assembly set: which lines are placed, which are not, and in what units.

A fab package is not "gerbers + a position file".  The factory needs to know
which BOM lines are SMT (they go in the CPL), which are through-hole or
hand-soldered (they do not), which are DNP, and how many panels/pieces/sets
were ordered.  `kicad-cli pcb export pos` writes every footprint with none of
that, so this module builds the assembly set from the BOM plus the position
file and keeps the three quantity units apart:

    panels × pieces_per_panel = finished pieces
    finished pieces / pieces_per_set = sets

A line whose assembly class cannot be read from the BOM or the footprint is
`unknown` and is reported, never silently put in the CPL.

    classify(bom) -> {'rows': [...], 'smt': [...], 'tht': [...], ...}
    cpl_rows(bom, positions_csv) -> [{'ref','val','package','x','y','rot','side'}]
    quantity(spec) -> {'panels','pieces_per_panel','pieces_per_set',
                       'finished_pieces','sets'}
    write(project_dir, fab_dir, bom, spec, positions_csv) -> summary
"""
from __future__ import annotations

import csv
import json
import os

SMT_HINTS = ('0402', '0603', '0805', '1206', '1210', 'sot', 'soic', 'tssop',
             'msop', 'ssop', 'qfn', 'qfp', 'lqfp', 'bga', 'dfn', 'smd',
             'sod', 'melf', 'plcc', 'soj')
THT_HINTS = ('tht', 'pth', 'dip', 'to-220', 'to220', 'to-92', 'to92',
             'pinheader', 'pin header', 'hdr', 'sip', 'axial', 'radial',
             'do-41', 'do-35', 'do-201', 'screw', 'terminal', 'conn-tht')


def _truthy(v):
    return str(v).strip().lower() in ('1', 'true', 'yes', 'y', 'dnp', 'x')


def classify_row(row):
    """smt / tht / manual / customer / dnp / unknown for one BOM line."""
    if _truthy(row.get('dnp')):
        return 'dnp'
    a = str(row.get('assembly') or row.get('assembly_class') or '').strip().lower()
    if a in ('dnp', 'none', 'exclude', 'not_placed', 'no'):
        return 'dnp'
    if a in ('manual', 'hand', 'hand_solder'):
        return 'manual'
    if a in ('customer', 'consigned', 'customer_supplied'):
        return 'customer'
    t = str(row.get('type') or '').strip().lower()
    text = ' '.join(str(row.get(k, '')) for k in
                    ('footprint', 'package', 'mpn', 'role', 'value')).lower()
    if t.startswith('tht') or any(h in text for h in THT_HINTS):
        return 'tht'
    if t.startswith('smt') or t.startswith('smd') or any(h in text for h in SMT_HINTS):
        return 'smt'
    if a in ('smt', 'smd'):
        return 'smt'
    if a in ('tht', 'through_hole'):
        return 'tht'
    return 'unknown'


def classify(bom):
    rows = []
    for r in bom or []:
        rows.append({'ref': r.get('ref'), 'class': classify_row(r),
                     'value': r.get('value'), 'footprint': r.get('footprint')})
    out = {'rows': rows}
    for k in ('smt', 'tht', 'manual', 'customer', 'dnp', 'unknown'):
        out[k] = [r['ref'] for r in rows if r['class'] == k]
    out['total'] = len(rows)
    return out


def _read_positions(path):
    if not path or not os.path.exists(path):
        return None
    with open(path, encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def cpl_rows(bom, positions_csv):
    """CPL = the SMT subset of the assembly set, with position data.

    Returns (rows, missing): `missing` names what kept a line out of the CPL
    (no position row, unknown class) so the package can say so.
    """
    pos = _read_positions(positions_csv)
    if pos is None:
        return [], ['positions.csv']
    by_ref = {}
    for p in pos:
        ref = p.get('Ref') or p.get('ref') or p.get('Reference')
        if ref:
            by_ref[ref] = p
    cls = {r['ref']: r['class'] for r in classify(bom)['rows']}
    rows, missing = [], []
    for r in bom or []:
        ref = r.get('ref')
        if cls.get(ref) != 'smt':
            continue
        p = by_ref.get(ref)
        if not p:
            missing.append(f'{ref}: no position row')
            continue
        rows.append({'ref': ref, 'val': r.get('value') or p.get('Val'),
                     'package': r.get('footprint') or p.get('Package'),
                     'x': p.get('PosX'), 'y': p.get('PosY'),
                     'rot': p.get('Rot'), 'side': p.get('Side')})
    return rows, missing


def quantity(spec):
    """panels / pieces per panel / pieces per set, disambiguated."""
    m = (spec or {}).get('manufacturing') or (spec or {}).get('quantity') or {}
    panels = int(m.get('panels') or 1)
    ppp = int(m.get('pieces_per_panel') or 1)
    pps = int(m.get('pieces_per_set') or 1)
    finished = panels * ppp
    return {'panels': panels, 'pieces_per_panel': ppp,
            'pieces_per_set': pps, 'finished_pieces': finished,
            'sets': finished // pps if pps else None}


def write(project_dir, fab_dir, bom, spec, positions_csv=None):
    """Write fab/cpl.csv and fab/assembly.json; return the summary."""
    os.makedirs(fab_dir, exist_ok=True)
    classes = classify(bom)
    q = quantity(spec)
    rows, missing = cpl_rows(bom, positions_csv or os.path.join(fab_dir, 'positions.csv'))
    cpl_path = os.path.join(fab_dir, 'cpl.csv')
    with open(cpl_path, 'w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=['ref', 'val', 'package', 'x', 'y',
                                          'rot', 'side'])
        w.writeheader()
        w.writerows(rows)
    summary = {'status': 'ok' if not missing else 'partial',
               'classes': {k: len(v) for k, v in classes.items()
                           if isinstance(v, list)},
               'cpl_rows': len(rows), 'cpl': cpl_path,
               'missing': missing, 'quantity': q,
               'unknown_refs': classes['unknown']}
    with open(os.path.join(fab_dir, 'assembly.json'), 'w', encoding='utf-8') as f:
        json.dump({'classes': classes, 'quantity': q, 'cpl_rows': rows,
                   'missing': missing}, f, indent=1, ensure_ascii=False)
    summary['assembly'] = os.path.join(fab_dir, 'assembly.json')
    return summary
