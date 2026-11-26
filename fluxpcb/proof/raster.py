"""Copper coverage equivalence: the plotted artifact vs the board state, as sets.

The item-by-item bijection is exact but brittle: KiCad draws some pads as
stroked outlines and arcs as polylines, so one board pad can be several plot
primitives.  This kernel answers the set question instead: rasterise the
copper of both sides on the same grid and require that neither side has a
cell the other lacks (within one cell for boundary rounding).  The
resolution is declared and recorded, not tuned per board; the negative
control is a translated layer, which leaves whole slivers unmatched.

Clearance does not need to be re-run on the artifact when the sets are equal:
a gap in the plot is a gap in the board.  The remaining predicates (width,
annular, drill, hole-to-hole, edge) still run on the plot's own geometry.
"""
from __future__ import annotations

import math

import numpy as np

DEFAULT_RES = 10000        # nm (10 um)
MAX_CELLS = 6000           # per axis; the resolution coarsens beyond this
MAX_SPAN = 250_000_000     # nm (250 mm): beyond this coverage is unproven, not passed


def _item_bbox(it):
    sh = it['shape']
    if sh['kind'] == 'seg':
        h = sh['w'] / 2.0
        return (min(sh['x1'], sh['x2']) - h, min(sh['y1'], sh['y2']) - h,
                max(sh['x1'], sh['x2']) + h, max(sh['y1'], sh['y2']) + h)
    if sh['kind'] == 'circle':
        return (sh['cx'] - sh['r'], sh['cy'] - sh['r'],
                sh['cx'] + sh['r'], sh['cy'] + sh['r'])
    xs = [p[0] for p in sh['pts']]
    ys = [p[1] for p in sh['pts']]
    return (min(xs), min(ys), max(xs), max(ys))


def _fill_circle(g, bbox, res, cx, cy, r):
    x0, y0, x1, y1 = bbox
    H, W = g.shape
    c0 = max(0, int((cx - r - x0) / res)); c1 = min(W - 1, int((cx + r - x0) / res) + 1)
    r0 = max(0, int((cy - r - y0) / res)); r1 = min(H - 1, int((cy + r - y0) / res) + 1)
    if c1 < c0 or r1 < r0:
        return
    xs = x0 + (np.arange(c0, c1 + 1) + 0.5) * res
    ys = y0 + (np.arange(r0, r1 + 1) + 0.5) * res
    m = (xs[None, :] - cx) ** 2 + (ys[:, None] - cy) ** 2 <= r * r
    g[r0:r1 + 1, c0:c1 + 1] |= m


def _fill_poly(g, bbox, res, pts):
    x0, y0, x1, y1 = bbox
    H, W = g.shape
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    c0 = max(0, int((min(xs) - x0) / res)); c1 = min(W - 1, int((max(xs) - x0) / res) + 1)
    r0 = max(0, int((min(ys) - y0) / res)); r1 = min(H - 1, int((max(ys) - y0) / res) + 1)
    n = len(pts)
    for rr in range(r0, r1 + 1):
        yc = y0 + (rr + 0.5) * res
        cross = []
        for k in range(n):
            a, b = pts[k], pts[(k + 1) % n]
            if (a[1] > yc) != (b[1] > yc):
                t = (yc - a[1]) / (b[1] - a[1])
                cross.append(a[0] + t * (b[0] - a[0]))
        if not cross:
            continue
        cross.sort()
        for k in range(0, len(cross) - 1, 2):
            lo = max(0, int((cross[k] - x0) / res))
            hi = min(W - 1, int((cross[k + 1] - x0) / res))
            if hi >= lo:
                g[rr, lo:hi + 1] = True


def _fill_seg(g, bbox, res, x1, y1, x2, y2, w):
    hw = w / 2.0
    dx, dy = x2 - x1, y2 - y1
    L = math.hypot(dx, dy)
    if L == 0:
        _fill_circle(g, bbox, res, x1, y1, hw)
        return
    nx, ny = -dy / L * hw, dx / L * hw
    _fill_poly(g, bbox, res, [(x1 + nx, y1 + ny), (x2 + nx, y2 + ny),
                              (x2 - nx, y2 - ny), (x1 - nx, y1 - ny)])
    _fill_circle(g, bbox, res, x1, y1, hw)
    _fill_circle(g, bbox, res, x2, y2, hw)


def raster(items, bbox, res):
    x0, y0, x1, y1 = bbox
    H = max(1, int(math.ceil((y1 - y0) / res)) + 1)
    W = max(1, int(math.ceil((x1 - x0) / res)) + 1)
    g = np.zeros((H, W), dtype=bool)
    for it in items:
        sh = it['shape']
        k = sh['kind']
        if k == 'seg':
            _fill_seg(g, bbox, res, sh['x1'], sh['y1'], sh['x2'], sh['y2'],
                      sh['w'])
        elif k == 'circle':
            _fill_circle(g, bbox, res, sh['cx'], sh['cy'], sh['r'])
        else:
            _fill_poly(g, bbox, res, [(p[0], p[1]) for p in sh['pts']])
    return g


def _dilate1(g):
    out = g.copy()
    out[1:, :] |= g[:-1, :]
    out[:-1, :] |= g[1:, :]
    out[:, 1:] |= g[:, :-1]
    out[:, :-1] |= g[:, 1:]
    out[1:, 1:] |= g[:-1, :-1]
    out[:-1, :-1] |= g[1:, 1:]
    out[1:, :-1] |= g[:-1, 1:]
    out[:-1, 1:] |= g[1:, :-1]
    return out


def coverage(board_state, gst, layers=None, res=DEFAULT_RES):
    """Per-layer set equality of copper (within one cell)."""
    layers = layers or [l for l in board_state['layers'] if l in gst['layers']]
    bitems = [i for i in board_state['items']
              if any(l in layers for l in i['layers'])]
    gitems = [i for i in gst['items']
              if i['layers'] and any(l in layers for l in i['layers'])
              and not i.get('npth')]
    if not bitems and not gitems:
        return {'ok': True, 'layers': {}, 'res_nm': res}
    boxes = [_item_bbox(i) for i in bitems + gitems]
    x0 = min(b[0] for b in boxes); y0 = min(b[1] for b in boxes)
    x1 = max(b[2] for b in boxes); y1 = max(b[3] for b in boxes)
    span = max(x1 - x0, y1 - y0)
    if span > MAX_SPAN:
        return {'ok': None, 'layers': {}, 'res_nm': res,
                'reason': f'board span {span / 1e6:.0f} mm exceeds the '
                          f'coverage budget (250 mm); coverage is unknown'}
    if span / res > MAX_CELLS:
        res = int(math.ceil(span / MAX_CELLS))
    out, ok = {}, True
    for layer in layers:
        b = [i for i in bitems if layer in i['layers']]
        g = [i for i in gitems if layer in i['layers']]
        if not b and not g:
            continue
        gb = raster(b, (x0, y0, x1, y1), res)
        gg = raster(g, (x0, y0, x1, y1), res)
        miss = gg & ~_dilate1(gb)          # plot copper the board lacks
        extra = gb & ~_dilate1(gg)         # board copper the plot lacks
        n_miss, n_extra = int(miss.sum()), int(extra.sum())
        if n_miss or n_extra:
            ok = False
        out[layer] = {'cells': int(gb.sum()), 'gerber_not_board': n_miss,
                      'board_not_gerber': n_extra}
    return {'ok': ok, 'layers': out, 'res_nm': res}
