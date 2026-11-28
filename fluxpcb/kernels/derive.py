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
                    t_mm=0.035, source_ref=None, sink_refs=None):
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
    by_id = {i['id']: i for i in items}
    if not source and source_ref:
        src = [p for p in pads if by_id.get(p, {}).get('ref') == source_ref]
        if not src:
            src = [pads[0]]
    else:
        src = [source] if source else [pads[0]]
    if not sinks and sink_refs:
        sref_set = set(sink_refs)
        snk = [p for p in pads if by_id.get(p, {}).get('ref') in sref_set and p not in src]
        if not snk:
            snk = [p for p in pads if p not in src]
    else:
        snk = list(sinks) if sinks else [p for p in pads if p not in src]
    if not snk:
        return {'status': 'unknown', 'needed_information': ['sink pads']}
    from ..proof import geom
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


def zdiff_edge_coupled(width_mm, s_mm, h_mm, er, t_mm=0.035,
                       kind='microstrip'):
    """IPC-2141 edge-coupled differential impedance Zdiff (ohm)."""
    missing = [k for k, v in (('width_mm', width_mm), ('s_mm', s_mm),
                              ('h_mm', h_mm), ('er', er)) if not v]
    if missing:
        return {'status': 'unknown', 'needed_information': missing}
    from ..tools.ipc2141 import (edge_coupled_microstrip_zdiff,
                                 edge_coupled_stripline_zdiff)
    if kind == 'stripline':
        zodd, zdiff = edge_coupled_stripline_zdiff(
            float(width_mm), float(s_mm), float(h_mm), float(er), float(t_mm))
        model = 'IPC-2141 edge-coupled stripline'
    else:
        zodd, zdiff = edge_coupled_microstrip_zdiff(
            float(width_mm), float(s_mm), float(h_mm), float(er), float(t_mm))
        model = 'IPC-2141 edge-coupled microstrip'
    return {'status': 'derived', 'measured': round(zdiff, 2), 'units': 'ohm',
            'z_odd_ohm': round(zodd, 2),
            'evidence': {'model': model, 'model_version': '1',
                         'inputs': {'w': width_mm, 's': s_mm, 'h': h_mm,
                                    'er': er, 't': t_mm},
                         'inputs_hash': _hash([kind, width_mm, s_mm, h_mm, er, t_mm]),
                         'result': round(zdiff, 4)}}


def ipc_min_width_mm(current_a: float, t_mm: float = 0.035,
                     temp_rise_c: float = 10.0, internal: bool = False) -> float:
    """IPC-2221/2152 conservative minimum trace width (mm) for current_a."""
    if not current_a or current_a <= 0:
        return 0.0
    k = 0.024 if internal else 0.048
    area_mil2 = (float(current_a) / (k * (float(temp_rise_c) ** 0.44))) ** (1.0 / 0.725)
    t_mil = max(float(t_mm) / 0.0254, 0.5)
    w_mil = area_mil2 / t_mil
    return round(w_mil * 0.0254, 4)


def diff_coupling(st, p_net, n_net, target_s_mm=None, max_skew_mm=None,
                  max_via_diff=0, min_coupled_pct=60.0):
    """Real geometric audit of a routed differential pair:
    measures length skew, via symmetry, layer match, trunk width, coupled
    parallel fraction, and average edge-to-edge spacing along coupled runs."""
    if not p_net or not n_net:
        return {'status': 'unknown', 'needed_information': ['p_net', 'n_net']}

    def _collect(net):
        segs, vias, layers = [], 0, set()
        for it in st['items']:
            if it.get('net') != net or 'ref' in it:
                continue
            sh = it.get('shape') or {}
            if sh.get('kind') == 'seg':
                x1, y1, x2, y2 = sh['x1']/1e6, sh['y1']/1e6, sh['x2']/1e6, sh['y2']/1e6
                L = math.hypot(x2 - x1, y2 - y1)
                if L < 1e-4:
                    continue
                w = (sh.get('w') or 0) / 1e6
                ly = (it.get('layers') or [0])[0]
                segs.append((x1, y1, x2, y2, L, w, ly))
                layers.add(ly)
            elif sh.get('kind') == 'circle' and (it.get('drill') or 0) > 0:
                vias += 1
        return segs, vias, sorted(layers)

    sp, vp, lp_lays = _collect(p_net)
    sn, vn, ln_lays = _collect(n_net)
    if not sp or not sn:
        return {'status': 'unknown',
                'needed_information': ['routed copper on both pair nets']}

    len_p = sum(s[4] for s in sp)
    len_n = sum(s[4] for s in sn)
    skew = abs(len_p - len_n)
    via_diff = abs(vp - vn)
    w_p = sum(s[5] * s[4] for s in sp) / len_p
    w_n = sum(s[5] * s[4] for s in sn) / len_n
    w_avg = 0.5 * (w_p + w_n)

    def _wmedian(pairs):
        if not pairs:
            return None
        pairs = sorted(pairs, key=lambda x: x[0])
        tot = sum(w_ for _, w_ in pairs)
        acc = 0.0
        for val, w_ in pairs:
            acc += w_
            if acc >= 0.5 * tot:
                return val
        return pairs[-1][0]

    # Measure parallel coupled length & dominant coupled trunk width/gap
    max_cc = w_avg + (float(target_s_mm) if target_s_mm else 0.25) + 0.35
    coupled_len = 0.0
    coupled_gaps = []
    coupled_widths = []
    for x1, y1, x2, y2, L, wp, ly in sp:
        ux, uy = (x2 - x1) / L, (y2 - y1) / L
        mx, my = 0.5 * (x1 + x2), 0.5 * (y1 + y2)
        best_cc = None
        best_wn = 0.0
        for a1, b1, a2, b2, Ln, wn, lyn in sn:
            if lyn != ly:
                continue
            vx, vy = (a2 - a1) / Ln, (b2 - b1) / Ln
            if abs(ux * vx + uy * vy) < 0.92:  # within ~23 deg parallel
                continue
            # Perpendicular distance from midpoint of p-seg to n-seg
            dx, dy = mx - a1, my - b1
            proj = dx * vx + dy * vy
            if proj < -0.5 or proj > Ln + 0.5:
                continue
            px, py = a1 + max(0.0, min(Ln, proj)) * vx, b1 + max(0.0, min(Ln, proj)) * vy
            cc = math.hypot(mx - px, my - py)
            if cc <= max_cc and (best_cc is None or cc < best_cc):
                best_cc = cc
                best_wn = wn
        if best_cc is not None:
            coupled_len += L
            gap_here = max(0.0, best_cc - 0.5 * (wp + best_wn))
            coupled_gaps.append((gap_here, L))
            coupled_widths.append((0.5 * (wp + best_wn), L))

    coupled_pct = min(100.0, 100.0 * coupled_len / max(1e-6, min(len_p, len_n)))
    avg_gap_mm = _wmedian(coupled_gaps) if coupled_gaps else None
    w_trunk = _wmedian(coupled_widths) if coupled_widths else w_avg

    reasons = []
    if max_skew_mm is not None and skew > float(max_skew_mm):
        reasons.append(f'skew {skew:.3f}mm > {max_skew_mm}mm')
    if via_diff > int(max_via_diff):
        reasons.append(f'asymmetric vias ({vp} vs {vn})')
    if coupled_pct < float(min_coupled_pct):
        reasons.append(f'coupled {coupled_pct:.1f}% < {min_coupled_pct:.0f}%')

    ok = not reasons
    return {
        'status': 'derived' if ok else 'refuted',
        'ok': ok,
        'measured': round(coupled_pct, 1),
        'units': '%',
        'skew_mm': round(skew, 3),
        'L_p_mm': round(len_p, 3),
        'L_n_mm': round(len_n, 3),
        'vias_p': vp, 'vias_n': vn, 'via_diff': via_diff,
        'layers_p': lp_lays, 'layers_n': ln_lays,
        'width_mm': round(w_trunk, 4),
        'avg_gap_mm': round(avg_gap_mm, 4) if avg_gap_mm is not None else None,
        'witness': '; '.join(reasons),
        'evidence': {
            'model': 'coupled parallel differential geometry + via symmetry + skew',
            'model_version': '1',
            'inputs': {'p_net': p_net, 'n_net': n_net, 'target_s_mm': target_s_mm,
                       'max_skew_mm': max_skew_mm, 'min_coupled_pct': min_coupled_pct},
            'inputs_hash': _hash([p_net, n_net, round(len_p, 3), round(len_n, 3), vp, vn]),
            'result': round(coupled_pct, 3),
        },
    }
