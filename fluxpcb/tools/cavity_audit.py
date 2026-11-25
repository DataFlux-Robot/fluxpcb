"""Cavity modes of the real plane shape, and whether anything excites them.

A parallel-plate cavity's modes solve the 2-D Helmholtz equation with
magnetic-wall (Neumann) boundaries at the plane edges:

    -laplacian(phi) = k^2 phi,   d phi/dn = 0

On the actual poured shape that is a sparse eigenproblem on the coverage
mask, which is what this module solves (shift-invert near zero).  The mask
matters: a plane with a hole under the MCU is not a rectangle.

Two honesty notes this module must carry:

  * a cavity needs a **pair** of planes.  On a 2-layer board with one GND
    pour there is no counter-electrode; running the solver treats the other
    layer as an ideal reference at h — a what-if, not a board property.
  * a mode is only a risk if an emitter's harmonic lands on it AND the
    emitter sits on an antinode.  Both are reported; neither is assumed.

    modes(mask, cell_mm, er, n) -> {modes: [{f_hz, phi}], ...}
    rect_analytic(L_mm, W_mm, er, m, n) -> Hz
    run(copper, spec) -> {applicable, modes, pairs, needed_information}
"""
from __future__ import annotations

import math

import numpy as np

from . import copper_graph as cg

C0 = 299792458.0


def downsample(mask, cell_mm, max_side=64):
    """Block-average a bool mask to at most max_side on the long side."""
    ny, nx = mask.shape
    k = max(1, int(math.ceil(max(ny, nx) / float(max_side))))
    ny2, nx2 = (ny // k) * k, (nx // k) * k
    m = mask[:ny2, :nx2].reshape(ny2 // k, k, nx2 // k, k).mean(axis=(1, 3))
    return m >= 0.5, cell_mm * k


def modes(mask, cell_mm, er=4.4, n=4):
    """Neumann modes of the copper mask.  Frequencies in Hz."""
    import scipy.sparse as sp
    from scipy.sparse.linalg import eigsh

    m, cell = downsample(mask, cell_mm)
    ny, nx = m.shape
    idx = -np.ones((ny, nx), int)
    ys, xs = np.nonzero(m)
    idx[ys, xs] = np.arange(len(ys))
    N = len(ys)
    if N < 16:
        return {'status': 'error', 'error': 'plane mask too small for modes'}
    rows, cols, vals = [], [], []
    for j, i in zip(ys, xs):
        k = idx[j, i]
        deg = 0
        for (dj, di) in ((0, 1), (1, 0), (0, -1), (-1, 0)):
            jj, ii = j + dj, i + di
            if 0 <= jj < ny and 0 <= ii < nx and idx[jj, ii] >= 0:
                rows.append(k)
                cols.append(idx[jj, ii])
                vals.append(-1.0)
                deg += 1
        rows.append(k)
        cols.append(k)
        vals.append(float(deg))
    L = sp.coo_matrix((vals, (rows, cols)), shape=(N, N)).tocsc()
    kk = min(n + 1, N - 2)
    try:
        w, v = eigsh(L, k=kk, sigma=-1e-6, which='LM')
    except Exception as e:
        return {'status': 'error', 'error': f'eigsh: {type(e).__name__}: {e}'}
    order = np.argsort(w)
    w, v = w[order], v[:, order]
    out = []
    for t in range(kk):
        mu = float(w[t])
        if mu < 1e-6:
            continue                      # the constant mode
        f = C0 / (2 * math.pi * math.sqrt(er)) * math.sqrt(mu) / (cell * 1e-3)
        phi = np.zeros((ny, nx))
        phi[ys, xs] = v[:, t]
        mx = float(np.abs(phi).max())
        if mx > 0:
            phi = phi / mx
        out.append({'f_hz': f, 'f_ghz': round(f / 1e9, 4), 'phi': phi,
                    'cell_mm': cell,
                    'nodal_frac': round(float((np.abs(phi) < 0.15).mean()), 3)})
        if len(out) >= n:
            break
    return {'status': 'ok', 'modes': out, 'mask_cells': int(N),
            'cell_mm': cell, 'shape': [ny, nx]}


def rect_analytic(L_mm, W_mm, er=4.4, m=1, n=0):
    """f_mn of a rectangular Neumann cavity (the anchor)."""
    a, b = L_mm * 1e-3, W_mm * 1e-3
    return C0 / (2 * math.sqrt(er)) * math.sqrt((m / a) ** 2 + (n / b) ** 2)


def _amp_at(mo, copper, xy):
    x, y = xy
    cell = mo['cell_mm']
    i = int((x - copper.x0_mm) / cell)
    j = int((y - copper.y0_mm) / cell)
    phi = mo['phi']
    if 0 <= j < phi.shape[0] and 0 <= i < phi.shape[1]:
        return round(float(abs(phi[j, i])), 3)
    return None


def run(copper, spec=None, gnd_net='GND', er=4.4, n_modes=4):
    """Cavity modes on the GND plane shape, paired against emitter harmonics.

    spec: {'er': 4.4, 'plane_pair': bool, 'tol': 0.10,
           'emitters': [{'net', 'f_hz', 'harmonics', 'at'}]}
    """
    spec = spec or {}
    er = float(spec.get('er', er))
    lay = None
    for z in copper.zones:
        if z['net'] == gnd_net:
            lay = z['layer']
            break
    if lay is None:
        return {'status': 'error', 'applicable': False,
                'error': f'no {gnd_net} plane on this board'}
    covs = cg.net_cover(copper, gnd_net)
    plane = covs[copper.layers.index(lay)] >= cg.MIN_COVER
    r = modes(plane, copper.cell_mm, er=er, n=n_modes)
    if r['status'] != 'ok':
        return {'status': 'error', 'applicable': False, 'error': r['error']}
    tol = float(spec.get('tol', 0.10))
    pairs = []
    for em in spec.get('emitters', []):
        f0 = float(em.get('f_hz', 0.0))
        if f0 <= 0:
            continue
        for h in range(1, int(em.get('harmonics', 5)) + 1):
            fh = f0 * h
            for mo in r['modes']:
                rel = abs(mo['f_hz'] - fh) / fh
                if rel <= tol:
                    pairs.append({'emitter': em.get('net'), 'harmonic': h,
                                  'f_hz': fh, 'mode_ghz': mo['f_ghz'],
                                  'delta_pct': round(100 * (mo['f_hz'] - fh) / fh, 2),
                                  'emitter_amp': (_amp_at(mo, copper, em['at'])
                                                  if em.get('at') else None),
                                  'risk': (abs(mo['f_hz'] - fh) / fh <= tol and
                                           (not em.get('at') or
                                            (_amp_at(mo, copper, em['at']) or 0) > 0.7))})
    return {'status': 'ok', 'applicable': bool(spec.get('plane_pair', False)),
            'plane_layer': lay, 'er': er,
            'plane_area_mm2': round(float(plane.sum()) * (copper.cell_mm ** 2), 1),
            'modes': [{'f_hz': m['f_hz'], 'f_ghz': m['f_ghz'],
                       'nodal_frac': m['nodal_frac']} for m in r['modes']],
            'mode_fields': r['modes'], 'pairs': pairs,
            'assumption': (f'Neumann modes on the filled {lay} copper mask '
                           f'({r["mask_cells"]} cells at {r["cell_mm"]} mm); '
                           f'er={er}; holes in the pour are in the mask'),
            'needed_information': ([] if spec.get('plane_pair', False) else
                                   ['a second plane (power/ground pair) on an '
                                    'adjacent layer, or a ground pour on the '
                                    'other side: a single plane has no cavity'])}
