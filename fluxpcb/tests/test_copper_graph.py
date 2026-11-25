"""Analytic anchors for the audit-grade copper solver.

A synthetic strip has a closed-form answer: J = I/(w*t) and R = rho*L/(w*t).
If the solver cannot reproduce that to a few percent, nothing it says about a
real board is worth reporting.  These go through the raster path (PIL only,
no pcbnew), so they also pin the rasterisation.
"""
from __future__ import annotations

import numpy as np

from fluxpcb.tools import copper_graph as cg

CELL = 0.5
W_CELLS = 11
W_MM = W_CELLS * CELL
T_MM = 0.035
I = 1.0


def _strip(nx=100, ny=30):
    cop = cg.Copper(board_path='synthetic', board_fp='0' * 16, cell_mm=CELL,
                    x0_mm=0.0, y0_mm=0.0, nx=nx, ny=ny, layers=['F.Cu'])
    cop.nets = {1: 'V'}
    yc = (10 + W_CELLS / 2) * CELL
    cop.segs = {1: [(2 * CELL, yc, (nx - 2) * CELL, yc, W_MM, 'F.Cu')]}
    cop.pads = {1: [
        dict(ref='A', pad='1', x=2 * CELL, y=yc, w=3.0, h=W_MM, rot=0.0,
             layers=['F.Cu']),
        dict(ref='B', pad='1', x=(nx - 3) * CELL, y=yc, w=3.0, h=W_MM, rot=0.0,
             layers=['F.Cu'])]}
    return cop, yc


def test_uniform_strip_current_density():
    cop, yc = _strip()
    r = cg.solve(cop, 'V', I, sources=[(2 * CELL, yc)],
                 sinks=[((100 - 3) * CELL, yc)])
    assert r['status'] == 'ok', r
    j_geom = I / (W_MM * T_MM)
    # the interior carries exactly I/(w*t); the hot-spot statistic sees the
    # pad entries, which is legitimate
    interior = np.asarray(r['j_smooth'][0])[14:17, 20:80]
    med = float(np.median(interior))
    assert abs(med - j_geom) / j_geom < 0.05, (med, j_geom)
    assert r['j_robust_a_mm2'] < 1.15 * j_geom, r['j_robust_a_mm2']


def test_uniform_strip_drop():
    cop, yc = _strip()
    r = cg.solve(cop, 'V', I, sources=[(2 * CELL, yc)],
                 sinks=[((100 - 3) * CELL, yc)])
    assert r['status'] == 'ok', r
    length_m = (100 - 5) * CELL * 1e-3
    r_geom = cg.RHO_CU * length_m / (W_MM * 1e-3 * T_MM * 1e-3)
    assert abs(r['drop_v'] - r_geom) / r_geom < 0.10, (r['drop_v'], r_geom)


def test_via_carries_the_current():
    """One via is the only way down: it must carry all of I."""
    nx, ny = 60, 20
    cop = cg.Copper(board_path='synthetic', board_fp='0' * 16, cell_mm=CELL,
                    x0_mm=0.0, y0_mm=0.0, nx=nx, ny=ny, layers=['F.Cu', 'B.Cu'])
    cop.nets = {1: 'V'}
    yc = 11 * CELL
    cop.segs = {1: [(4 * CELL, yc, 44 * CELL, yc, 3.0, 'F.Cu'),
                    (40 * CELL, yc, 56 * CELL, yc, 3.0, 'B.Cu')]}
    cop.pads = {1: [
        dict(ref='A', pad='1', x=4 * CELL, y=yc, w=2.0, h=3.0, rot=0.0,
             layers=['F.Cu']),
        dict(ref='B', pad='1', x=54 * CELL, y=yc, w=2.0, h=3.0, rot=0.0,
             layers=['B.Cu'])]}
    cop.vias = [{'x': 42 * CELL, 'y': yc, 'r_mm': 0.3, 'net': 'V'}]
    r = cg.solve(cop, 'V', I, sources=[(4 * CELL, yc)], sinks=[(54 * CELL, yc)])
    assert r['status'] == 'ok', r
    assert r['via_currents'], 'no via current reported'
    assert abs(r['via_currents'][0]['current_a'] - I) < 0.05, r['via_currents'][0]


def test_no_copper_is_an_error_not_a_number():
    cop = cg.Copper(board_path='synthetic', board_fp='0' * 16, cell_mm=CELL,
                    x0_mm=0.0, y0_mm=0.0, nx=10, ny=10, layers=['F.Cu'])
    cop.nets = {1: 'V'}
    r = cg.solve(cop, 'V', 1.0)
    assert r['status'] == 'error'
