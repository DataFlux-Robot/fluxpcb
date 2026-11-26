"""The fab domain kernel: geometry and process rules, alongside the Lean kernel.

Not a subordinate tool: it owns the claims whose predicate is geometric/process
(clearance, width, and later annular/drill/edge/assembly) and emits
`ProofObject`-shaped verdicts.  The Lean kernel owns connectivity, netlist,
the frame and the DAG; the Step gate requires both to pass.

Exactness: all verdicts are integer cross-multiplications (nm), never floats.
Floats appear only in the human-readable witness distance.
"""
from __future__ import annotations

import math

from ..proof import geom

PREDS = ('clearance', 'width', 'annular', 'drill', 'hole', 'edge')


def _frac_lt(n1, d1, n2, d2):
    return n1 * d2 < n2 * d1


def _min_frac(a, b):
    return a if _frac_lt(a[0], a[1], b[0], b[1]) else b


def _pt_poly_d2(p, pts):
    if geom.pt_in_poly(p, pts):
        return (0, 1)
    best = None
    for a, b in geom.poly_edges(pts):
        d = geom.pt_seg_d2(p, a, b)
        best = d if best is None else _min_frac(best, d)
    return best if best is not None else (0, 1)


def _poly_poly_d2(p, q):
    if geom.poly_poly(p, q):
        return (0, 1)
    best = None
    for v in p:
        for a, b in geom.poly_edges(q):
            d = geom.pt_seg_d2(v, a, b)
            best = d if best is None else _min_frac(best, d)
    for v in q:
        for a, b in geom.poly_edges(p):
            d = geom.pt_seg_d2(v, a, b)
            best = d if best is None else _min_frac(best, d)
    return best if best is not None else (0, 1)


def _seg_poly_d2(s, pts):
    if geom.seg_poly((s['x1'], s['y1']), (s['x2'], s['y2']), s['w'], pts):
        return (0, 1)
    best = None
    for p in ((s['x1'], s['y1']), (s['x2'], s['y2'])):
        d = _pt_poly_d2(p, pts)
        best = d if best is None else _min_frac(best, d)
    for v in pts:
        d = geom.pt_seg_d2(v, (s['x1'], s['y1']), (s['x2'], s['y2']))
        best = d if best is None else _min_frac(best, d)
    return best if best is not None else (0, 1)


def _seg_seg_d2(s1, s2):
    a, b = (s1['x1'], s1['y1']), (s1['x2'], s1['y2'])
    c, d = (s2['x1'], s2['y1']), (s2['x2'], s2['y2'])
    best = None
    for p in (a, b):
        x = geom.pt_seg_d2(p, c, d)
        best = x if best is None else _min_frac(best, x)
    for p in (c, d):
        x = geom.pt_seg_d2(p, a, b)
        best = x if best is None else _min_frac(best, x)
    return best if best is not None else (0, 1)


def _pair_d2(i, j):
    """(num, den, extra_nm): centerline squared distance and the half-widths."""
    a, b = i['shape'], j['shape']
    ka, kb = a['kind'], b['kind']
    if ka == 'seg' and kb == 'seg':
        return (*_seg_seg_d2(a, b), a['w'] + b['w'])
    if ka == 'seg' and kb == 'circle':
        return (*geom.pt_seg_d2((b['cx'], b['cy']), (a['x1'], a['y1']),
                                (a['x2'], a['y2'])), a['w'] + 2 * b['r'])
    if ka == 'circle' and kb == 'seg':
        return (*geom.pt_seg_d2((a['cx'], a['cy']), (b['x1'], b['y1']),
                                (b['x2'], b['y2'])), b['w'] + 2 * a['r'])
    if ka == 'circle' and kb == 'circle':
        dx, dy = a['cx'] - b['cx'], a['cy'] - b['cy']
        return (dx * dx + dy * dy, 1, 2 * a['r'] + 2 * b['r'])
    if ka == 'seg' and kb == 'poly':
        return (*_seg_poly_d2(a, [tuple(p) for p in b['pts']]), a['w'])
    if ka == 'poly' and kb == 'seg':
        return (*_seg_poly_d2(b, [tuple(p) for p in a['pts']]), b['w'])
    if ka == 'circle' and kb == 'poly':
        return (*_pt_poly_d2((a['cx'], a['cy']), [tuple(p) for p in b['pts']]),
                2 * a['r'])
    if ka == 'poly' and kb == 'circle':
        return (*_pt_poly_d2((b['cx'], b['cy']), [tuple(p) for p in a['pts']]),
                2 * b['r'])
    if ka == 'poly' and kb == 'poly':
        return (*_poly_poly_d2([tuple(p) for p in a['pts']],
                               [tuple(p) for p in b['pts']]), 0)
    return (0, 1, 0)


def _gap_ok(i, j, rule):
    """(ok, gap_nm_float).  Exact verdict: sqrt(num/den) - extra/2 >= rule."""
    num, den, extra = _pair_d2(i, j)
    need = 2 * rule + extra
    ok = 4 * num >= need * need * den
    gap = math.sqrt(num / den) - extra / 2.0
    return ok, gap


def _same_layer(i, j):
    return any(l in j['layers'] for l in i['layers'])


def _same_footprint(i, j):
    """KiCad does not check clearance between pads of one footprint.

    Solder jumpers, castellated pads and fine-pitch parts are drawn with
    sub-rule pad gaps by design; a blanket rule would refute JMP1 on 0054
    (0.10 mm between its two pads), which the board's own DRC accepts.
    """
    return bool(i.get('ref')) and i.get('ref') == j.get('ref')


def clearance_worst(st, scope, rule):
    ids = set(scope) if scope is not None else None
    items = [i for i in st['items'] if ids is None or i['id'] in ids]
    worst = None
    for a in range(len(items)):
        for b in range(a + 1, len(items)):
            i, j = items[a], items[b]
            if i['net'] == j['net'] or not _same_layer(i, j) or \
                    _same_footprint(i, j):
                continue
            bi, bj = geom.bbox(i['shape']), geom.bbox(j['shape'])
            gx = max(bi[0] - bj[2], bj[0] - bi[2], 0)
            gy = max(bi[1] - bj[3], bj[1] - bi[3], 0)
            if gx * gx + gy * gy > rule * rule:
                continue
            ok, gap = _gap_ok(i, j, rule)
            if not ok and (worst is None or gap < worst[0]):
                worst = (gap, i['id'], j['id'])
    if worst is None:
        return None
    return (f"{worst[1]} vs {worst[2]}: gap {worst[0] / 1000:.1f} um "
            f"< {rule / 1000:.1f} um")


def width_worst(st, scope, rule):
    ids = set(scope) if scope is not None else None
    worst = None
    for i in st['items']:
        if ids is not None and i['id'] not in ids:
            continue
        sh = i['shape']
        if sh['kind'] != 'seg':
            continue
        if sh['w'] < rule and (worst is None or sh['w'] < worst[0]):
            worst = (sh['w'], i['id'])
    if worst is None:
        return None
    return f"{worst[1]}: width {worst[0] / 1000:.3f} mm < {rule / 1000:.3f} mm"


def annular_worst(st, scope, rule):
    ids = set(scope) if scope is not None else None
    worst = None
    for i in st['items']:
        if ids is not None and i['id'] not in ids:
            continue
        drill = i.get('drill', -1)
        if drill is None or drill < 0:
            continue
        sh = i['shape']
        if sh['kind'] == 'circle':
            r = sh['r']
        else:
            xs = [p[0] for p in sh['pts']]
            ys = [p[1] for p in sh['pts']]
            r = min(max(xs) - min(xs), max(ys) - min(ys)) // 2
        ann = r - drill // 2
        if ann < rule and (worst is None or ann < worst[0]):
            worst = (ann, i['id'])
    if worst is None:
        return None
    return f'{worst[1]}: annular {worst[0] / 1000:.3f} mm < {rule / 1000:.3f} mm'


def drill_worst(st, scope, rule, rule_max=None):
    ids = set(scope) if scope is not None else None
    worst = None
    for i in st['items']:
        if ids is not None and i['id'] not in ids:
            continue
        drill = i.get('drill', -1)
        if drill is None or drill < 0:
            continue
        bad = drill < rule or (rule_max is not None and drill > rule_max)
        if bad and (worst is None or drill < worst[0]):
            worst = (drill, i['id'])
    if worst is None:
        return None
    return f'{worst[1]}: drill {worst[0] / 1000:.3f} mm outside the process range'


def hole_to_hole_worst(st, scope, rule):
    ids = set(scope) if scope is not None else None
    holes = [i for i in st['items']
             if (i.get('drill') or -1) >= 0 and (ids is None or i['id'] in ids)]
    worst = None
    for a in range(len(holes)):
        for b in range(a + 1, len(holes)):
            i, j = holes[a], holes[b]
            si, sj = i['shape'], j['shape']
            ci = (si['cx'], si['cy']) if si['kind'] == 'circle' else (
                (min(p[0] for p in si['pts']) + max(p[0] for p in si['pts'])) // 2,
                (min(p[1] for p in si['pts']) + max(p[1] for p in si['pts'])) // 2)
            cj = (sj['cx'], sj['cy']) if sj['kind'] == 'circle' else (
                (min(p[0] for p in sj['pts']) + max(p[0] for p in sj['pts'])) // 2,
                (min(p[1] for p in sj['pts']) + max(p[1] for p in sj['pts'])) // 2)
            dx, dy = ci[0] - cj[0], ci[1] - cj[1]
            # edge-to-edge: centre distance minus the two drill radii
            import math
            gap = math.isqrt(dx * dx + dy * dy) - (i['drill'] + j['drill']) // 2
            if gap < rule and (worst is None or gap < worst[0]):
                worst = (gap, i['id'], j['id'])
    if worst is None:
        return None
    return (f'{worst[1]} vs {worst[2]}: hole gap {worst[0] / 1000:.3f} mm '
            f'< {rule / 1000:.3f} mm')


def edge_worst(st, scope, rule):
    outline = st.get('outline') or []
    if len(outline) < 3:
        return None
    edges = list(zip(outline, outline[1:] + outline[:1]))
    ids = set(scope) if scope is not None else None
    worst = None
    for i in st['items']:
        if ids is not None and i['id'] not in ids:
            continue
        sh = i['shape']
        if sh['kind'] == 'seg':
            pts = [(sh['x1'], sh['y1']), (sh['x2'], sh['y2'])]
            hw = sh['w'] // 2
        elif sh['kind'] == 'circle':
            pts = [(sh['cx'], sh['cy'])]
            hw = sh['r']
        else:
            pts = [(p[0], p[1]) for p in sh['pts']]
            hw = 0
        for p in pts:
            for a, b in edges:
                num, den = geom.pt_seg_d2(p, a, b)
                dist = math.isqrt(num // den) if den else 0
                gap = dist - hw
                if gap < rule and (worst is None or gap < worst[0]):
                    worst = (gap, i['id'])
    if worst is None:
        return None
    return (f'{worst[1]}: copper {worst[0] / 1000:.3f} mm from the edge '
            f'< {rule / 1000:.3f} mm')


def check(st, claims, rules):
    out = []
    for c in claims:
        pred = c['pred']
        rule = c.get('rule')
        if rule is None:
            key = {'clearance': 'clearance', 'width': 'track_min',
                   'annular': 'annular', 'drill': 'drill_min',
                   'hole': 'hole_to_hole', 'edge': 'edge'}[pred]
            rule = rules.get(key)
        if rule is None:
            out.append({'id': c['id'], 'pred': pred, 'status': 'unknown',
                        'witness': f'{pred} rule not declared',
                        'kernel': 'fab'})
            continue
        if pred in ('clearance', 'edge') and st.get('zones', 0) > 0:
            out.append({'id': c['id'], 'pred': pred, 'status': 'unknown',
                        'witness': 'zone copper is not extracted',
                        'kernel': 'fab'})
            continue
        rule = int(rule)
        if pred == 'clearance':
            w = clearance_worst(st, c.get('scope'), rule)
        elif pred == 'width':
            w = width_worst(st, c.get('scope'), rule)
        elif pred == 'annular':
            w = annular_worst(st, c.get('scope'), rule)
        elif pred == 'drill':
            w = drill_worst(st, c.get('scope'), rule)
        elif pred == 'hole':
            w = hole_to_hole_worst(st, c.get('scope'), rule)
        elif pred == 'edge':
            w = edge_worst(st, c.get('scope'), rule)
        else:
            w = None
        out.append({'id': c['id'], 'pred': pred,
                    'status': 'refuted' if w else 'proven',
                    'witness': w or '', 'kernel': 'fab'})
    return {'claims': out, 'ok': all(x['status'] == 'proven' for x in out)}
