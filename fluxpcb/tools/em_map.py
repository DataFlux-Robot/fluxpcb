"""Susceptibility map: a per-cell coupling field from the real emitters.

This is a proxy, and it says so in every result.  Each emitter is its own
copper (clock/SPI/UART/PWM nets named in the spec); coupling falls as

    c(r) = r0^2 / (r0^2 + r^2)          for r <= 10*r0     (near field)
    c(r) = (1/101) * 10*r0 / r          beyond             (far field, 1/r)

which is the reference kernel: flat on top of the aggressor, 1/r once the
distance is large.  A GND plane layer *between* the emitter's layer and the
victim's costs 30 dB; a shield can over the victim costs 20 dB.  Nothing
here solves Maxwell's equations — `status` is `estimate` and an openEMS run
is the upgrade path.

    coupling(r_mm) -> linear
    run(copper, spec) -> {map_db, grid, victims, emitters, shield_db}

The map is normalised so 0 dB is the strongest coupling anywhere in the
board; victim levels are read off that map, and the worst aggressor is
named, because "the UART is at -31 dB" is only useful with "from the pump
PWM".
"""
from __future__ import annotations

import math

import numpy as np

from . import copper_graph as cg

R0_MM = 1.0
PLANE_DB = -30.0
SHIELD_DB = -20.0


def coupling(r_mm):
    r = np.asarray(r_mm, dtype=np.float64)
    near = R0_MM ** 2 / (R0_MM ** 2 + r ** 2)
    far = (1.0 / 101.0) * (10.0 * R0_MM / np.maximum(r, 1e-9))
    return np.where(r <= 10 * R0_MM, near, far)


def _coarse(cov, k):
    ny2, nx2 = (cov.shape[0] // k) * k, (cov.shape[1] // k) * k
    return cov[:ny2, :nx2].reshape(ny2 // k, k, nx2 // k, k).mean(axis=(1, 3))


def _layers_between(copper, a, b):
    ia = copper.layer_index(a)
    ib = copper.layer_index(b)
    if ia is None or ib is None:
        return []
    lo, hi = min(ia, ib), max(ia, ib)
    return copper.layers[lo + 1:hi]


def run(copper, spec=None, cell_mm=0.5, gnd_net='GND'):
    """Per-cell coupling dB map, victim levels, and who couples into them."""
    from scipy.ndimage import distance_transform_edt
    spec = spec or {}
    k = max(1, int(round(cell_mm / copper.cell_mm)))
    ny, nx = copper.ny // k, copper.nx // k
    domain = np.zeros((ny, nx), bool)
    if copper.board_mask is not None:
        domain = _coarse(copper.board_mask.astype(np.float32), k) >= 0.5
    else:
        domain[:] = True
    plane_layers = {z['layer'] for z in copper.zones if z['net'] == gnd_net}
    emitters = [e for e in spec.get('emitters', []) if e.get('net')]
    fallback = False
    if not emitters:
        # net names can carry no function (Net-(U3-Pad13)): fall back to the
        # longest copper runs, at a declared low level, and say so
        emitters = [e for e in spec.get('emitter_fallback', []) if e.get('net')]
        fallback = bool(emitters)
    if not emitters:
        return {'status': 'skipped',
                'error': 'no net names read as an emitter and no fallback'}
    total = np.zeros((ny, nx), np.float64)
    per = {}
    for em in emitters:
        covs = cg.net_cover(copper, em['net'])
        if not any((c >= cg.MIN_COVER).any() for c in covs):
            continue
        # each emitter's own layer(s); the coupling is computed on the grid
        mask = np.zeros((copper.ny, copper.nx), bool)
        for c in covs:
            mask |= c >= cg.MIN_COVER
        if not mask.any():
            continue
        d = distance_transform_edt(~mask) * copper.cell_mm
        dc = _coarse(d, k)
        c = coupling(dc)
        amp = 10 ** (float(em.get('amp_db', 0.0)) / 10.0)
        # a plane between the emitter and the victim's layer costs 30 dB;
        # the victim layer is resolved per victim below, so the map records
        # the unshielded field and victims apply their own factor
        per[em['net']] = {'c': c, 'amp': amp, 'layer': em.get('layer')}
        total += amp * c
    if not per:
        return {'status': 'error', 'error': 'no emitter copper found'}
    peak = float(total[domain].max()) if domain.any() else float(total.max())
    with np.errstate(divide='ignore'):
        map_db = 10.0 * np.log10(np.maximum(total, 1e-30) / max(peak, 1e-30))
    map_db = np.clip(map_db, -120.0, 0.0)

    shield = spec.get('shield') or {}
    shell_db = SHIELD_DB if shield.get('exists') else 0.0
    victims = []
    for v in spec.get('victims', []):
        net = v.get('net')
        if not net:
            continue
        covs = cg.net_cover(copper, net)
        vmask = np.zeros((copper.ny, copper.nx), bool)
        for c in covs:
            vmask |= c >= cg.MIN_COVER
        if not vmask.any():
            continue
        vc = _coarse(vmask.astype(np.float32), k) >= 0.5
        best = None
        for name, e in per.items():
            if name == net:
                continue
            e_db = 0.0
            if e.get('layer') and v.get('layer'):
                for lay in _layers_between(copper, e['layer'], v['layer']):
                    if lay in plane_layers:
                        e_db += PLANE_DB
            e_db += shell_db
            lev = 10 * math.log10(max(float((e['amp'] * e['c'][vc]).max()), 1e-30)) - 10 * math.log10(max(peak, 1e-30)) + e_db
            if best is None or lev > best[0]:
                best = (lev, name)
        if best is None:
            continue
        lev = best[0]
        grade = 'high' if lev >= -20 else ('medium' if lev >= -35 else 'low')
        victims.append({'net': net, 'level_db': round(lev, 1), 'grade': grade,
                        'worst_aggressor': best[1],
                        'shield_db': shell_db})
    return {'status': 'ok', 'cell_mm': cell_mm,
            'grid': {'x0': copper.x0_mm, 'y0': copper.y0_mm, 'cell': cell_mm,
                     'nx': nx, 'ny': ny},
            'map_db': map_db, 'domain': domain,
            'peak': round(peak, 4),
            'emitters': [{'net': e['net'], 'amp_db': float(e.get('amp_db', 0.0)),
                          'f_hz': e.get('f_hz')} for e in emitters],
            'victims': victims, 'shield_db': shell_db,
            'plane_layers': sorted(plane_layers),
            'fallback_emitters': fallback,
            'status_caveat': 'estimate',
            'assumption': ('coupling kernel r0^2/(r0^2+r^2) near, 1/r far '
                           f'(r0={R0_MM} mm); -30 dB per plane layer between '
                           'emitter and victim; -20 dB if a shield exists; '
                           'relative dB (0 = strongest coupling on the board), '
                           'not an absolute field'
                           + ('; emitters are a FALLBACK (longest nets, function '
                              'unknown, -10 dB), not named sources' if fallback
                              else ''))}
