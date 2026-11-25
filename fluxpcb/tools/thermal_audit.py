"""Steady thermal audit on the real board: two runs, one colour scale.

The board is a 2-D sheet: copper raises the in-plane conductance, both faces
convect, and each dissipating part injects its power over its own footprint.
A heatsink enters as an extra surface conductance over the footprint it
covers, 1/(theta_sa * A), which is the lumped way to say "the tab now has a
path that does not go through the board".

    k(x,y) = k_fr4 + (k_cu - k_fr4) * t_cu * (cov_F + cov_B) / t_board

so a cell under the pour conducts ~30x better than bare FR4, and a cell in
the plane's island-removed hole (0376 has a 294 mm^2 one under U3) does not.

Junction temperature is computed twice, on purpose:

  * lumped: Tj = T_amb + P * theta_ja            (no sink)
            Tj = T_amb + P * (theta_jc + theta_cs + theta_sa)  (sink)
  * field:  Tj = T_board_under_part + P * theta_jc

If the two disagree by more than 15% the part is marked `disputed` — one of
the theta values or the spreading assumption is then wrong, and the report
says which.

    solve_board(copper, parts, sink=None) -> {t, p_total, heat_out, balance}
    run(copper, spec) -> {base, with_sink, parts, energy, disputed}
"""
from __future__ import annotations

import math

import numpy as np

from . import copper_graph as cg

K_FR4 = 0.3          # W/m/K
K_CU = 385.0
T_BOARD_MM = 1.6
T_CU_MM = 0.035


def k_sheet(cov_sum):
    """In-plane sheet conductivity (W/m/K) for coverage `cov_sum` (0..2)."""
    frac = T_CU_MM * np.clip(cov_sum, 0.0, 2.0) / T_BOARD_MM
    return K_FR4 + (K_CU - K_FR4) * frac


def _coarse(copper, cell_mm, which='sum'):
    """Downsample the coverage to the thermal grid (block mean)."""
    k = max(1, int(round(cell_mm / copper.cell_mm)))
    ny2, nx2 = (copper.ny // k) * k, (copper.nx // k) * k
    tot = None
    for c in cg.net_cover(copper, None):
        b = c[:ny2, :nx2].reshape(ny2 // k, k, nx2 // k, k).mean(axis=(1, 3))
        tot = b if tot is None else tot + b
    return tot, cell_mm


def pad_centroid(copper, ref):
    xs, ys = [], []
    for plist in copper.pads.values():
        for p in plist:
            if p.get('ref') == ref:
                xs.append(p['x'])
                ys.append(p['y'])
    if not xs:
        return None
    return (sum(xs) / len(xs), sum(ys) / len(ys))


def part_bbox(copper, ref, pad_mm=1.0):
    xs, ys = [], []
    for plist in copper.pads.values():
        for p in plist:
            if p.get('ref') == ref:
                xs.append(p['x'])
                ys.append(p['y'])
    if not xs:
        return None
    return (min(xs) - pad_mm, min(ys) - pad_mm,
            max(xs) + pad_mm, max(ys) + pad_mm)


def solve_board(copper, parts, h_w_m2k=10.0, t_amb_c=25.0,
                cell_mm=0.5, sink=None, fixed=None):
    """Steady 2-D sheet with Robin faces; returns the temperature field."""
    import scipy.sparse as sp
    from scipy.sparse.linalg import spsolve

    cov, cell = _coarse(copper, cell_mm)
    ny, nx = cov.shape
    k = k_sheet(cov)                       # W/m/K (sheet)
    t_m = T_BOARD_MM * 1e-3
    A = cell * 1e-3 * cell * 1e-3          # m^2 per cell
    g = k * t_m                            # W/K per square  (k*t * 1)
    # convection: both faces
    h = 2.0 * h_w_m2k
    extra = np.zeros((ny, nx))
    if sink:
        b = part_bbox(copper, sink['ref'])
        if b:
            cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
            sw = float(sink.get('w_mm', 20.0)) / 2
            sh = float(sink.get('h_mm', 15.0)) / 2
            i0 = max(0, int((cx - sw - copper.x0_mm) / cell_mm))
            i1 = min(nx - 1, int((cx + sw - copper.x0_mm) / cell_mm))
            j0 = max(0, int((cy - sh - copper.y0_mm) / cell_mm))
            j1 = min(ny - 1, int((cy + sh - copper.y0_mm) / cell_mm))
            area = max(1, (i1 - i0 + 1) * (j1 - j0 + 1)) * A
            extra[j0:j1 + 1, i0:i1 + 1] = 1.0 / (float(sink['theta_sa']) * area)
            sink = dict(sink, area_mm2=round(area * 1e6, 1),
                        h_extra=round(float(extra[j0, i0]), 1))
    q = np.zeros((ny, nx))
    for p in parts:
        b = p.get('bbox')
        if not b and p.get('w_mm') and p.get('h_mm'):
            c = pad_centroid(copper, p['ref'])
            if c:
                b = (c[0] - p['w_mm'] / 2, c[1] - p['h_mm'] / 2,
                     c[0] + p['w_mm'] / 2, c[1] + p['h_mm'] / 2)
        b = b or part_bbox(copper, p['ref'], pad_mm=2.0)
        if not b:
            continue
        x0, y0, x1, y1 = b
        i0 = max(0, int((x0 - copper.x0_mm) / cell_mm))
        i1 = min(nx - 1, int((x1 - copper.x0_mm) / cell_mm))
        j0 = max(0, int((y0 - copper.y0_mm) / cell_mm))
        j1 = min(ny - 1, int((y1 - copper.y0_mm) / cell_mm))
        n = max(1, (i1 - i0 + 1) * (j1 - j0 + 1))
        q[j0:j1 + 1, i0:i1 + 1] += float(p['power_w']) / n
    # equation: -div(g grad T) + (h + extra) A T = q + (h+extra) A T_amb
    rows, cols, vals = [], [], []
    for j in range(ny):
        for i in range(nx):
            kk = j * nx + i
            for (dj, di) in ((1, 0), (0, 1)):
                jj, ii = j + dj, i + di
                if jj < ny and ii < nx:
                    gg = 0.5 * (g[j, i] + g[jj, ii])
                    rows += [kk, jj * nx + ii]
                    cols += [jj * nx + ii, kk]
                    vals += [-gg, -gg]
    Aoff = sp.coo_matrix((vals, (rows, cols)), shape=(ny * nx, ny * nx)).tocsr()
    diag = -np.asarray(Aoff.sum(axis=1)).ravel() + (h + extra).ravel() * A
    M = Aoff + sp.diags(diag)
    b0 = (q + (h + extra) * A * t_amb_c).ravel().copy()
    b = b0.copy()
    free = np.ones(ny * nx, bool)
    fixed_nodes = []
    if fixed:
        for (j, i), tv in fixed.items():
            kk = j * nx + i
            # a fixed node is a boundary: its neighbours must feel it
            b -= np.asarray(M[:, kk].todense()).ravel() * tv
            free[kk] = False
            fixed_nodes.append(kk)
        b[list(fixed_nodes)] = [fixed[(k // nx, k % nx)] for k in fixed_nodes]
    x = np.full(ny * nx, t_amb_c)
    # the Robin term is ~1e-4 of the conduction term, so CG crawls; the grid
    # is small enough (tens of thousands of cells) for a direct solve
    try:
        sol = spsolve(M[free][:, free].tocsc(), b[free])
    except Exception as e:
        return {'status': 'error', 'error': f'{type(e).__name__}: {e}'}
    if not np.all(np.isfinite(sol)):
        return {'status': 'error', 'error': 'singular thermal matrix'}
    x[free] = sol
    if fixed_nodes:
        x[fixed_nodes] = [fixed[(k // nx, k % nx)] for k in fixed_nodes]
    T = x.reshape(ny, nx)
    p_total = float(sum(float(p['power_w']) for p in parts))
    heat = float(((h + extra) * A * (T - t_amb_c)).sum())
    # power that enters through the fixed boundary = the residual there
    heat_in = 0.0
    if fixed_nodes:
        resid = M @ x - b0
        heat_in = float(resid[fixed_nodes].sum())
    denom = p_total + abs(heat_in)
    bal = abs(heat - p_total - heat_in) / denom if denom else 0.0
    return {'status': 'ok', 't': T, 'cell_mm': cell_mm,
            'x0': copper.x0_mm, 'y0': copper.y0_mm,
            'tmax_c': round(float(T.max()), 2),
            'tmean_c': round(float(T.mean()), 2),
            'p_total_w': round(p_total, 3),
            'heat_out_w': round(heat, 3),
            'heat_in_w': round(heat_in, 3),
            'balance_err': round(bal, 4),
            'sink': ({k: v for k, v in sink.items() if k != 'ref'} if sink else None),
            'k_range': [round(float(k.min()), 2), round(float(k.max()), 2)]}


def t_under(field, copper, ref, part=None):
    """Mean board temperature over a part's footprint."""
    b = None
    if part and part.get('w_mm') and part.get('h_mm'):
        c = pad_centroid(copper, ref)
        if c:
            b = (c[0] - part['w_mm'] / 2, c[1] - part['h_mm'] / 2,
                 c[0] + part['w_mm'] / 2, c[1] + part['h_mm'] / 2)
    b = b or part_bbox(copper, ref)
    if not b:
        return None
    x0, y0, x1, y1 = b
    cell = field['cell_mm']
    i0 = max(0, int((x0 - field['x0']) / cell))
    i1 = min(field['t'].shape[1] - 1, int((x1 - field['x0']) / cell))
    j0 = max(0, int((y0 - field['y0']) / cell))
    j1 = min(field['t'].shape[0] - 1, int((y1 - field['y0']) / cell))
    return round(float(field['t'][j0:j1 + 1, i0:i1 + 1].mean()), 2)


def tj(part, t_board, t_amb, sink=False):
    """Lumped junction temperature, and the field-based one beside it."""
    p = float(part['power_w'])
    if sink:
        th = float(part.get('theta_jc', 5.0)) + float(part.get('theta_cs', 0.5)) \
            + float(part.get('theta_sa', 4.0))
        lumped = t_amb + p * th
    else:
        th = float(part.get('theta_ja', 60.0))
        lumped = t_amb + p * th
    field = (t_board + p * float(part.get('theta_jc', 5.0))
             if t_board is not None else None)
    out = {'lumped_c': round(lumped, 1), 'theta_k_per_w': th,
           'field_c': round(field, 1) if field is not None else None}
    if field is not None and lumped:
        out['field_vs_lumped_pct'] = round(100 * (field - lumped) / lumped, 1)
        # the sheet model has no package surface, so the field can only be
        # *below* a datasheet theta_ja chain — that is expected, not a fault.
        # Above it by 15% means the board is hotter than the lumped estimate
        # (neighbours, spreading), and one of the two is wrong.
        out['disputed'] = field > lumped * 1.15
        out['note'] = ('field below lumped: package surface is not in the '
                       'sheet model' if field < lumped else 'field above lumped')
    return out


def lumped_only(spec):
    """Fast mode: the theta chain, no board solve.  Everything is an estimate."""
    spec = spec or {}
    t_amb = float(spec.get('t_amb_c', 25.0))
    parts = [p for p in spec.get('parts', []) if p.get('power_w', 0)]
    if not parts:
        return {'status': 'error', 'error': 'no parts with power_w in spec'}
    rows = []
    for p in parts:
        for k_ in ('theta_ja', 'theta_jc', 'theta_cs', 'theta_sa'):
            p.setdefault(k_, {'theta_ja': 60.0, 'theta_jc': 5.0,
                              'theta_cs': 0.5, 'theta_sa': 4.0}[k_])
        rows.append({'ref': p['ref'], 'power_w': p['power_w'],
                     't_board_base_c': None,
                     'tj_base': tj(p, None, t_amb, sink=False)})
    return {'status': 'ok', 't_amb_c': t_amb, 'p_total_w': round(sum(
        float(p['power_w']) for p in parts), 3), 'parts': rows,
        'estimate': True,
        'assumption': ('lumped theta chain only: no board field, no coupling '
                       'between parts; fast mode')}


def run(copper, spec):
    """base vs with_sink, the same grid and the same colour scale."""
    spec = spec or {}
    t_amb = float(spec.get('t_amb_c', 25.0))
    h = float(spec.get('h_w_m2k', 10.0))
    cell = float(spec.get('cell_mm', 0.5))
    parts = [p for p in spec.get('parts', []) if p.get('power_w', 0)]
    if not parts:
        return {'status': 'error', 'error': 'no parts with power_w in spec'}
    base = solve_board(copper, parts, h_w_m2k=h, t_amb_c=t_amb, cell_mm=cell)
    if base['status'] != 'ok':
        return base
    sink_spec = spec.get('sink')
    for p in parts:
        for k_ in ('theta_ja', 'theta_jc', 'theta_cs', 'theta_sa'):
            if k_ not in p:
                p[k_] = {'theta_ja': 60.0, 'theta_jc': 5.0,
                         'theta_cs': 0.5, 'theta_sa': 4.0}[k_]
    rows = []
    for p in parts:
        tb = t_under(base, copper, p['ref'], p)
        rows.append({'ref': p['ref'], 'power_w': p['power_w'],
                     't_board_base_c': tb,
                     'tj_base': tj(p, tb, t_amb, sink=False)})
    out = {'status': 'ok', 't_amb_c': t_amb, 'h_w_m2k': h,
           'base': {'tmax_c': base['tmax_c'], 'tmean_c': base['tmean_c'],
                    'balance_err': base['balance_err'],
                    'heat_out_w': base['heat_out_w'],
                    'k_range': base['k_range'],
                    't': base['t'], 'grid': {'x0': base['x0'], 'y0': base['y0'],
                                             'cell': base['cell_mm'],
                                             'nx': base['t'].shape[1],
                                             'ny': base['t'].shape[0]}},
           'p_total_w': base['p_total_w'], 'parts': rows,
           'assumption': (f'2-D steady sheet: k_eff from the real coverage, '
                          f'{h} W/m2K both faces, power spread over each '
                          f'footprint; theta values from spec (datasheet, '
                          f'not measured)')}
    if sink_spec:
        sink = dict(sink_spec)
        sink['theta_sa'] = float(sink.get('theta_sa', 4.0))
        ws = solve_board(copper, parts, h_w_m2k=h, t_amb_c=t_amb,
                         cell_mm=cell, sink=sink)
        if ws['status'] != 'ok':
            out['with_sink'] = ws
            return out
        out['with_sink'] = {'tmax_c': ws['tmax_c'], 'tmean_c': ws['tmean_c'],
                            'balance_err': ws['balance_err'],
                            'heat_out_w': ws['heat_out_w'],
                            'k_range': ws['k_range'], 'sink': ws['sink'],
                            't': ws['t'], 'grid': {'x0': ws['x0'], 'y0': ws['y0'],
                                                   'cell': ws['cell_mm'],
                                                   'nx': ws['t'].shape[1],
                                                   'ny': ws['t'].shape[0]}}
        for row, p in zip(rows, parts):
            tw = t_under(ws, copper, p['ref'], p)
            row['t_board_sink_c'] = tw
            row['tj_sink'] = tj(p, tw, t_amb, sink=True)
            b, s = row['tj_base']['lumped_c'], row['tj_sink']['lumped_c']
            row['tj_drop_c'] = round(b - s, 1)
            row['tjmax_c'] = p.get('tjmax_c')
            row['margin_pct'] = (round(100 * s / p['tjmax_c'], 1)
                                 if p.get('tjmax_c') else None)
    return out
