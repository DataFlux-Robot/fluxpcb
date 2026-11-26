"""The process box: manufacturability as a statement over a window, not a point.

A nominal clearance pass says "the copper as drawn is legal".  A board is
manufacturable when every perturbation in the fab's process window is still
legal.  The box below moves each copper edge by registration + plating and
shrinks each feature by etch; drill positions move by drill_offset.  Every
predicate then yields a *margin* (nm), not a boolean: margin >= 0 means the
whole box is safe for that predicate.

The box is a declared model, not a promise from the fab: it is recorded with
its provenance in the certificate, so the assumption is auditable and can be
replaced by measured capability data without changing any predicate.
"""
from __future__ import annotations

from ..kernels import fab

BOXES = {
    # Declared window for a 1 oz / 2-4 layer prototype process.  Each entry
    # is the physical mechanism that moves that predicate:
    #   registration: layer-to-layer misregistration (annular, drill-to-copper)
    #   drill_offset: hole position error (annular, hole-to-hole)
    #   etch:         copper edge pull-back per edge (width, annular, and it
    #                 *opens* a same-layer gap)
    #   rout:         board-edge position error (copper-to-edge)
    'jlc-1oz': {'registration': 75000, 'drill_offset': 50000,
                'etch': 10000, 'rout': 50000},
    'tight': {'registration': 25000, 'drill_offset': 20000,
              'etch': 8000, 'rout': 25000},
}
NOTES = {
    'jlc-1oz': 'declared conservative prototype window (not a JLC guarantee); '
               'replace with measured capability data for production',
    'tight': 'declared tight window for high-reliability runs',
}


def _entry(margin, witness, rule):
    return {'margin_nm': int(margin), 'witness': witness, 'rule_nm': int(rule)}


def margins(st, rules, box='jlc-1oz'):
    """Per-predicate worst-case margins over the process box (nm)."""
    if isinstance(box, str):
        if box not in BOXES:
            raise ValueError(f'unknown process box {box}')
        params, name = BOXES[box], box
    else:
        params, name = box, 'custom'
    reg = params['registration']
    drill = params['drill_offset']
    etch = params['etch']
    rout = params['rout']
    out, reasons = {}, []
    zones = st.get('zones', 0)

    rule = rules.get('clearance')
    if rule:
        if zones:
            reasons.append('clearance: zone copper is not extracted')
        else:
            g = fab.clearance_min_gap(st)
            if g is None:
                reasons.append('clearance: no different-net pair')
            else:
                # etch pulls both facing edges back: the gap opens
                m = g[0] + 2 * etch - rule
                out['clearance'] = _entry(m, f'{g[1]} vs {g[2]}: gap {g[0]} nm',
                                          rule)
    rule = rules.get('track_min')
    if rule:
        w = fab.width_min_value(st)
        if w is not None:
            out['width'] = _entry(w[0] - rule - 2 * etch,
                                  f'{w[1]}: width {w[0]} nm', rule)
    rule = rules.get('annular')
    if rule:
        a = fab.annular_min_value(st)
        if a is not None:
            out['annular'] = _entry(
                a[0] - rule - drill - reg - etch,
                f'{a[1]}: annular {a[0]} nm', rule)
    rule = rules.get('hole_to_hole')
    if rule:
        h = fab.hole_min_gap(st)
        if h is not None:
            out['hole_to_hole'] = _entry(
                h[0] - rule - 2 * drill,
                f'{h[1]} vs {h[2]}: hole gap {h[0]} nm', rule)
    rule = rules.get('edge')
    if rule:
        if zones:
            reasons.append('edge: zone copper is not extracted')
        e = fab.edge_min_gap(st)
        if e is not None:
            out['edge'] = _entry(e[0] - rule - rout,
                                 f'{e[1]}: edge gap {e[0]} nm', rule)
    for pred, m in out.items():
        if m['margin_nm'] < 0:
            reasons.append(f'{pred}: margin {m["margin_nm"]} nm < 0 '
                           f'({m["witness"]})')
    return {'ok': not reasons, 'reasons': reasons, 'box': name,
            'params': dict(params), 'note': NOTES.get(name, ''),
            'margins': out}


def robust_ok(report):
    return bool(report.get('ok'))
