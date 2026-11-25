"""Placement checks that a router can act on, on the real board.

The existing `placement_quality` gate knows two things: footprints overlap,
and a cap is within 8 mm of any IC's bounding box.  The reference skill's
VF list asks sharper questions, and each answer is a distance on copper:

    VF-05  a decoupling cap within 3 mm of the *pin* it serves, not the package
    VF-xx  routed length <= 1.3 x the pad MST + 2 mm (a detour detector)
    VF-04  the C_in -> IC -> C_out loop <= 20 mm^2 (switching loop area)
    VF-xx  a signal via within 3 mm of a ground via (return via proximity)

Everything is measured from `copper_graph.Copper` (tracks, pads with values,
vias, nets) — no extra board load.  Thresholds live in one dict with the
source string; findings are P2 (advisory) unless the caller asks strict.

    decap_distance(cop, limit_mm=3.0, cap_max_uf=1.0)
    trace_length_ratio(cop, factor=1.3, slop_mm=2.0)
    loop_area(cop, limit_mm2=20.0)
    reference_via(cop, gnd_net='GND', limit_mm=3.0)
    run(cop, cfg=None, gnd_net='GND') -> {'findings': [...], 'details': {...}}
"""
from __future__ import annotations

import math
import re

THRESHOLDS = {
    'decap_mm': 3.0, 'cap_max_uf': 1.0, 'trace_factor': 1.3,
    'trace_slop_mm': 2.0, 'loop_mm2': 20.0, 'ref_via_mm': 3.0,
}
SOURCES = {
    'decap_mm': 'low-power-iot-hardware-design VF-05 (MIT)',
    'trace_factor': 'reference skill: routed <= 1.3 x MST + 2 mm',
    'loop_mm2': 'low-power-iot-hardware-design VF-04 (MIT)',
    'ref_via_mm': 'geometric return-via proximity (lambda/20 when f is known)',
}


def cap_uf(value):
    """'100n' -> 0.1, '4u7' -> 4.7, '10uF' -> 10, '1n5' -> 0.0015 uF."""
    if value is None:
        return None
    s = str(value).strip().lower().replace('\u00b5', 'u').replace('\u03bc', 'u')
    m = re.match(r'^([0-9]*\.?[0-9]+)\s*([pnu]?)f?$', s)
    if m:
        v = float(m.group(1))
        unit = m.group(2)
        return v * {'p': 1e-6, 'n': 1e-3, 'u': 1.0}.get(unit, 1.0)
    m = re.match(r'^([0-9]+)([pnu])([0-9]+)f?$', s)
    if m:
        v = float(m.group(1) + '.' + m.group(3))
        return v * {'p': 1e-6, 'n': 1e-3, 'u': 1.0}[m.group(2)]
    return None


def _pads(cop):
    out = []
    for nc, plist in cop.pads.items():
        for p in plist:
            out.append((nc, cop.nets.get(nc, ''), p))
    return out


def _dist(a, b):
    return math.hypot(a['x'] - b['x'], a['y'] - b['y'])


def _pad_minus(p, q):
    """Centre distance minus both pads' half-diagonal (an edge-ish distance)."""
    ra = math.hypot(p['w'], p['h']) / 2.0
    rb = math.hypot(q['w'], q['h']) / 2.0
    return max(0.0, _dist(p, q) - ra - rb)


def decap_distance(cop, limit_mm=3.0, cap_max_uf=1.0):
    """Decoupling caps: the nearest U/MOD pin on the cap's net, in mm."""
    ics, caps = {}, {}
    for _nc, name, p in _pads(cop):
        ref = p.get('ref') or ''
        if ref.startswith(('U', 'MOD')) and not name.lower().startswith(('gnd', 'vss')):
            ics.setdefault(name, []).append(p)
        elif ref.startswith('C'):
            v = cap_uf(p.get('value'))
            if v is not None and v <= cap_max_uf:
                caps.setdefault(ref, []).append((name, p))
    rows = []
    for ref, entries in caps.items():
        best = None
        for name, p in entries:
            for q in ics.get(name, []):
                d = _pad_minus(p, q)
                if best is None or d < best[0]:
                    best = (d, name, q.get('ref'))
        if best is None:
            rows.append({'ref': ref, 'd_mm': None,
                         'why': 'no IC pin on its net'})
        else:
            rows.append({'ref': ref, 'd_mm': round(best[0], 2),
                         'net': best[1], 'pin_ref': best[2]})
    return rows


def mst_length(points):
    """Prim over the pad centres — the unavoidable length between pads."""
    pts = list(points)
    if len(pts) < 2:
        return 0.0
    used = {0}
    total = 0.0
    while len(used) < len(pts):
        best = None
        for i in used:
            for j in range(len(pts)):
                if j in used:
                    continue
                d = math.hypot(pts[i][0] - pts[j][0], pts[i][1] - pts[j][1])
                if best is None or d < best[0]:
                    best = (d, j)
        if best is None:
            break
        total += best[0]
        used.add(best[1])
    return total


def trace_length_ratio(cop, factor=1.3, slop_mm=2.0):
    """Per net: routed length vs the pad MST.  A high ratio is a detour."""
    plane_nets = {z['net'] for z in cop.zones}
    rows = []
    for nc, segs in cop.segs.items():
        name = cop.nets.get(nc, '')
        if name in plane_nets or not segs:
            continue
        pads = cop.pads.get(nc) or []
        if len(pads) < 2:
            continue
        routed = sum(math.hypot(x2 - x1, y2 - y1)
                     for x1, y1, x2, y2, _w, _l in segs)
        mst = mst_length([(p['x'], p['y']) for p in pads])
        if mst <= 0 or routed < 3.0:
            continue
        rows.append({'net': name, 'routed_mm': round(routed, 2),
                     'mst_mm': round(mst, 2),
                     'ratio': round(routed / mst, 2),
                     'limit_mm': round(factor * mst + slop_mm, 2),
                     'over': routed > factor * mst + slop_mm})
    return rows


def loop_area(cop, limit_mm2=20.0):
    """C_in -> IC pin -> C_out triangle per IC with a rail and a GND pad."""
    pts_rail, pts_gnd, ics = {}, {}, {}
    for _nc, name, p in _pads(cop):
        ref = p.get('ref') or ''
        low = name.lower()
        if ref.startswith('C'):
            if low.startswith(('gnd', 'vss')):
                pts_gnd.setdefault(name, []).append(p)
            else:
                pts_rail.setdefault(name, []).append(p)
        elif ref.startswith(('U', 'MOD')):
            ics.setdefault(ref, []).append((name, p))
    rows = []
    for ref, entries in ics.items():
        rail = [(n, p) for n, p in entries
                if n and not n.lower().startswith(('gnd', 'vss'))]
        gnd = [(n, p) for n, p in entries
               if n.lower().startswith(('gnd', 'vss'))]
        if not rail or not gnd:
            continue
        cin = cout = None
        for name, p in rail:
            for q in pts_rail.get(name, []):
                if cin is None or _dist(p, q) < _dist(rail[0][1], cin):
                    cin = q
        for name, p in gnd:
            for q in pts_gnd.get(name, []):
                if cout is None or _dist(p, q) < _dist(gnd[0][1], cout):
                    cout = q
        if not cin or not cout:
            continue
        a = (cin['x'], cin['y'])
        b = (rail[0][1]['x'], rail[0][1]['y'])
        c = (cout['x'], cout['y'])
        area = abs(a[0] * (b[1] - c[1]) + b[0] * (c[1] - a[1])
                   + c[0] * (a[1] - b[1])) / 2.0
        rows.append({'ref': ref, 'area_mm2': round(area, 1),
                     'cin': cin.get('ref'), 'cout': cout.get('ref')})
    return rows


def reference_via(cop, gnd_net='GND', limit_mm=3.0):
    """Signal vias vs the nearest ground via — the return has to change layers."""
    gnd = [(v['x'], v['y']) for v in cop.vias if v.get('net') == gnd_net]
    rows = []
    for v in cop.vias:
        if v.get('net') in (gnd_net, '', None):
            continue
        if not gnd:
            rows.append({'net': v['net'], 'x': round(v['x'], 1),
                         'y': round(v['y'], 1), 'd_mm': None})
            continue
        d = min(math.hypot(v['x'] - x, v['y'] - y) for x, y in gnd)
        rows.append({'net': v['net'], 'x': round(v['x'], 1),
                     'y': round(v['y'], 1), 'd_mm': round(d, 2)})
    return rows


def _finding(rule, where, measured, limit, units, assumption, needed=()):
    return {'rule_id': rule, 'where': {'text': where}, 'measured': measured,
            'limit': limit, 'units': units, 'severity': 'P2',
            'status': 'confirmed', 'phase': 'post_route',
            'assumption': assumption, 'needed_information': list(needed)}


def run(cop, cfg=None, gnd_net='GND'):
    cfg = dict(THRESHOLDS, **(cfg or {}))
    findings, details = [], {}
    dd = decap_distance(cop, cfg['decap_mm'], cfg['cap_max_uf'])
    far = [r for r in dd if r['d_mm'] is not None
           and r['d_mm'] > cfg['decap_mm']]
    for r in far[:8]:
        findings.append(_finding(
            'PLACEMENT.decap_distance', r['ref'], r['d_mm'], cfg['decap_mm'],
            'mm', f"nearest {r.get('pin_ref')} pin on {r.get('net')}; "
                  f"source {SOURCES['decap_mm']}"))
    details['decap'] = {'checked': len(dd), 'far': len(far),
                        'worst': sorted([r['d_mm'] for r in dd
                                         if r['d_mm'] is not None])[-3:]}
    tr = trace_length_ratio(cop, cfg['trace_factor'], cfg['trace_slop_mm'])
    over = [r for r in tr if r['over']]
    for r in sorted(over, key=lambda r: -r['ratio'])[:8]:
        findings.append(_finding(
            'PLACEMENT.trace_length', r['net'], r['routed_mm'], r['limit_mm'],
            'mm', f"{r['ratio']}x the pad MST; {SOURCES['trace_factor']}"))
    details['trace'] = {'nets': len(tr), 'over': len(over),
                        'worst': max([r['ratio'] for r in tr], default=None)}
    la = loop_area(cop, cfg['loop_mm2'])
    for r in sorted(la, key=lambda r: -r['area_mm2'])[:4]:
        if r['area_mm2'] > cfg['loop_mm2']:
            findings.append(_finding(
                'PLACEMENT.loop_area', r['ref'], r['area_mm2'],
                cfg['loop_mm2'], 'mm2',
                f"C_in {r['cin']} / C_out {r['cout']}; {SOURCES['loop_mm2']}"))
    details['loop'] = {'ics': len(la),
                       'worst_mm2': max([r['area_mm2'] for r in la],
                                        default=None)}
    rv = reference_via(cop, gnd_net, cfg['ref_via_mm'])
    farv = [r for r in rv if r['d_mm'] is None
            or r['d_mm'] > cfg['ref_via_mm']]
    for r in farv[:8]:
        findings.append(_finding(
            'PLACEMENT.reference_via', f"{r['net']} @ ({r['x']},{r['y']})",
            r['d_mm'], cfg['ref_via_mm'], 'mm', SOURCES['ref_via_mm'],
            ['the signal edge rate (lambda/20 needs a frequency)']
            if r['d_mm'] is None else ()))
    details['ref_via'] = {'vias': len(rv), 'far': len(farv)}
    return {'findings': findings, 'details': details}
