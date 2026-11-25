"""Analytic anchors for the physical audit before any board is trusted.

Each solver has a closed-form case it must reproduce: a uniform strip for
the copper field, the rectangular cavity for the mode solver, the cosh fin
for the thermal sheet, the 1/r^2 / 1/r splice for the coupling kernel.  The
board-level flips (narrow the neck, remove the plane, add the sink, move the
harmonic onto a mode) live in test_boards_phys_audit.py.
"""
from __future__ import annotations

import math

import numpy as np

from fluxpcb.tools import cavity_audit as ca
from fluxpcb.tools import copper_graph as cg
from fluxpcb.tools import em_map
from fluxpcb.tools import thermal_audit as ta


def test_cavity_rect_matches_the_analytic_modes():
    mask = np.ones((260, 350), bool)          # 70 x 52 mm at 0.2 mm
    r = ca.modes(mask, 0.2, er=4.4, n=3)
    assert r['status'] == 'ok', r
    got = [m['f_hz'] for m in r['modes']]
    want = [ca.rect_analytic(70, 52, 4.4, 1, 0),
            ca.rect_analytic(70, 52, 4.4, 0, 1),
            ca.rect_analytic(70, 52, 4.4, 1, 1)]
    for g, w in zip(got, want):
        assert abs(g - w) / w < 0.03, (g, w)


def test_cavity_is_a_what_if_without_a_plane_pair():
    cop = cg.Copper(board_path='synthetic', board_fp='0' * 16, cell_mm=0.5,
                    x0_mm=0, y0_mm=0, nx=60, ny=40, layers=['F.Cu'])
    cop.nets = {1: 'GND'}
    yc = 10.0
    cop.segs = {1: [(1, yc, 29, yc, 1.0, 'F.Cu')]}
    cop.zones = [{'net': 'GND', 'layer': 'F.Cu',
                  'areas': [([(1, 1), (29, 1), (29, 19), (1, 19)], [])]}]
    r = ca.run(cop, {'plane_pair': False, 'emitters': []}, er=4.4)
    assert r['status'] == 'ok', r
    assert r['applicable'] is False
    assert r['needed_information']


def _fin_copper(nx=110, ny=20, width_mm=11.0):
    """A straight copper strip with nothing else on the board."""
    cop = cg.Copper(board_path='synthetic', board_fp='0' * 16, cell_mm=0.5,
                    x0_mm=0.0, y0_mm=0.0, nx=nx, ny=ny, layers=['F.Cu'])
    cop.nets = {1: 'CU'}
    yc = 5.0
    cop.segs = {1: [(1.0, yc, 54.0, yc, width_mm, 'F.Cu')]}
    cop.pads = {}
    return cop, yc


def test_thermal_fin_matches_cosh():
    """The sheet must solve the discrete fin equation, whose solution is the
    cosh with m = sqrt(2h/(k t)); check the recurrence cell by cell and the
    implied decay constant against the closed form."""
    cop, yc = _fin_copper()           # the strip fills the height: exactly 1-D
    dx = 0.5
    fixed = {(j, 2): 100.0 for j in range(20)}
    r = ta.solve_board(cop, [], h_w_m2k=10.0, t_amb_c=25.0, cell_mm=dx,
                       fixed=fixed)
    assert r['status'] == 'ok', r
    k = ta.k_sheet(1.0)
    t_m = ta.T_BOARD_MM * 1e-3
    hA_g = (2 * 10.0) * (dx * 1e-3) ** 2 / (k * t_m)
    T = r['t']
    jc = int(yc / dx)
    th = T[jc] - 25.0
    assert float(T[jc, 2]) == 100.0
    for i in (20, 40, 60, 80):
        lhs = 2 * th[i] - th[i - 1] - th[i + 1]
        assert abs(float(lhs) - hA_g * float(th[i])) < 1e-3 * float(th[i]), i
    mu = math.acosh(1 + hA_g / 2)                 # the recurrence's decay/cell
    m = math.sqrt(2 * 10.0 / (k * t_m))
    assert abs(mu / (dx * 1e-3) - m) / m < 0.01, (mu / (dx * 1e-3), m)
    for i in range(10, 100, 10):
        assert float(th[i + 1]) < float(th[i])    # decaying, no oscillation
    # energy balance: the fixed end injects, convection removes
    assert r['heat_in_w'] > 0
    assert abs(r['heat_out_w'] - r['heat_in_w']) / r['heat_in_w'] < 0.02


def test_thermal_energy_balance_with_a_part():
    cop, yc = _fin_copper(nx=80, ny=40)
    parts = [{'ref': 'U9', 'power_w': 0.5, 'w_mm': 6.0, 'h_mm': 6.0,
              'theta_ja': 50, 'theta_jc': 5, 'theta_cs': 0.5, 'tjmax_c': 125}]
    # a footprint so part_bbox can find it
    cop.pads = {1: [dict(ref='U9', pad='1', x=20.0, y=yc, w=2.0, h=2.0,
                         rot=0.0, layers=['F.Cu'])]}
    r = ta.solve_board(cop, parts, h_w_m2k=10.0, t_amb_c=25.0)
    assert r['status'] == 'ok', r
    assert r['balance_err'] < 0.02, r
    assert r['tmax_c'] > 25.0


def test_em_kernel_is_flat_near_and_one_over_r_far():
    assert abs(float(em_map.coupling(0.0)) - 1.0) < 1e-9
    a = float(em_map.coupling(9.999))
    b = float(em_map.coupling(10.001))
    assert abs(a - b) < 1e-3, (a, b)            # continuous at 10*r0
    far = [float(em_map.coupling(r)) for r in (12.0, 24.0, 48.0)]
    assert far[0] > far[1] > far[2]
    assert abs(far[1] / far[0] - 0.5) < 0.05     # 1/r


def test_ipc2221_anchor():
    # 1 mm, 1 oz, 10 C rise, external layer: 2.4-2.6 A by the standard
    i = ta_i = __import__('fluxpcb.tools.phys_audit', fromlist=['x']).ipc2221_i_max(1.0)
    assert 2.3 < i < 2.7, i
    # doubling the width raises capacity by 2^0.725, not by 2
    i2 = __import__('fluxpcb.tools.phys_audit', fromlist=['x']).ipc2221_i_max(2.0)
    assert abs(i2 / i - 2 ** 0.725) < 0.02
