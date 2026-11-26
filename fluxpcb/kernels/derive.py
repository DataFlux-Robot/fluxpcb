"""Domain derivations: the physics a domain kernel can certify from declared inputs.

Each function returns ``{'status': 'derived'|'unknown', 'measured': ..., 'units': ...,
'evidence': {...}}``.  A derivation is never `proven`: it is a checked
calculation over a declared model and declared inputs, with the model id and
an inputs hash recorded.  Missing inputs are `unknown` and name what is
missing — the same discipline as the Lean side.
"""
from __future__ import annotations

import hashlib
import json
import math

SIGMA_CU = 5.8e7


def _hash(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str)
                          .encode()).hexdigest()[:16]


def power_drop(current_a, length_mm, width_mm, t_mm=0.035, v_rail=None,
               copper=True):
    """Analytic rail drop: rho*L/(w*t) on a declared length and width."""
    missing = [k for k, v in (('current_a', current_a), ('length_mm', length_mm),
                              ('width_mm', width_mm)) if v in (None, 0)]
    if missing:
        return {'status': 'unknown', 'needed_information': missing}
    if copper:
        r = 1.0 / SIGMA_CU * (length_mm * 1e-3) / (width_mm * 1e-3 * t_mm * 1e-3)
    else:
        return {'status': 'unknown', 'needed_information': ['conductor']}
    drop = current_a * r
    return {'status': 'derived', 'measured': round(drop * 1e3, 3), 'units': 'mV',
            'drop_pct': round(100 * drop / v_rail, 3) if v_rail else None,
            'evidence': {'model': 'R = rho*L/(w*t)', 'model_version': '1',
                         'inputs': {'I': current_a, 'L_mm': length_mm,
                                    'w_mm': width_mm, 't_mm': t_mm},
                         'inputs_hash': _hash([current_a, length_mm, width_mm,
                                               t_mm]),
                         'result': round(drop, 6)}}


def thermal_tj(power_w, theta_ja=None, theta_jc=None, theta_cs=None,
               theta_sa=None, t_amb=25.0, sink=False, theta_via=None):
    """Lumped junction temperature: theta_ja, or the theta_jc+cs+sa chain.

    A via array in parallel with the ambient path lowers the total theta:
    theta = 1 / (1/theta_path + 1/theta_via).
    """
    if power_w is None:
        return {'status': 'unknown', 'needed_information': ['power_w']}
    if sink:
        th = [theta_jc, theta_cs, theta_sa]
        if any(v is None for v in th):
            return {'status': 'unknown',
                    'needed_information': ['theta_jc/theta_cs/theta_sa']}
        total = sum(th)
    else:
        if theta_ja is None:
            return {'status': 'unknown', 'needed_information': ['theta_ja']}
        total = theta_ja
    if theta_via:
        total = 1.0 / (1.0 / total + 1.0 / theta_via)
    tj = t_amb + power_w * total
    return {'status': 'derived', 'measured': round(tj, 1), 'units': 'degC',
            'evidence': {'model': 'Tj = Ta + P*theta', 'model_version': '1',
                         'inputs': {'P': power_w, 'theta': total, 'Ta': t_amb},
                         'inputs_hash': _hash([power_w, total, t_amb]),
                         'result': round(tj, 3)}}


def z0_microstrip(width_mm, h_mm, er, t_mm=0.035):
    """IPC-2141 microstrip impedance (the same formula as tools/ipc2141)."""
    if not width_mm or not h_mm or not er:
        return {'status': 'unknown',
                'needed_information': ['width_mm', 'h_mm', 'er']}
    w, h = width_mm, h_mm
    z0 = 87.0 / math.sqrt(er + 1.41) * math.log(5.98 * h / (0.8 * w + t_mm))
    return {'status': 'derived', 'measured': round(z0, 1), 'units': 'ohm',
            'evidence': {'model': 'IPC-2141 microstrip', 'model_version': '1',
                         'inputs': {'w': w, 'h': h, 'er': er, 't': t_mm},
                         'inputs_hash': _hash([w, h, er, t_mm]),
                         'result': round(z0, 3)}}


def net_length_mm(st, net):
    """Total copper length of a net (sum of segment lengths, mm)."""
    total = 0
    for it in st['items']:
        if it.get('net') != net or it.get('shape', {}).get('kind') != 'seg':
            continue
        sh = it['shape']
        total += math.hypot(sh['x2'] - sh['x1'], sh['y2'] - sh['y1'])
    return total / 1e6


def copper_area_mm2(st, nets=None):
    """Copper area of the given nets (tracks + pads), mm²."""
    def poly_area(pts):
        a = 0.0
        for i in range(len(pts)):
            x1, y1 = pts[i]
            x2, y2 = pts[(i + 1) % len(pts)]
            a += x1 * y2 - x2 * y1
        return abs(a) / 2.0
    area = 0.0
    for it in st['items']:
        if nets is not None and it.get('net') not in nets:
            continue
        sh = it['shape']
        if sh['kind'] == 'seg':
            area += math.hypot(sh['x2'] - sh['x1'], sh['y2'] - sh['y1']) * sh['w']
        elif sh['kind'] == 'poly':
            area += poly_area(sh['pts'])
        elif sh['kind'] == 'circle':
            area += math.pi * sh['r'] * sh['r']
    return area / 1e6


def _via_resistance_ohm(drill_mm, length_mm=1.6, plating_mm=0.025):
    r_o = 0.5 * max(drill_mm or 0.3, 0.01) + plating_mm
    r_i = 0.5 * max(drill_mm or 0.3, 0.01)
    a = math.pi * (r_o * r_o - r_i * r_i) * 1e-6
    return 1.0 / SIGMA_CU * (length_mm * 1e-3) / a if a > 0 else 0.0


def power_drop_path(st, net, current_a, v_rail=None, source=None, sinks=None,
                    t_mm=0.035):
    """Worst-path rail drop from the routed copper graph (Dijkstra).

    The graph is the net's items; two items are adjacent when their copper
    touches (the same predicate the connectivity certificate uses).  Edge
    weight is the segment/via resistance.  The drop is I * max path
    resistance from the source pad to any sink pad, so a rail that looks fine
    on total length but narrows on one branch is not hidden.
    """
    import heapq
    items = [i for i in st['items'] if i.get('net') == net]
    pads = list(st['netPads'].get(net) or [])
    if not pads:
        return {'status': 'unknown',
                'needed_information': ['pads on ' + str(net)]}
    src = [source] if source else [pads[0]]
    snk = list(sinks) if sinks else [p for p in pads if p not in src]
    if not snk:
        return {'status': 'unknown', 'needed_information': ['sink pads']}
    from ..proof import geom
    by_id = {i['id']: i for i in items}
    adj = {i['id']: [] for i in items}
    for a in range(len(items)):
        for b in range(a + 1, len(items)):
            ia, ib = items[a], items[b]
            if not any(l in ib['layers'] for l in ia['layers']):
                continue
            if not geom.item_touch(ia, ib):
                continue

            def weight(j):
                sh = j['shape']
                if sh['kind'] == 'seg':
                    # coordinates are nm: length_m = nm*1e-9, width_m = nm*1e-9
                    length = math.hypot(sh['x2'] - sh['x1'],
                                        sh['y2'] - sh['y1']) * 1e-9
                    w = sh['w'] * 1e-9
                    return 1.0 / SIGMA_CU * length / (w * t_mm * 1e-3) if w else None
                if sh['kind'] == 'circle':
                    drill = j.get('drill') or 0
                    return _via_resistance_ohm(drill / 1e6 if drill > 0 else 0.3)
                return 0.0

            wj, wi = weight(ib), weight(ia)
            if wj is not None:
                adj[ia['id']].append((ib['id'], wj))
            if wi is not None:
                adj[ib['id']].append((ia['id'], wi))
    if any(s not in adj for s in src):
        return {'status': 'unknown',
                'needed_information': [f'source item not on net {net}']}
    best, prev = {s: 0.0 for s in src}, {}
    q = [(0.0, s) for s in src]
    while q:
        d, u = heapq.heappop(q)
        if d > best.get(u, math.inf):
            continue
        for v, w in adj.get(u, []):
            nd = d + w
            if nd < best.get(v, math.inf):
                best[v] = nd
                prev[v] = u
                heapq.heappush(q, (nd, v))
    reach = [p for p in snk if p in best]
    if not reach:
        return {'status': 'unknown',
                'needed_information': ['no resistive path to a sink']}
    worst = max(reach, key=lambda p: best[p])
    path, node = [], worst
    while node in prev:
        path.append(node)
        node = prev[node]
    path.append(node)
    widths = [by_id[p]['shape']['w'] for p in path
              if by_id.get(p, {}).get('shape', {}).get('kind') == 'seg'
              and by_id[p]['shape'].get('w')]
    r = best[worst]
    drop = current_a * r
    min_w = min(widths) / 1e6 if widths else None
    j = (current_a / (min_w * t_mm)) if min_w else None
    return {'status': 'derived', 'measured': round(drop * 1e3, 3),
            'units': 'mV',
            'drop_pct': round(100 * drop / v_rail, 3) if v_rail else None,
            'r_ohm': round(r, 4), 'path_items': len(path),
            'min_width_mm': min_w,
            'current_density': round(j, 3) if j else None,
            'evidence': {'model': 'R = sum(rho*L/(w*t)) over the worst path',
                         'model_version': '2',
                         'inputs': {'I': current_a, 'net': net,
                                    'source': src, 'sinks': snk,
                                    't_mm': t_mm},
                         'inputs_hash': _hash([current_a, net, src, snk, t_mm]),
                         'result': round(drop, 6)}}


def via_array_theta(n_vias, drill_mm, length_mm=1.6):
    """Thermal resistance of a via array (K/W), copper barrel only."""
    if not n_vias or not drill_mm:
        return {'status': 'unknown',
                'needed_information': ['n_vias', 'drill_mm']}
    a = math.pi * (drill_mm * 1e-3) * 0.025e-3      # barrel wall cross-section
    per = (length_mm * 1e-3) / (385.0 * a)
    theta = per / n_vias
    return {'status': 'derived', 'measured': round(theta, 3), 'units': 'K/W',
            'evidence': {'model': 'theta = L/(k*A*n)', 'model_version': '1',
                         'inputs': {'n': n_vias, 'drill_mm': drill_mm,
                                    'L_mm': length_mm},
                         'inputs_hash': _hash([n_vias, drill_mm, length_mm]),
                         'result': round(theta, 6)}}


def z0_stripline(width_mm, h_mm, er, t_mm=0.035):
    """IPC-2141 stripline (centred, b = 2h)."""
    if not width_mm or not h_mm or not er:
        return {'status': 'unknown',
                'needed_information': ['width_mm', 'h_mm', 'er']}
    b = 2 * h_mm
    z0 = 60.0 / math.sqrt(er) * math.log(4 * b / (0.67 * math.pi *
                                                  (0.8 * width_mm + t_mm)))
    return {'status': 'derived', 'measured': round(z0, 1), 'units': 'ohm',
            'evidence': {'model': 'IPC-2141 stripline', 'model_version': '1',
                         'inputs': {'w': width_mm, 'h': h_mm, 'er': er,
                                    't': t_mm},
                         'inputs_hash': _hash([width_mm, h_mm, er, t_mm]),
                         'result': round(z0, 3)}}


def diff_skew(st, p_net, n_net):
    """Length difference of a declared differential pair, mm."""
    if not p_net or not n_net:
        return {'status': 'unknown', 'needed_information': ['p_net', 'n_net']}
    lp, ln = net_length_mm(st, p_net), net_length_mm(st, n_net)
    if lp <= 0 or ln <= 0:
        return {'status': 'unknown',
                'needed_information': ['routed copper on both pair nets']}
    return {'status': 'derived', 'measured': round(abs(lp - ln), 3),
            'units': 'mm',
            'evidence': {'model': '|L(P) - L(N)|', 'model_version': '1',
                         'inputs': {'p_net': p_net, 'n_net': n_net,
                                    'L_p_mm': round(lp, 3),
                                    'L_n_mm': round(ln, 3)},
                         'inputs_hash': _hash([p_net, n_net, round(lp, 3),
                                               round(ln, 3)]),
                         'result': round(abs(lp - ln), 6)}}
