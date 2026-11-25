"""Return-path audit on the real copper: two questions, two measurements.

There are two different "return paths" and a DC solve only answers one:

  * **Power return (DC).**  Where the GND current actually goes between the
    source and the load, and what it costs.  A plane makes this a solved
    field: `solve_return` drives the GND net between the driver's and the
    load's GND pads and reports the drop, the current density and the
    current-weighted mean return distance.  This is what the plane is for.

  * **Signal return (AC).**  At the frequencies a signal cares about, the
    return current is the image current under the trace — it follows the
    trace, not the least-resistance path.  A DC solve puts the current on
    the least-resistance path *away* from a short trace, so scoring signal
    return by a DC field would be wrong (we measured it: pouring a plane
    makes the DC "fraction within 3h" worse, and that is not a defect).
    What a board can actually fail is **availability**: is there continuous
    GND copper within 3h under the whole route, or does a slot/void force
    the image current to detour?  `availability` measures that on the real
    raster: copper share in a 3h disk around each sample, and the longest
    stretch with no GND copper near it.

    solve_return(copper, signal, driver_ref, load_ref, current_a, h_mm)
    availability(copper, signal, gnd_net, h_mm)
    dc_fraction(field, distance, h)  # diagnostic, power only

`h_mm` is the signal-to-plane spacing from the stackup.
"""
from __future__ import annotations

import math

import numpy as np

from . import copper_graph as cg


def _gnd_pads(copper, ref, gnd_net='GND'):
    nc = cg.net_by_name(copper, gnd_net)
    if nc is None:
        return []
    return [(p['x'], p['y']) for p in copper.pads.get(nc, [])
            if p.get('ref') == ref]


def distance_field(copper, signal, cell_mm=None):
    """Distance in mm from every cell to the signal's own copper."""
    from scipy.ndimage import distance_transform_edt
    cell = cell_mm or copper.cell_mm
    covs = cg.net_cover(copper, signal)
    fwd = np.zeros((copper.ny, copper.nx), bool)
    for c in covs:
        fwd |= c >= cg.MIN_COVER
    if not fwd.any():
        return None
    return distance_transform_edt(~fwd) * cell


def dc_fraction(distance, jmag, plane, h_mm):
    """Current-weighted share of the DC return within k*h of the trace."""
    w = jmag[plane].astype(np.float64)
    dd = distance[plane]
    tot = float(w.sum())
    if tot <= 0:
        return {}
    by = {}
    for k in (1, 3, 10):
        by[f'fraction_{k}h'] = round(float(w[dd <= k * h_mm].sum() / tot), 3)
    order = np.argsort(dd)
    cw = np.cumsum(w[order]) / tot
    by['d90_mm'] = round(float(dd[order][np.searchsorted(cw, 0.90)]), 3)
    by['d_mean_mm'] = round(float((w * dd).sum() / tot), 3)
    return by


def solve_return(copper, signal, driver_ref, load_ref, current_a,
                 h_mm=1.6, gnd_net='GND'):
    """DC solve of the GND net between the driver's and load's GND pads."""
    src = _gnd_pads(copper, driver_ref, gnd_net)
    snk = _gnd_pads(copper, load_ref, gnd_net)
    if not src or not snk:
        return {'status': 'error',
                'error': f'no {gnd_net} pads on {driver_ref!r} or {load_ref!r}',
                'signal': signal, 'driver': driver_ref, 'load': load_ref}
    r = cg.solve(copper, gnd_net, current_a, v_rail=0.0,
                 sources=src, sinks=snk)
    if r['status'] != 'ok':
        return {'status': 'error', 'error': r.get('error'),
                'signal': signal, 'driver': driver_ref, 'load': load_ref}
    d = distance_field(copper, signal)
    if d is None:
        return {'status': 'error', 'error': f'signal {signal!r} has no copper',
                'signal': signal}
    plane = np.zeros((copper.ny, copper.nx), bool)
    jt = np.zeros((copper.ny, copper.nx), np.float32)
    for c, j in zip(cg.net_cover(copper, gnd_net), r['j_by_layer']):
        plane |= c >= cg.MIN_COVER
        jt = np.maximum(jt, j)
    dc = dc_fraction(d, jt, plane, h_mm)
    return {'status': 'ok', 'signal': signal,
            'driver': driver_ref, 'load': load_ref,
            'gnd_net': gnd_net, 'current_a': current_a, 'h_mm': h_mm,
            'dc': dc,
            'plane_area_mm2': round(plane_area(copper, gnd_net), 1),
            'gnd_drop_mv': round(r['drop_v'] * 1e3, 3),
            'gnd_resistance_mohm': round(1e3 * r['drop_v'] / current_a, 3),
            'gnd_j_p95': r['j_p95_a_mm2'],
            'gnd_pads_source': len(src), 'gnd_pads_sink': len(snk),
            'grid': {'x0': copper.x0_mm, 'y0': copper.y0_mm,
                     'cell': copper.cell_mm, 'nx': copper.nx, 'ny': copper.ny},
            'assumption': (f'DC solve on the real {gnd_net} copper between '
                           f'{driver_ref} and {load_ref} GND pads; h={h_mm} mm; '
                           f'the dc block is a power-return diagnostic and is '
                           f'not used to score signal return')}


def plane_area(copper, gnd_net='GND'):
    a = 0.0
    for z in copper.zones:
        if z['net'] != gnd_net:
            continue
        for (pts, _holes) in z['areas']:
            if len(pts) >= 3:
                a += abs(sum(pts[k][0] * pts[(k + 1) % len(pts)][1]
                             - pts[(k + 1) % len(pts)][0] * pts[k][1]
                             for k in range(len(pts)))) / 2.0
    return a


def nearest_foreign(copper, x, y, layer, exclude=('GND',)):
    """Which net's copper sits nearest (x,y) on `layer` — the blocker."""
    best = None
    for nc, rows in copper.segs.items():
        nm = copper.nets.get(nc, '')
        if nm in exclude:
            continue
        for (x1, y1, x2, y2, w, lay) in rows:
            if lay != layer:
                continue
            d = max(_seg_dist_point(x1, y1, x2, y2, x, y) - w / 2, 0.0)
            if best is None or d < best[0]:
                best = (d, nm, (round((x1 + x2) / 2, 1), round((y1 + y2) / 2, 1)))
    for nc, plist in copper.pads.items():
        nm = copper.nets.get(nc, '')
        if nm in exclude:
            continue
        for p in plist:
            if layer not in p['layers']:
                continue
            d = max(math.hypot(p['x'] - x, p['y'] - y) - max(p['w'], p['h']) / 2, 0.0)
            if best is None or d < best[0]:
                best = (d, nm, (round(p['x'], 1), round(p['y'], 1)))
    return None if best is None else {'d_mm': round(best[0], 2),
                                      'net': best[1], 'at': best[2]}


def _seg_dist_point(x1, y1, x2, y2, px, py):
    dx, dy = x2 - x1, y2 - y1
    L2 = dx * dx + dy * dy
    if L2 <= 0:
        return math.hypot(px - x1, py - y1)
    t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / L2))
    return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))


def plane_voids(copper, gnd_net='GND', min_area_mm2=1.0):
    """Coverage of the plane inside the board, and the biggest voids.

    A void that is not near the edge is usually an island the filler removed:
    copper that no GND pad/via reaches.  Naming the footprints inside it says
    which part is sitting on a hole in the plane.
    """
    from scipy.ndimage import binary_erosion, label
    plane = np.zeros((copper.ny, copper.nx), bool)
    for c in cg.net_cover(copper, gnd_net):
        plane |= c >= cg.MIN_COVER
    domain = copper.board_mask
    if domain is None:
        domain = np.ones((copper.ny, copper.nx), bool)
    cell = copper.cell_mm
    area_cell = cell * cell
    cov = float((plane & domain).sum()) * area_cell
    board = float(domain.sum()) * area_cell
    # erode by 1.5 mm so the unfilled margin ring does not swallow every
    # interior hole into one perimeter-connected void
    inner = binary_erosion(domain, iterations=max(1, int(round(1.5 / cell))))
    lab, n = label(inner & ~plane)
    out = []
    for k in range(1, n + 1):
        m = lab == k
        a = float(m.sum()) * area_cell
        if a < min_area_mm2:
            continue
        ys, xs = np.nonzero(m)
        bx0, by0 = copper.x0_mm + xs.min() * cell, copper.y0_mm + ys.min() * cell
        bx1, by1 = copper.x0_mm + xs.max() * cell, copper.y0_mm + ys.max() * cell
        refs = sorted({p['ref'] for plist in copper.pads.values() for p in plist
                       if bx0 <= p['x'] <= bx1 and by0 <= p['y'] <= by1})
        out.append({'area_mm2': round(a, 1),
                    'at': (round(copper.x0_mm + xs.mean() * cell, 1),
                           round(copper.y0_mm + ys.mean() * cell, 1)),
                    'bbox_mm': [round(bx0, 1), round(by0, 1), round(bx1, 1), round(by1, 1)],
                    'footprints': refs[:8]})
    out.sort(key=lambda r: -r['area_mm2'])
    return {'coverage_pct': round(100.0 * cov / board, 1),
            'plane_area_mm2': round(cov, 1), 'board_area_mm2': round(board, 1),
            'voids': out[:5]}


def availability(copper, signal, gnd_net='GND', h_mm=1.6,
                 max_samples=120, cell_mm=None):
    """Can the image current follow this trace?  Measured on the real copper.

    For a sample of the signal's cells: the share of a 3h disc that is GND
    copper, the distance to the nearest GND copper, and — for the worst
    stretches — which net's copper is in the way.  A continuous plane gives a
    high share and a clearance-sized distance; a slot under the route shows up
    as samples with a large distance, a low share, and a named blocker.
    """
    cell = cell_mm or copper.cell_mm
    covs = cg.net_cover(copper, signal)
    fwd = np.zeros((copper.ny, copper.nx), bool)
    for c in covs:
        fwd |= c >= cg.MIN_COVER
    if not fwd.any():
        return {'status': 'error', 'error': f'signal {signal!r} has no copper'}
    gplane = np.zeros((copper.ny, copper.nx), bool)
    for c in cg.net_cover(copper, gnd_net):
        gplane |= c >= cg.MIN_COVER
    ys, xs = np.nonzero(fwd & ~gplane)
    if not ys.size:
        ys, xs = np.nonzero(fwd)
    step = max(1, int(math.ceil(len(ys) / max_samples)))
    ys, xs = ys[::step], xs[::step]
    from scipy.ndimage import distance_transform_edt
    if gplane.any():
        dgnd = distance_transform_edt(~gplane) * cell
    else:
        dgnd = np.full((copper.ny, copper.nx), 1e3, np.float32)
    r_mm = 3.0 * h_mm
    r_cells = r_mm / cell
    yy, xx = np.mgrid[-int(r_cells) - 1:int(r_cells) + 2,
                      -int(r_cells) - 1:int(r_cells) + 2]
    disk = (yy * yy + xx * xx) <= r_cells * r_cells
    domain = copper.board_mask
    if domain is None:
        domain = np.ones((copper.ny, copper.nx), bool)
    shares, gaps = [], []
    for j, i in zip(ys, xs):
        j0, j1 = max(0, j - int(r_cells) - 1), min(copper.ny, j + int(r_cells) + 2)
        i0, i1 = max(0, i - int(r_cells) - 1), min(copper.nx, i + int(r_cells) + 2)
        sub = gplane[j0:j1, i0:i1]
        dom = domain[j0:j1, i0:i1]
        dj0, di0 = j0 - (j - int(r_cells) - 1), i0 - (i - int(r_cells) - 1)
        dsub = disk[dj0:dj0 + sub.shape[0], di0:di0 + sub.shape[1]] & dom
        if not dsub.any():
            continue
        shares.append(float(sub[dsub].sum()) / float(dsub.sum()))
        gaps.append(float(dgnd[j, i]))
    sh = np.asarray(shares) if shares else np.zeros(1)
    gp = np.asarray(gaps) if gaps else np.zeros(1)
    lay = None
    for z in sorted(copper.zones, key=lambda z: -plane_area(copper)):
        if z['net'] == gnd_net:
            lay = z['layer']
            break
    blockers = []
    if lay:
        for idx in np.argsort(gp)[-3:][::-1]:
            j, i = int(ys[idx]), int(xs[idx])
            x, y = copper.x0_mm + (i + 0.5) * cell, copper.y0_mm + (j + 0.5) * cell
            blockers.append({'at': (round(x, 1), round(y, 1)),
                             'gap_mm': round(float(gp[idx]), 2),
                             'nearest_foreign': nearest_foreign(
                                 copper, x, y, lay, exclude=(gnd_net, signal))})
    return {'status': 'ok', 'signal': signal, 'gnd_net': gnd_net,
            'h_mm': h_mm, 'window_mm': round(2 * r_mm, 2),
            'plane_layer': lay,
            'samples': int(len(shares)),
            'gnd_share_mean': round(float(sh.mean()), 3),
            'gnd_share_min': round(float(sh.min()), 3),
            'samples_below_50pct': int((sh < 0.5).sum()),
            'mean_gap_mm': round(float(gp.mean()), 2),
            'max_gap_mm': round(float(gp.max()), 2),
            'p90_gap_mm': round(float(np.percentile(gp, 90)), 2),
            'blockers': blockers,
            'plane_area_mm2': round(plane_area(copper, gnd_net), 1),
            'assumption': ('image-current availability: fraction of a 3h disc '
                           'around each route sample that is GND copper, and the '
                           'distance from the route to the nearest GND copper; '
                           'a geometric proxy for AC return, not a field solve')}


def analytic_fraction(h_ratio=3.0):
    """Infinite-plane benchmark: (2/pi) atan(h_ratio)."""
    return (2 / math.pi) * math.atan(h_ratio)
