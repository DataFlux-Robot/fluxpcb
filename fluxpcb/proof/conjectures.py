"""Requirements as conjectures: the spec compiler.

Each formalizable requirement becomes a claim the gate must discharge; each
requirement the spec cannot formalize is reported as unformalized rather than
silently ignored.  Geometry-dependent claims read the board state, so the same
conjecture goes from `unknown` (no copper yet) to `derived` (routed) without
being rewritten — that is what keeps proofs alive across refactors.

Spec shape (all sections optional)::

    {"rails": [{"name": "+3V3", "current_a": 0.3, "v_rail": 3.3,
                "drop_limit_mv": 100, "net": "+3V3"}],
     "parts": [{"ref": "U2", "power_w": 0.5, "theta_ja": 60, "tj_max": 125}],
     "interfaces": [{"name": "USB", "net": "D+", "width_mm": 0.2,
                     "h_mm": 0.2, "er": 4.2, "target_ohm": 90,
                     "tol_pct": 10}],
     "requirements": [{"id": "R1", "text": "2-layer, 1.6 mm"}]}
"""
from __future__ import annotations

import math


def net_geometry(st, net):
    """(total copper length mm, narrowest width mm) for a net, from the state."""
    length_nm, width_nm = 0, None
    for it in st['items']:
        if it.get('net') != net or 'ref' in it:
            continue
        sh = it['shape']
        if sh['kind'] != 'seg':
            continue
        dx = sh['x2'] - sh['x1']
        dy = sh['y2'] - sh['y1']
        length_nm += int(round(math.hypot(dx, dy)))
        w = sh.get('w')
        if w:
            width_nm = w if width_nm is None else min(width_nm, w)
    return (length_nm / 1e6, width_nm / 1e6 if width_nm else None)


def from_spec(spec, st):
    """Compile the spec into (claims, unformalized)."""
    claims, unformalized = [], []
    rails = spec.get('rails')
    if not rails and isinstance(spec.get('power'), dict):
        rails = spec['power'].get('rails')
    for r in rails or []:
        net = r.get('net') or r['name']
        length_mm, width_mm = net_geometry(st, net)
        if r.get('path'):
            params = {'net': net, 'current_a': r.get('current_a'),
                      'v_rail': r.get('v_rail'), 'source': r.get('source'),
                      'sinks': r.get('sinks')}
            pred = 'power.drop_path'
        else:
            params = {'current_a': r.get('current_a'),
                      'v_rail': r.get('v_rail'),
                      'length_mm': length_mm, 'width_mm': width_mm}
            pred = 'power.drop'
        c = {'id': f"POWER:DROP:{r['name']}", 'pred': pred,
             'net': net, 'params': params}
        if r.get('drop_limit_mv') is not None:
            c['limit'] = r['drop_limit_mv']
        claims.append(c)
    stack = spec.get('stackup') or {}
    for f in spec.get('parts', []) or []:
        if not isinstance(f, dict):
            continue          # a bare ref is a presence obligation (spec.py)
        params = {'power_w': f.get('power_w'), 'theta_ja': f.get('theta_ja'),
                  'theta_jc': f.get('theta_jc'), 'theta_cs': f.get('theta_cs'),
                  'theta_sa': f.get('theta_sa'), 't_amb': f.get('t_amb', 25.0),
                  'sink': f.get('sink', False),
                  'n_vias': f.get('n_vias'),
                  'via_drill_mm': f.get('via_drill_mm'),
                  'length_mm': f.get('length_mm', 1.6)}
        c = {'id': f"THERMAL:TJ:{f['ref']}", 'pred': 'thermal.tj',
             'ref': f['ref'], 'params': params}
        if f.get('tj_max') is not None:
            c['limit'] = f['tj_max']
        claims.append(c)
    for i in spec.get('interfaces', []) or []:
        length_mm, width_mm = net_geometry(st, i.get('net') or '')
        params = {'kind': i.get('kind', 'microstrip'),
                  'width_mm': i.get('width_mm') or width_mm,
                  'h_mm': i.get('h_mm') or stack.get('h_mm'),
                  'er': i.get('er') or stack.get('er'),
                  't_mm': i.get('t_mm') or stack.get('t_mm', 0.035),
                  'target_ohm': i.get('target_ohm'),
                  'tol_pct': i.get('tol_pct', 10.0)}
        claims.append({'id': f"SIGNAL:Z0:{i['name']}", 'pred': 'signal.z0',
                       'net': i.get('net'), 'params': params})
    for pr in spec.get('pairs', []) or []:
        c = {'id': f"SIGNAL:SKEW:{pr['name']}", 'pred': 'signal.skew',
             'params': {'p_net': pr.get('p'), 'n_net': pr.get('n')}}
        if pr.get('max_skew_mm') is not None:
            c['limit'] = pr['max_skew_mm']
        claims.append(c)
    for r in spec.get('requirements', []) or []:
        unformalized.append({'id': r.get('id'), 'text': r.get('text'),
                             'why': r.get('why') or
                             'no formal predicate for this requirement yet'})
    return claims, unformalized
