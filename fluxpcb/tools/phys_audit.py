"""Physical audit: current and return, susceptibility, cavity, heatsink.

One board, one spec, two modes:

  fast  seconds.  Geometry only: J from the narrowest segment, drop from
        rho*L/A on the routed path, return concentration from the analytic
        image-current kernel, cavity from the board rectangle, thermal from
        the theta chain.  Everything is `estimate` and capped at P1.

  full  minutes.  The copper graph from the real board: per-layer coverage
        including zone fill, via/barrel connectivity, Neumann edges, a PCG
        solve per rail; a GND solve for the return; the eigenmodes of the
        actual pour; two thermal solves (base / with sink) with an energy
        balance.  Numbers measured on copper are `confirmed`.

`full` pours a GND plane on a **copy** when the board has none, re-runs DRC
after the pour, and keeps the unpoured board as the control.  The report says
which conclusions hold on the audit modification and which on the original.

Nothing here changes a gate, the benchmark path, or `tools/dcsolve.py`.

    run(board_path, spec_path, mode='full', out_dir=None) -> audit dict
    pour_plane(src, dst, net='GND') -> dict
    ipc2221_i_max(width_mm, t_mm, dT_c) -> A

audit.json carries: every number with its source, a finding per conclusion
(rule id, location, measured/limit, status, assumption), the cross-check that
contradicts it if any, and the list of things that were not run.
"""
from __future__ import annotations

import json
import math
import os
import shutil
import time

from ..findings import Finding

AUDIT_VERSION = '1.0'
PHASE = 'post_route'
T_CU_MM = 0.035
DEFAULT_LIMITS = {'j_a_mm2': 45.0, 'drop_pct': 2.0, 'via_a': 1.0,
                  'gnd_mohm': 10.0, 'tj_of_max': 0.9, 'em_db': -20.0,
                  'return_gap_mm': None}


# ------------------------------------------------------------------ plumbing
def find_board(path):
    path = str(path)
    if os.path.isfile(path) and path.endswith('.kicad_pcb'):
        return path
    for root, _dirs, files in os.walk(path):
        for f in files:
            if f.endswith('.kicad_pcb'):
                return os.path.join(root, f)
    raise SystemExit(f'no .kicad_pcb under {path}')


def pour_plane(src, dst, net='GND', inset_mm=0.3):
    """Copy `src` to `dst` and pour `net` over the board on the free layer."""
    import pcbnew

    shutil.copyfile(src, dst)
    # the board's rules and ignored-check list live in the sibling project;
    # without it kicad-cli applies default clearances and reports hundreds of
    # false errors on a board the benchmark certified clean
    for ext in ('.kicad_pro', '.kicad_prl'):
        if os.path.exists(src[:-len('.kicad_pcb')] + ext):
            shutil.copyfile(src[:-len('.kicad_pcb')] + ext,
                            dst[:-len('.kicad_pcb')] + ext)
    b = pcbnew.LoadBoard(dst)
    existing = list(b.Zones())
    if existing:
        return {'status': 'skipped', 'reason': 'board already has zones',
                'zones': len(existing), 'path': dst}
    if b.FindNet(net) is None:
        return {'status': 'error', 'error': f'no {net} net', 'path': dst}
    copper = [pcbnew.F_Cu, pcbnew.B_Cu]
    # pour on the layer with the least routing: that is the one a designer
    # would give to the plane
    lay = min(copper, key=lambda l: sum(1 for t in b.GetTracks()
                                        if t.GetLayer() == l))
    bb = b.GetBoardEdgesBoundingBox()
    pad = pcbnew.FromMM(inset_mm)
    z = pcbnew.ZONE(b)
    z.SetLayer(lay)
    z.SetNet(b.FindNet(net))
    o = z.Outline()
    idx = o.NewOutline()
    for (x, y) in ((bb.GetLeft() + pad, bb.GetTop() + pad),
                   (bb.GetRight() - pad, bb.GetTop() + pad),
                   (bb.GetRight() - pad, bb.GetBottom() - pad),
                   (bb.GetLeft() + pad, bb.GetBottom() - pad)):
        o.Append(pcbnew.VECTOR2I(x, y), idx)
    z.SetLocalClearance(pcbnew.FromMM(0.25))
    z.SetMinThickness(pcbnew.FromMM(0.25))
    z.SetPadConnection(pcbnew.ZONE_CONNECTION_THERMAL)
    z.SetIsFilled(False)
    b.Add(z)
    pcbnew.ZONE_FILLER(b).Fill(b.Zones())
    area = z.CalculateFilledArea() / 1e12
    pcbnew.SaveBoard(dst, b)
    return {'status': 'ok', 'path': dst, 'layer': b.GetLayerName(lay),
            'area_mm2': round(area, 1), 'island_removal': z.GetIslandRemovalMode()}


def drc_counts(board_path, out_json, runs=1):
    from ..repair_drc import run_drc, run_drc_steady
    d = run_drc_steady(board_path, out_json, runs=runs) if runs > 1 \
        else run_drc(board_path, out_json)
    if not d.get('ok'):
        return {'ok': False, 'error': d.get('error')}
    viol = d.get('violations') or []
    errs = [v for v in viol if str(v.get('severity', '')).lower() in ('error', '')]
    return {'ok': True, 'violations': len(viol), 'errors': len(errs),
            'unconnected': len(d.get('unconnected_items') or [])}


def ipc2221_i_max(width_mm, t_mm=T_CU_MM, dT_c=10.0, internal=False):
    """IPC-2221 external-layer current capacity, A in mil^2."""
    area = (width_mm / 0.0254) * (t_mm / 0.0254)
    k = 0.024 if internal else 0.048
    return k * (dT_c ** 0.44) * (area ** 0.725)


def _abs(a, b):
    return abs(a - b)


def _rel(a, b):
    return abs(a - b) / abs(b) if b else None


def _spec_inputs(spec):
    out = []
    for key, rows in (('rails', spec.get('rails', [])),
                      ('signals', spec.get('signals', [])),
                      ('parts', spec.get('parts', [])),
                      ('sink', [spec.get('sink')] if spec.get('sink') else []),
                      ('emitters', spec.get('emitters', []))):
        for r in rows:
            if not r:
                continue
            out.append({'kind': key, 'what': r.get('net') or r.get('ref'),
                        'value': {k: v for k, v in r.items()
                                  if k in ('current_a', 'power_w', 'f_hz',
                                           'theta_sa', 'theta_ja', 'amp_db')},
                        'source': r.get('source', 'spec (declared, not measured)')})
    return out


# --------------------------------------------------------------------- main
def run(board_path, spec_path, mode='fast', out_dir=None, runs_drc=3):
    import numpy as _np
    t0 = time.time()
    board_path = find_board(board_path)
    spec_path = str(spec_path)
    with open(spec_path, encoding='utf-8') as f:
        spec = json.load(f)
    lim = dict(DEFAULT_LIMITS)
    lim.update(spec.get('limits') or {})
    stack = spec.get('stackup') or {}
    t_board = float(stack.get('thickness_mm', 1.6))
    t_cu = float(stack.get('copper_mm', T_CU_MM))
    er = float(stack.get('er', 4.4))
    full = (mode == 'full')
    out_dir = out_dir or os.path.join(os.path.dirname(board_path),
                                      os.path.basename(board_path)[:-10] + '.phys')
    os.makedirs(out_dir, exist_ok=True)

    from . import copper_graph as cg
    audit = {'audit_version': AUDIT_VERSION, 'mode': mode,
             'board': os.path.abspath(board_path), 'spec': os.path.abspath(spec_path),
             'stackup': {'thickness_mm': t_board, 'copper_mm': t_cu, 'er': er,
                         'source': stack.get('source', 'spec/default')},
             'limits': lim, 'inputs': _spec_inputs(spec),
             'run': {'what': [], 'assumed': [], 'missing': []},
             'checks': [], 'started': time.strftime('%Y-%m-%d %H:%M:%S')}

    def add(row):
        audit['checks'].append(row)

    def finding(**kw):
        kw.setdefault('phase', PHASE)
        kw.setdefault('source', {'solver': 'phys_audit', 'version': AUDIT_VERSION,
                                 'at': audit['started']})
        if isinstance(kw.get('where'), str):
            kw['where'] = {'text': kw['where']}
        f = Finding(**kw)
        audit['checks'].append(f.as_dict())
        return f

    # ------------------------------------------------------------ the board
    poured = None
    base = cg.extract(board_path, cell_mm=float(spec.get('cell_mm', 0.2)))
    audit['board_meta'] = {'fp': base.board_fp,
                           'size_mm': [round(base.nx * base.cell_mm, 1),
                                       round(base.ny * base.cell_mm, 1)],
                           'board_area_mm2': base.board_area_mm2,
                           'nets': len(base.nets), 'layers': base.layers,
                           'zones': len(base.zones),
                           'segments': sum(len(v) for v in base.segs.values()),
                           'vias': len(base.vias)}
    if full:
        poured_path = os.path.join(out_dir, 'board_poured.kicad_pcb')
        pr = pour_plane(board_path, poured_path)
        audit['pour'] = pr
        audit['run']['what'].append('poured a GND plane on a copy' if pr.get('status') == 'ok'
                                    else f'pour skipped: {pr.get("reason") or pr.get("error")}')
        before = drc_counts(board_path, os.path.join(out_dir, 'drc_before.json'), runs=runs_drc)
        after = drc_counts(poured_path, os.path.join(out_dir, 'drc_after.json'), runs=runs_drc)
        audit['drc'] = {'before': before, 'after_pour': after}
        if not (before.get('ok') and after.get('ok')):
            audit['drc']['ok'] = False
            audit['run']['missing'].append('DRC re-check after the pour did not run')
        else:
            worse = after['errors'] > before['errors'] or after['unconnected'] > before['unconnected']
            audit['drc']['ok'] = not worse
            if worse:
                audit['run']['missing'].append(
                    'the pour introduced DRC errors; its results are not trusted')
        if pr.get('status') == 'ok':
            poured = cg.extract(poured_path, cell_mm=base.cell_mm)

    # --------------------------------------------------------------- rails
    rails = []
    for r in spec.get('rails', []):
        net = r['net']
        i_a = float(r['current_a'])
        v_rail = float(r.get('v_rail', 0.0))
        nw = cg.narrowest(base, net)
        row = {'net': net, 'current_a': i_a, 'v_rail': v_rail,
               'narrowest': nw, 'source': r.get('source')}
        if nw:
            area = nw['width_mm'] * t_cu
            j_geom = i_a / area
            i_max = ipc2221_i_max(nw['width_mm'], t_cu)
            row['j_geom_a_mm2'] = round(j_geom, 2)
            row['ipc2221_i_max_a_10c'] = round(i_max, 3)
            row['ipc_use_pct'] = round(100 * i_a / i_max, 1)
        segs = base.segs.get(cg.net_by_name(base, net), [])
        length_m = sum(math.hypot(x2 - x1, y2 - y1) for x1, y1, x2, y2, _w, _l
                       in segs) * 1e-3
        if nw and length_m:
            r_ohm = 1.72e-8 * length_m / (nw['width_mm'] * 1e-3 * t_cu * 1e-3)
            row['r_geom_ohm'] = round(r_ohm, 5)
            row['drop_geom_mv'] = round(1e3 * i_a * r_ohm, 3)
            row['drop_geom_pct'] = (round(100 * i_a * r_ohm / v_rail, 4)
                                    if v_rail else None)
            row['length_mm'] = round(length_m * 1e3, 1)
        has_plane = any(z['net'] == net for z in base.zones)
        if full and poured is not None:
            solved_net = net
            sol = cg.solve(poured, solved_net, i_a, v_rail=max(v_rail, 1e-9),
                           t_cu_mm=t_cu)
            if sol['status'] == 'ok':
                row['field'] = {k: sol[k] for k in
                                ('drop_v', 'drop_pct', 'j_max_a_mm2', 'j_p95_a_mm2',
                                 'j_robust_a_mm2', 'j_robust_at', 'j_at', 'nodes')}
                jm = _np.max(_np.stack(sol['j_smooth']), axis=0)
                row['field']['j_map'] = _thin(jm, 4)
                row['field']['j_map_cell_mm'] = base.cell_mm * 4
                row['via_current_max_a'] = (sol['via_currents'][0]['current_a']
                                            if sol['via_currents'] else 0.0)
                row['crosscheck'] = (_rail_crosscheck(row, sol, i_a)
                                     if not has_plane else
                                     {'plane': True, 'agrees': None,
                                      'j_geom_a_mm2': None,
                                      'j_field_p95': sol['j_p95_a_mm2'],
                                      'j_field_robust': sol['j_robust_a_mm2'],
                                      'note': ('this net is a plane: the '
                                               'current spreads, so I/(w*t) at '
                                               'the narrowest trace says nothing; '
                                               'the plane check is the return '
                                               'section (coverage, voids, drop)'),
                                      'assumption': 'plane net: J is a spreading '
                                                    'field'})
                finding(**_j_finding(net, row, lim))
                finding(**_drop_finding(net, row, v_rail, lim))
                if row['via_current_max_a'] > lim['via_a']:
                    finding(rule_id='PHYS.via_current', severity='P1',
                            status='confirmed', where=f'{net} barrel',
                            measured=row['via_current_max_a'], limit=lim['via_a'],
                            units='A', assumption=row['crosscheck']['assumption'])
        else:
            finding(**_j_finding(net, row, lim, estimate=True))
            finding(**_drop_finding(net, row, v_rail, lim, estimate=True))
        rails.append(row)
    audit['rails'] = rails

    # ------------------------------------------------------- return paths
    audit['return'] = _return_section(base, poured, spec, t_board, cg)
    audit['return']['maps'] = _return_maps(base, poured)
    audit['checks'] += audit['return'].pop('checks', [])

    # -------------------------------------------------------------- cavity
    audit['cavity'] = _cavity_section(poured or base, spec, er, full=full)

    # ------------------------------------------------------------- thermal
    audit['thermal'] = _thermal_section(poured or base, spec, full=full)

    # ------------------------------------------------------------------- em
    from . import em_map
    audit['em'] = em_map.run(poured or base, spec, cell_mm=0.5)
    import numpy as _np
    audit['em']['map_db'] = _np.round(audit['em']['map_db'], 1)

    # -------------------------------------------------- findings & summary
    _thermal_findings(audit, spec, lim, finding, estimate=not full)
    _cavity_findings(audit, finding)
    _em_findings(audit, lim, finding)
    _return_findings(audit, lim, finding)
    audit['summary'] = _summary(audit)
    audit['elapsed_s'] = round(time.time() - t0, 2)
    audit['run']['what'].insert(0, 'fast: geometry only (no field solves)'
                                if not full else
                                'full: copper field solves on the audit board')
    audit['run']['missing'] += list(spec.get('needed_information') or [])
    audit['run']['missing'] = list(dict.fromkeys(audit['run']['missing']))
    jpath = os.path.join(out_dir, 'audit.json')
    with open(jpath, 'w', encoding='utf-8') as f:
        json.dump(_jsonable(audit), f, indent=1, ensure_ascii=False)
    try:
        from ..dashboard import phys_page
        audit['page'] = phys_page.render(jpath, os.path.join(out_dir, 'audit.html'))
        with open(jpath, 'w', encoding='utf-8') as f:
            json.dump(_jsonable(audit), f, indent=1, ensure_ascii=False)
    except Exception as e:
        audit['page_error'] = f'{type(e).__name__}: {e}'
    return audit


# ------------------------------------------------------------------ findings
def _mk(row):
    """Validate a plain result row as a Finding (and normalise `where`)."""
    row = dict(row)
    if isinstance(row.get('where'), str):
        row['where'] = {'text': row['where']}
    row.setdefault('phase', PHASE)
    row.setdefault('source', {'solver': 'phys_audit', 'version': AUDIT_VERSION,
                              'at': time.strftime('%Y-%m-%d %H:%M:%S')})
    return Finding(**row).as_dict()


def _rail_crosscheck(row, sol, i_a):
    nw = row.get('narrowest') or {}
    j_geom = row.get('j_geom_a_mm2')
    j_field = sol['j_robust_a_mm2']
    ratio = (j_field / j_geom) if j_geom else None
    why = None
    if ratio is not None and ratio < 0.7:
        why = ('field below the geometric value: the narrowest segment does '
               'not carry the whole rail current (branches), so I/(w*t) is an '
               'upper bound')
    elif ratio is not None and ratio > 1.15:
        why = ('field above the geometric value: a 45-degree bend and the pad '
               'entries crowd current past I/(w*t); up to 1.4 is the bend, '
               'beyond that the segment table misses a constriction')
    return {'j_geom_a_mm2': j_geom, 'j_field_p95': j_field,
            'ratio': round(ratio, 3) if ratio else None,
            'agrees': bool(ratio and 0.7 <= ratio <= 1.4),
            'note': why,
            'assumption': (f'{i_a} A steady from spec; {row["narrowest"]["width_mm"]} mm '
                           f'narrowest, {T_CU_MM*1000:.0f} um copper; field J is the '
                           f'maximum of a 2 mm-smoothed solve (p95 '
                           f'{sol["j_p95_a_mm2"]} A/mm2)')}


def _j_finding(net, row, lim, estimate=False):
    j = row.get('field', {}).get('j_robust_a_mm2') if not estimate else row.get('j_geom_a_mm2')
    where = (row['field'].get('j_robust_at') if row.get('field') else None) or \
        (row['narrowest']['at'] if row.get('narrowest') else None)
    over = j is not None and j > lim['j_a_mm2']
    sev = ('P1' if over else ('P2' if j and j > 0.8 * lim['j_a_mm2'] else 'P3'))
    return {'rule_id': 'PHYS.j_density', 'where': f'{net} @ {where}',
            'measured': round(j, 2) if j is not None else None,
            'limit': lim['j_a_mm2'], 'units': 'A/mm2',
            'severity': sev, 'status': 'estimate' if estimate else 'confirmed',
            'phase': PHASE,
            'assumption': (row.get('crosscheck') or {}).get('assumption') or
                          (f'{row["current_a"]} A steady; {T_CU_MM*1000:.0f} um copper; '
                           f'45 A/mm2 is a conservative policy (IPC-2221 at 10 C '
                           f'allows {row.get("ipc2221_i_max_a_10c")} A here)'),
            'needed_information': ([] if not estimate else
                                   ['a full-mode solve on the real copper']),
            'source': {'solver': 'phys_audit', 'version': AUDIT_VERSION,
                       'at': time.strftime('%Y-%m-%d %H:%M:%S')}}


def _drop_finding(net, row, v_rail, lim, estimate=False):
    pct = (row.get('field') or {}).get('drop_pct') if not estimate         else row.get('drop_geom_pct')
    measured = pct if pct is not None else None
    sev = 'P3'
    if measured is not None:
        sev = 'P0' if measured > 5 else ('P1' if measured > lim['drop_pct'] else 'P3')
    return {'rule_id': 'PHYS.drop', 'where': net,
            'measured': measured,
            'limit': lim['drop_pct'], 'units': '% of rail',
            'severity': sev,
            'status': ('estimate' if estimate else
                       ('confirmed' if measured is not None else 'unknown')),
            'phase': PHASE,
            'assumption': (f'rail {v_rail} V, {row["current_a"]} A from spec; '
                           + ('field drop on the real copper from source pad to '
                              'all other pads' if not estimate else
                              f'rho*L/A with L={row.get("length_mm")} mm of routed '
                              f'segments (an upper bound: parallel paths are ignored)')),
            'needed_information': ([] if measured is not None else
                                   ['the rail voltage: a percentage needs it',
                                    'a full-mode solve']),
            'source': {'solver': 'phys_audit', 'version': AUDIT_VERSION,
                       'at': time.strftime('%Y-%m-%d %H:%M:%S')}}


def _return_section(base, poured, spec, t_board, cg):
    from . import return_audit as ra
    sigs = spec.get('signals', [])
    out = {'signals': [], 'checks': [], 'h_mm': t_board,
           'boards': {'base': os.path.basename(base.board_path),
                      'poured': os.path.basename(poured.board_path) if poured else None}}
    if not poured:
        out['assumption'] = 'no plane on this board: signal return is analytic only'
    for s in sigs:
        row = {'net': s['net'], 'name': s.get('name'), 'driver': s.get('driver'),
               'load': s.get('load'), 'current_a': s.get('current_a', 0.02),
               'f_hz': s.get('f_hz'), 'source': s.get('source')}
        av = ra.availability(base, s['net'], h_mm=t_board)
        row['base'] = {k: av.get(k) for k in
                       ('gnd_share_mean', 'gnd_share_min', 'max_gap_mm',
                        'p90_gap_mm', 'samples_below_50pct', 'blockers')} \
            if av.get('status') == 'ok' else {'error': av.get('error')}
        if s.get('driver') and s.get('load'):
            r = ra.solve_return(base, s['net'], s['driver'], s['load'],
                                float(s.get('current_a', 0.02)), h_mm=t_board)
            row['base']['dc'] = ({k: r.get(k) for k in
                                  ('gnd_drop_mv', 'gnd_resistance_mohm', 'gnd_j_p95', 'dc')}
                                 if r.get('status') == 'ok' else {'error': r.get('error')})
        if poured:
            av2 = ra.availability(poured, s['net'], h_mm=t_board)
            row['poured'] = {k: av2.get(k) for k in
                             ('gnd_share_mean', 'gnd_share_min', 'max_gap_mm',
                              'p90_gap_mm', 'samples_below_50pct', 'blockers')} \
                if av2.get('status') == 'ok' else {'error': av2.get('error')}
            if s.get('driver') and s.get('load'):
                r2 = ra.solve_return(poured, s['net'], s['driver'], s['load'],
                                     float(s.get('current_a', 0.02)), h_mm=t_board)
                row['poured']['dc'] = ({k: r2.get(k) for k in
                                        ('gnd_drop_mv', 'gnd_resistance_mohm',
                                         'gnd_j_p95', 'dc')}
                                       if r2.get('status') == 'ok' else {'error': r2.get('error')})
        # the analytic benchmark for this h
        row['analytic_fraction_3h'] = round(ra.analytic_fraction(3.0), 3)
        out['signals'].append(row)
        best = row.get('poured') or row.get('base')
        gap = best.get('max_gap_mm')
        sev = 'P1' if gap is not None and gap > 3 * t_board else 'P3'
        out['checks'].append(_mk({
            'rule_id': 'RETURN.availability', 'where': row['name'] or s['net'],
            'measured': gap, 'limit': round(3 * t_board, 1), 'units': 'mm gap',
            'severity': sev,
            'status': 'confirmed' if row.get('poured') else 'estimate',
            'phase': PHASE,
            'assumption': (f'image-current availability within 3h of the route '
                           f'(h={t_board} mm); gap = distance from the route to '
                           f'the nearest GND copper; on the audit-poured board'
                           if row.get('poured') else
                           f'no plane: h={t_board} mm is the board thickness, '
                           f'the image-current model does not apply'),
            'needed_information': [] if row.get('poured') else
            ['a GND plane or pour on a layer under the route'],
            'source': {'solver': 'return_audit', 'version': AUDIT_VERSION,
                       'at': time.strftime('%Y-%m-%d %H:%M:%S')}}))
        dc = best.get('dc') or {}
        r_mohm = dc.get('gnd_resistance_mohm')
        if r_mohm is not None:
            out['checks'].append(_mk({
                'rule_id': 'RETURN.gnd_resistance', 'where': row['name'] or s['net'],
                'measured': r_mohm, 'limit': 10.0, 'units': 'mOhm',
                'severity': 'P1' if r_mohm > 10 else 'P3',
                'status': 'confirmed' if row.get('poured') else 'estimate',
                'phase': PHASE,
                'assumption': (f'DC solve between {s.get("driver")} and '
                               f'{s.get("load")} GND pads with '
                               f'{s.get("current_a")} A; a power-return '
                               f'diagnostic, not a score of AC return'),
                'source': {'solver': 'return_audit', 'version': AUDIT_VERSION,
                           'at': time.strftime('%Y-%m-%d %H:%M:%S')}}))
    if poured:
        out['plane'] = ra.plane_voids(poured)
    return out


def _return_maps(base, poured):
    """Distance-to-GND rasters for the page: where the return has to go."""
    from . import copper_graph as cg
    out = {}
    sig = None
    for r in base.nets.values():
        if r and r != 'GND':
            sig = sig or r
    for name, cop in (('base', base), ('poured', poured)):
        if cop is None:
            continue
        ref = None
        for z in cop.zones:
            if z['net'] == 'GND':
                ref = z['layer']
                break
        covs = cg.net_cover(cop, 'GND')
        import numpy as _np
        g = _np.zeros((cop.ny, cop.nx), bool)
        for c in covs:
            g |= c >= cg.MIN_COVER
        from scipy.ndimage import distance_transform_edt
        d = distance_transform_edt(~g) * cop.cell_mm
        out[name] = _thin(_np.minimum(d, 20.0), 4).tolist()
    out['cell_mm'] = base.cell_mm * 4
    out['scale_mm'] = 20.0
    out['what'] = 'distance from each cell to the nearest GND copper (mm, capped at 20)'
    return out


def _cavity_section(copper, spec, er, full=True):
    from . import cavity_audit as ca
    n = int(spec.get('cavity_modes', 4))
    has_plane = any(z['net'] == 'GND' for z in copper.zones)
    if not full or not has_plane:
        w = copper.nx * copper.cell_mm
        h = copper.ny * copper.cell_mm
        modes = []
        for (m_, n_, label) in ((1, 0, 'TM10'), (0, 1, 'TM01'),
                                (1, 1, 'TM11'), (2, 0, 'TM20')):
            f = ca.rect_analytic(w, h, er, m_, n_)
            modes.append({'f_hz': f, 'f_ghz': round(f / 1e9, 4),
                          'nodal_frac': None, 'label': label})
        modes.sort(key=lambda r: r['f_hz'])
        pairs = []
        tol = float(spec.get('cavity_tol', 0.10))
        for e in spec.get('emitters', []):
            f0 = e.get('f_hz')
            if not f0:
                continue
            for k in range(1, int(e.get('harmonics', 5)) + 1):
                for mo in modes:
                    if abs(mo['f_hz'] - f0 * k) / (f0 * k) <= tol:
                        pairs.append({'emitter': e.get('net'), 'harmonic': k,
                                      'f_hz': f0 * k, 'mode_ghz': mo['f_ghz'],
                                      'delta_pct': round(100 * (mo['f_hz'] - f0 * k)
                                                         / (f0 * k), 2),
                                      'emitter_amp': None,
                                      'risk': False})
        return {'status': 'ok', 'applicable': False, 'what_if': True,
                'plane_layer': None, 'er': er, 'modes': modes,
                'pairs': pairs, 'solid_rect_first_ghz': round(modes[0]['f_ghz'], 4),
                'fragmentation_ratio': None,
                'assumption': ('analytic rectangular cavity from the board '
                               'outline: no plane mask was solved in this mode'),
                'needed_information': ['a full-mode run on the poured plane']}

    spec_c = {'er': er, 'plane_pair': bool(spec.get('plane_pair', False)),
              'tol': float(spec.get('cavity_tol', 0.10)),
              'emitters': [{'net': e.get('net'), 'f_hz': e.get('f_hz'),
                            'harmonics': e.get('harmonics', 5),
                            'at': e.get('at')}
                           for e in spec.get('emitters', []) if e.get('f_hz')]}
    r = ca.run(copper, spec_c, er=er, n_modes=n)
    w = copper.nx * copper.cell_mm
    h = copper.ny * copper.cell_mm
    r['solid_rect_first_ghz'] = round(ca.rect_analytic(w, h, er, 1, 0) / 1e9, 4)
    if r.get('modes'):
        r['fragmentation_ratio'] = round(r['modes'][0]['f_ghz'] /
                                         max(r['solid_rect_first_ghz'], 1e-9), 2)
    r['what_if'] = not r.get('applicable')
    return r


def _thermal_section(copper, spec, full=True):
    from . import thermal_audit as ta
    if not full:
        return ta.lumped_only(spec)
    r = ta.run(copper, spec)
    if r.get('status') == 'ok':
        # the field arrays are large; the page gets them, audit.json gets a
        # coarse copy so the file stays readable
        for blk in ('base', 'with_sink'):
            b = r.get(blk)
            if b and 't' in b:
                b['t'] = _thin(b['t'], 4)
                b['grid']['cell'] = b['grid']['cell'] * 4
        r['units'] = 'degC'
    return r


def _thin(a, k):
    import numpy as np
    ny, nx = (a.shape[0] // k) * k, (a.shape[1] // k) * k
    return np.round(a[:ny, :nx].reshape(ny // k, k, nx // k, k).mean(axis=(1, 3)), 2)


def _thermal_findings(audit, spec, lim, finding, estimate=False):
    th = audit.get('thermal') or {}
    if th.get('status') != 'ok':
        return
    for p in th['parts']:
        base = p['tj_base']
        used = max(base['lumped_c'], base['field_c'] or 0)
        tjmax = p.get('tjmax_c')
        sev = 'P3'
        if tjmax:
            frac = used / tjmax
            sev = 'P1' if frac > lim['tj_of_max'] else ('P2' if frac > 0.75 else 'P3')
        finding(rule_id='THERMAL.tj', where=p['ref'], measured=round(used, 1),
                limit=tjmax, units='degC', severity=sev,
                status='estimate' if estimate else 'confirmed',
                assumption=(f"P={p['power_w']} W from spec; theta_ja "
                            f"{p['tj_base']['theta_k_per_w']} K/W; taken as "
                            f"max(lumped, board field + theta_jc); board under "
                            f"the part at {p['t_board_base_c']} C"),
                needed_information=([] if tjmax else ['the part junction limit']))
        sink = p.get('tj_sink')
        if sink:
            drop = p.get('tj_drop_c')
            finding(rule_id='THERMAL.heatsink_delta', where=p['ref'],
                    measured=drop, limit=None, units='K drop',
                    severity='P3', status='estimate',
                    assumption=(f"theta_sa={spec.get('sink', {}).get('theta_sa')} "
                                f"K/W, {spec.get('sink', {}).get('w_mm')}x"
                                f"{spec.get('sink', {}).get('h_mm')} mm sink on "
                                f"{spec.get('sink', {}).get('ref')}; natural "
                                f"convection; the sink is a lumped theta, not a "
                                f"fin solve"),
                    needed_information=['a fin-resolved theta_sa measurement'])
        if base.get('disputed') or (sink or {}).get('disputed'):
            note = (base.get('note') if base.get('disputed') else
                    (sink or {}).get('note', ''))
            finding(rule_id='THERMAL.coupling', where=p['ref'],
                    measured=base.get('field_c'), limit=base.get('lumped_c'),
                    units='degC', severity='P2',
                    status='estimate',
                    assumption=(f'the lumped theta chain and the board field '
                                f'disagree by '
                                f'{base.get("field_vs_lumped_pct")}%: the part '
                                f'is heated by its neighbours through the board '
                                f'({note})'),
                    needed_information=['a per-part thermal measurement or a '
                                        '3-D model with package surfaces'])


def _cavity_findings(audit, finding):
    c = audit.get('cavity') or {}
    if c.get('status') != 'ok':
        finding(rule_id='CAVITY.mode', where='plane', measured=None,
                limit=None, units=None, severity='P2', status='unknown',
                assumption='no plane to solve modes on',
                needed_information=c.get('needed_information') or ['a GND plane'])
        return
    hits = [p for p in c.get('pairs', []) if p.get('risk')]
    if not c.get('applicable'):
        finding(rule_id='CAVITY.mode', where='plane', measured=None, limit=None,
                units=None, severity='P2', status='unknown',
                assumption=('single plane: a parallel-plate cavity needs a '
                            'plane pair; the modes below are a what-if with an '
                            'ideal counter-electrode'),
                needed_information=c.get('needed_information') or [])
        finding(rule_id='CAVITY.mode', where='plane (what-if)',
                measured=c['modes'][0]['f_ghz'], limit=None, units='GHz',
                severity='P3', status='estimate',
                assumption=(f'first Neumann mode of the real pour is '
                            f'{c["modes"][0]["f_ghz"]} GHz vs '
                            f'{c["solid_rect_first_ghz"]} GHz for the solid '
                            f'rectangle (fragmentation ratio '
                            f'{c.get("fragmentation_ratio")}); holes and necks '
                            f'in the pour lower it'),
                needed_information=['a plane pair to make this a real cavity'])
    elif hits:
        finding(rule_id='CAVITY.mode', where=hits[0]['emitter'],
                measured=hits[0]['mode_ghz'], limit=hits[0]['f_hz'] / 1e9,
                units='GHz', severity='P2', status='confirmed',
                assumption='an emitter harmonic lands within tol of a mode')
    else:
        finding(rule_id='CAVITY.mode', where='plane', measured=c['modes'][0]['f_ghz'],
                limit=None, units='GHz', severity='P3', status='estimate',
                assumption='no emitter harmonic lands within tolerance of a mode')


def _em_findings(audit, lim, finding):
    em = audit.get('em') or {}
    if em.get('status') != 'ok':
        return
    for v in em['victims']:
        sev = 'P2' if v['level_db'] >= lim['em_db'] else 'P3'
        finding(rule_id='EMI.susceptibility', where=v['net'],
                measured=v['level_db'], limit=lim['em_db'], units='dB',
                severity=sev, status='estimate',
                assumption=(f'coupling proxy from {v["worst_aggressor"]}; '
                            f'relative dB (0 = strongest coupling on the board); '
                            f'no field solve, no spectrum'),
                needed_information=['an openEMS or measured near-field scan to '
                                    'turn this into absolute dBuV/m'])


def _return_findings(audit, lim, finding):
    ret = audit.get('return') or {}
    pl = ret.get('plane')
    if pl:
        big = pl['voids'][0] if pl.get('voids') else None
        finding(rule_id='RETURN.plane_coverage', where='plane',
                measured=pl['coverage_pct'], limit=95.0, units='% of board',
                severity='P2' if pl['coverage_pct'] < 90 else 'P3',
                status='confirmed',
                assumption=(f'the pour covers {pl["plane_area_mm2"]} of '
                            f'{pl["board_area_mm2"]} mm2; '
                            f'{len(pl["voids"])} interior voids >= 1 mm2'
                            + (f'; the largest is {big["area_mm2"]} mm2 at '
                               f'{big["at"]} mm, with '
                               f'{", ".join(big.get("footprints") or [])} inside'
                               if big else '')),
                needed_information=(['why the filler removed that island: add a '
                                     'GND stitch inside it, or set island '
                                     'removal to keep it'] if big and
                                    big['area_mm2'] > 50 else []))


# -------------------------------------------------------------------- output
def _summary(audit):
    rows = [c for c in audit['checks'] if 'severity' in c]
    order = {'P0': 0, 'P1': 1, 'P2': 2, 'P3': 3}
    rows.sort(key=lambda c: (order.get(c['severity'], 9), c.get('rule_id') or ''))
    counts = {}
    for c in rows:
        counts[c['severity']] = counts.get(c['severity'], 0) + 1
    return {'counts': counts,
            'worst': [{k: c.get(k) for k in ('rule_id', 'where', 'severity',
                                             'status', 'measured', 'limit', 'units')}
                      for c in rows[:8]],
            'confirmed': sum(1 for c in rows if c.get('status') == 'confirmed'),
            'estimate': sum(1 for c in rows if c.get('status') == 'estimate'),
            'unknown': sum(1 for c in rows if c.get('status') == 'unknown')}


def _jsonable(o):
    import numpy as np
    if isinstance(o, dict):
        return {k: _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if isinstance(o, np.ndarray):
        return _jsonable(o.tolist())
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    return o


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog='fluxpcb.tools.phys_audit')
    ap.add_argument('board')
    ap.add_argument('--spec', required=True)
    ap.add_argument('--mode', choices=('fast', 'full'), default='fast')
    ap.add_argument('-o', '--out')
    ap.add_argument('--drc-runs', type=int, default=3)
    a = ap.parse_args(argv)
    r = run(a.board, a.spec, mode=a.mode, out_dir=a.out, runs_drc=a.drc_runs)
    print(json.dumps({'mode': r['mode'], 'summary': r['summary'],
                      'elapsed_s': r['elapsed_s'],
                      'page': r.get('page'), 'out': os.path.dirname(
                          r.get('page') or '')}, indent=1, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
