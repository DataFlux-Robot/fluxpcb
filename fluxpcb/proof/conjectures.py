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


def net_trunk_width_mm(st, net):
    """Length-weighted dominant trace width (mm) of a net (robust to short pad neck-downs)."""
    segs = []
    for it in st['items']:
        if it.get('net') != net or 'ref' in it:
            continue
        sh = it['shape']
        if sh['kind'] != 'seg' or not sh.get('w'):
            continue
        L = math.hypot(sh['x2'] - sh['x1'], sh['y2'] - sh['y1'])
        if L > 0:
            segs.append((sh['w'] / 1e6, L))
    if not segs:
        return None
    segs.sort(key=lambda x: x[0])
    total = sum(L for _, L in segs)
    acc = 0.0
    for w, L in segs:
        acc += L
        if acc >= 0.5 * total:
            return round(w, 4)
    return round(segs[-1][0], 4)


def _pair_from_connections(conns: list[str]) -> tuple[str, str] | None:
    """Identify a (P, N) differential pair inside an interface's connections list."""
    if not conns or len(conns) < 2:
        return None
    cset = list(conns)
    suffix_pairs = (('_DP', '_DM'), ('_D+', '_D-'), ('_P', '_N'),
                    ('_H', '_L'), ('+', '-'), ('DP', 'DM'))
    for p_suf, n_suf in suffix_pairs:
        for a in cset:
            au = a.upper()
            if au.endswith(p_suf):
                stem = a[:len(a) - len(p_suf)]
                for b in cset:
                    if b != a and b.upper() == (stem + n_suf).upper():
                        return (a, b)
    return None


def extract_diff_pairs(spec: dict, board_nets: set[str] | None = None) -> list[dict]:
    """Extract normalized differential pair declarations from spec (both `pairs`
    and `interfaces` with `differential: true`)."""
    out = []
    seen = set()
    stack = spec.get('stackup') or {}
    cons = spec.get('constraints') or {}
    default_er = stack.get('er') or cons.get('er') or 4.4
    default_h = stack.get('h_mm') or (0.21 if (cons.get('layers') or 4) >= 6 else 0.2)

    for pr in spec.get('pairs', []) or []:
        p, n = pr.get('p'), pr.get('n')
        if not p or not n:
            continue
        if board_nets is not None and (p not in board_nets or n not in board_nets):
            continue
        key = (p, n)
        if key in seen:
            continue
        seen.add(key)
        out.append({
            'name': pr.get('name') or f'{p}/{n}',
            'p': p, 'n': n,
            'target_ohm': pr.get('target_ohm') or pr.get('impedance_ohm') or 90.0,
            'tol_pct': pr.get('tol_pct') or pr.get('tolerance_pct') or 15.0,
            's_mm': pr.get('s_mm') or 0.18,
            'h_mm': pr.get('h_mm') or default_h,
            'er': pr.get('er') or default_er,
            'style': pr.get('kind') or pr.get('style') or 'microstrip',
            'max_skew_mm': pr.get('max_skew_mm') if pr.get('max_skew_mm') is not None else 0.5,
        })

    for i in spec.get('interfaces', []) or []:
        if not i.get('differential'):
            continue
        pair = None
        if i.get('p') and i.get('n'):
            pair = (i['p'], i['n'])
        elif i.get('connections'):
            pair = _pair_from_connections(i['connections'])
        if not pair:
            continue
        p, n = pair
        if board_nets is not None and (p not in board_nets or n not in board_nets):
            continue
        if (p, n) in seen:
            continue
        seen.add((p, n))
        itype = str(i.get('type') or i.get('name') or '').lower()
        default_skew = 0.5 if 'usb' in itype else (2.0 if 'can' in itype else 1.0)
        default_z = 90.0 if 'usb' in itype else (120.0 if 'can' in itype else 100.0)
        out.append({
            'name': i.get('name') or f'{p}/{n}',
            'p': p, 'n': n,
            'target_ohm': float(i.get('target_ohm') or i.get('impedance_ohm') or default_z),
            'tol_pct': float(i.get('tol_pct') or i.get('tolerance_pct') or 15.0),
            's_mm': float(i.get('s_mm') or 0.18),
            'h_mm': float(i.get('h_mm') or default_h),
            'er': float(i.get('er') or default_er),
            'style': str(i.get('kind') or i.get('style') or 'microstrip'),
            'max_skew_mm': float(i['max_skew_mm']) if i.get('max_skew_mm') is not None else default_skew,
        })
    return out


def lower_constraints(spec: dict, rules: dict | None = None,
                      board_nets: set[str] | None = None) -> dict:
    """Constraint Lowering Compiler: compile declarative spec.yaml + fab rules
    into concrete synthesis targets for routers (Component 4/7 -> Component 9).

    Returns:
      {
        "diff_pairs": [{"name","p","n","w_mm","s_mm","z_target","tol_pct",
                        "max_skew_mm","style","prefer_layers"}],
        "net_widths_mm": {net_name: target_trunk_width_mm, ...},
        "neck_width_mm": float,
      }
    """
    from ..tools.ipc2141 import ImpedanceTarget, solve_for_width
    from ..kernels import derive

    r = rules or {}
    clear_mm = (int(r.get('clearance') or 127_000)) / 1e6
    track_min_mm = (int(r.get('track_min') or 127_000)) / 1e6
    t_mm = float((spec.get('stackup') or {}).get('t_mm') or 0.035)

    # 1) Differential pairs -> solved IPC-2141 edge-coupled width & legal gap
    diff_out = []
    for dp in extract_diff_pairs(spec, board_nets=board_nets):
        s_mm = max(dp['s_mm'], clear_mm + 0.015)
        tgt = ImpedanceTarget(
            net_class=dp['name'], style=dp['style'],
            z_target=dp['target_ohm'], tolerance_pct=dp['tol_pct'],
            differential=True,
        )
        w_sol, _ = solve_for_width(tgt, dp['h_mm'], dp['er'], t=t_mm, s=s_mm)
        # Keep within practical manufacturable bounds [track_min, 0.35mm]
        w_mm = round(max(track_min_mm + 0.003, min(0.35, w_sol)), 4)
        diff_out.append({
            'name': dp['name'],
            'p': dp['p'],
            'n': dp['n'],
            'w_mm': w_mm,
            's_mm': round(s_mm, 4),
            'z_target': dp['target_ohm'],
            'tol_pct': dp['tol_pct'],
            'max_skew_mm': dp['max_skew_mm'],
            'style': dp['style'],
            'h_mm': dp['h_mm'],
            'er': dp['er'],
            'prefer_layers': [0],  # top microstrip over In1.Cu GND reference plane
        })

    # 2) Power rails -> IPC-2221/2152 trunk widths with safe margin
    net_widths: dict[str, float] = {}
    power = spec.get('power') if isinstance(spec.get('power'), dict) else {}
    rails = spec.get('rails') or power.get('rails') or []
    for rail in rails:
        net = rail.get('net') or rail.get('name')
        if not net:
            continue
        if board_nets is not None and net not in board_nets:
            continue
        cur = float(rail.get('current_a') or 0.5)
        # External 1oz IPC width for 10C rise, with practical minimum 0.35mm for power trunks
        w_ipc = derive.ipc_min_width_mm(cur, t_mm=t_mm, temp_rise_c=10.0, internal=False)
        w_target = round(max(0.35, min(0.80, max(w_ipc * 1.4, track_min_mm * 2.5))), 3)
        net_widths[net] = max(net_widths.get(net, 0.0), w_target)

    # Auto-derive input power rail (e.g. 5V from "USB-C 5V" + total_w) & GND/VCAP/VDDA
    inp = str(power.get('input') or '').upper()
    total_w = float(power.get('total_w') or 0.0)
    if '5V' in inp and (board_nets is None or '5V' in board_nets):
        cur_5v = max(0.5, total_w / 5.0) if total_w > 0 else 0.5
        w_5v = derive.ipc_min_width_mm(cur_5v, t_mm=t_mm, temp_rise_c=10.0, internal=False)
        net_widths['5V'] = max(net_widths.get('5V', 0.0), round(max(0.45, min(0.80, w_5v * 1.6)), 3))
    if board_nets is not None:
        for pn, default_w in (('GND', 0.35), ('3V3', 0.35), ('5V', 0.45),
                              ('VCC', 0.40), ('VDD', 0.35), ('VBAT', 0.50),
                              ('VCAP', 0.25), ('VDDA', 0.25)):
            if pn in board_nets and pn not in net_widths:
                net_widths[pn] = default_w

    return {
        'diff_pairs': diff_out,
        'net_widths_mm': net_widths,
        'neck_width_mm': round(track_min_mm + 0.002, 4),
    }


def from_spec(spec, st):
    """Compile the spec into (claims, unformalized).

    Supports both compact proof-bench specs (`rails`, `pairs`, `interfaces[].net`)
    and full project `spec.yaml` (`power.rails[].v/source_ref/sink_refs`,
    `interfaces[].differential/impedance_ohm/s_mm/connections`, `constraints.er`).
    """
    claims, unformalized = [], []
    power = spec.get('power') if isinstance(spec.get('power'), dict) else {}
    rails = list(spec.get('rails') or power.get('rails') or [])
    stack = spec.get('stackup') or {}
    cons = spec.get('constraints') or {}
    default_er = stack.get('er') or cons.get('er')
    t_mm = stack.get('t_mm', 0.035)
    board_nets = set((st.get('netPads') or {}).keys())

    # Synthesize 5V rail check if power.input declares 5V + total_w and 5V is on board
    rail_names = {r.get('net') or r.get('name') for r in rails}
    if '5V' in str(power.get('input') or '').upper() and '5V' in board_nets and '5V' not in rail_names:
        tw = float(power.get('total_w') or 0.0)
        if tw > 0:
            rails.append({'name': '5V', 'net': '5V', 'v_rail': 5.0,
                          'current_a': round(tw / 5.0, 3),
                          'drop_limit_mv': 150.0, 'path': True})

    for r in rails:
        net = r.get('net') or r['name']
        v_rail = r.get('v_rail') if r.get('v_rail') is not None else r.get('v')
        cur = r.get('current_a')
        length_mm, min_w_mm = net_geometry(st, net)
        trunk_w_mm = net_trunk_width_mm(st, net) or min_w_mm
        use_path = bool(r.get('path') or r.get('source_ref') or r.get('sink_refs'))
        if use_path:
            params = {'net': net, 'current_a': cur,
                      'v_rail': v_rail, 'source': r.get('source'),
                      'sinks': r.get('sinks'),
                      'source_ref': r.get('source_ref'),
                      'sink_refs': r.get('sink_refs'),
                      't_mm': r.get('t_mm', t_mm)}
            pred = 'power.drop_path'
        else:
            params = {'current_a': cur, 'v_rail': v_rail,
                      'length_mm': length_mm, 'width_mm': min_w_mm,
                      't_mm': r.get('t_mm', t_mm)}
            pred = 'power.drop'
        c = {'id': f"POWER:DROP:{r['name']}", 'pred': pred,
             'net': net, 'params': params}
        drop_lim = r.get('drop_limit_mv')
        if drop_lim is None and v_rail and cur:
            drop_lim = round(float(v_rail) * 30.0, 1)  # 3% default IR-drop budget
        if drop_lim is not None:
            c['limit'] = drop_lim
        claims.append(c)

        # IPC-2152/2221 trunk current-density obligation
        if cur:
            j_lim = r.get('j_limit_a_mm2', 45.0)
            claims.append({
                'id': f"POWER:J:{r['name']}",
                'pred': 'power.j',
                'net': net,
                'params': {'current_a': cur, 'width_mm': trunk_w_mm,
                           't_mm': r.get('t_mm', t_mm)},
                'limit': j_lim,
            })

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

    # Differential interfaces & pairs -> Zdiff + Skew + Coupling obligations!
    emitted_pairs = set()
    for dp in extract_diff_pairs(spec, board_nets=None):
        p_net, n_net = dp['p'], dp['n']
        emitted_pairs.add((p_net, n_net))
        wp = net_trunk_width_mm(st, p_net)
        wn = net_trunk_width_mm(st, n_net)
        w_meas = round(0.5 * (wp + wn), 4) if (wp and wn) else (wp or wn)
        # Measure real coupled spacing if routed, fallback to declared s_mm
        from ..kernels import derive as _drv
        coup = _drv.diff_coupling(st, p_net, n_net, target_s_mm=dp['s_mm'])
        s_meas = coup.get('avg_gap_mm') if coup.get('avg_gap_mm') else dp['s_mm']

        claims.append({
            'id': f"SIGNAL:ZDIFF:{dp['name']}",
            'pred': 'signal.zdiff',
            'net': p_net,
            'p_net': p_net, 'n_net': n_net,
            'params': {
                'kind': dp['style'],
                'width_mm': w_meas,
                's_mm': s_meas,
                'h_mm': dp['h_mm'],
                'er': dp['er'],
                't_mm': t_mm,
                'target_ohm': dp['target_ohm'],
                'tol_pct': dp['tol_pct'],
            },
        })
        claims.append({
            'id': f"SIGNAL:SKEW:{dp['name']}",
            'pred': 'signal.skew',
            'net': p_net,
            'p_net': p_net, 'n_net': n_net,
            'params': {'p_net': p_net, 'n_net': n_net},
            'limit': dp['max_skew_mm'],
        })
        claims.append({
            'id': f"SIGNAL:COUPLED:{dp['name']}",
            'pred': 'signal.coupled',
            'net': p_net,
            'p_net': p_net, 'n_net': n_net,
            'params': {
                'p_net': p_net, 'n_net': n_net,
                's_mm': dp['s_mm'],
                'max_skew_mm': dp['max_skew_mm'],
                'max_via_diff': 0,
                'min_coupled_pct': 60.0,
            },
        })

    # Single-ended interfaces
    for i in spec.get('interfaces', []) or []:
        if i.get('differential'):
            continue
        net = i.get('net') or ((i.get('connections') or [None])[0])
        length_mm, width_mm = net_geometry(st, net or '')
        params = {'kind': i.get('kind') or i.get('style') or 'microstrip',
                  'width_mm': i.get('width_mm') or width_mm,
                  'h_mm': i.get('h_mm') or stack.get('h_mm'),
                  'er': i.get('er') or default_er,
                  't_mm': i.get('t_mm') or t_mm,
                  'target_ohm': i.get('target_ohm') or i.get('impedance_ohm'),
                  'tol_pct': i.get('tol_pct') or i.get('tolerance_pct') or 10.0}
        claims.append({'id': f"SIGNAL:Z0:{i['name']}", 'pred': 'signal.z0',
                       'net': net, 'params': params})

    for r in spec.get('requirements', []) or []:
        unformalized.append({'id': r.get('id'), 'text': r.get('text'),
                             'why': r.get('why') or
                             'no formal predicate for this requirement yet'})
    return claims, unformalized
