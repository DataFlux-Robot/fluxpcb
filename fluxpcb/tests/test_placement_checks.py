"""Placement checks and the inductance term, on synthetic copper.

The four placement rules are distances; each test builds the geometry that
must flip it.  The inductance test compares the self term with its own
large-L asymptotic (0.2*L*(ln(2L/r)-1) nH) and checks that a via adds.
"""
from __future__ import annotations

import math

from fluxpcb.kicadgen import placement_checks as pc
from fluxpcb.tools import copper_graph as cg


def _cop(pads=None, segs=None, vias=None, zones=None, nets=None):
    cop = cg.Copper(board_path='synthetic', board_fp='0' * 16, cell_mm=0.5,
                    x0_mm=0.0, y0_mm=0.0, nx=200, ny=120,
                    layers=['F.Cu', 'B.Cu'])
    cop.nets = nets or {1: 'GND', 2: '+3V3', 3: 'SIG'}
    cop.pads = pads or {}
    cop.segs = segs or {}
    cop.vias = vias or []
    cop.zones = zones or []
    return cop


def _p(ref, x, y, value='', w=0.6, h=0.4):
    return {'ref': ref, 'pad': '1', 'value': value, 'x': x, 'y': y,
            'w': w, 'h': h, 'rot': 0.0, 'layers': ['F.Cu']}


def test_cap_value_parsing():
    assert pc.cap_uf('100n') == 0.1
    assert abs(pc.cap_uf('4u7') - 4.7) < 1e-9
    assert pc.cap_uf('10uF') == 10.0
    assert abs(pc.cap_uf('1n5') - 0.0015) < 1e-12
    assert pc.cap_uf('10k') is None


def test_decap_distance_flags_the_far_cap_only():
    cop = _cop(pads={2: [_p('C1', 2.0, 10.0, '100n'), _p('C2', 30.0, 10.0, '100n'),
                       _p('C3', 40.0, 10.0, '10u')],
                     1: [_p('C1', 2.0, 10.0, '100n')]},
               nets={1: 'GND', 2: '+3V3'})
    # an IC pin 1 mm from C1 and 6 mm from C2
    cop.pads[2] += [_p('U1', 3.0, 10.0, ''), _p('U1', 36.0, 10.0, '')]
    rows = {r['ref']: r for r in pc.decap_distance(cop, 3.0, 1.0)}
    assert rows['C1']['d_mm'] < 1.0, rows['C1']
    assert rows['C2']['d_mm'] > 3.0, rows['C2']
    assert 'C3' not in rows                    # 10 uF is bulk, not decoupling


def test_mst_length_of_collinear_pads():
    assert abs(pc.mst_length([(0, 0), (1, 0), (3, 0)]) - 3.0) < 1e-9


def test_trace_detour_is_flagged():
    cop = _cop(pads={3: [_p('U1', 0.0, 0.0), _p('U2', 10.0, 0.0)]},
               segs={3: [(0, 0, 0, 10, 0.2, 'F.Cu'),
                         (0, 10, 10, 10, 0.2, 'F.Cu'),
                         (10, 10, 10, 0, 0.2, 'F.Cu')]})
    rows = pc.trace_length_ratio(cop)
    assert rows and rows[0]['over'] is True, rows
    assert rows[0]['ratio'] > 2.5


def test_loop_area_from_cin_ic_cout():
    cop = _cop(pads={2: [_p('U1', 10.0, 10.0), _p('C1', 12.0, 10.0)],
                     1: [_p('U1', 10.0, 12.0), _p('C2', 12.0, 12.0)]})
    rows = pc.loop_area(cop)
    assert rows and rows[0]['ref'] == 'U1'
    assert abs(rows[0]['area_mm2'] - 2.0) < 0.2, rows


def test_reference_via_distance():
    cop = _cop(vias=[{'x': 0.0, 'y': 0.0, 'r_mm': 0.3, 'net': 'GND'},
                     {'x': 1.0, 'y': 0.0, 'r_mm': 0.3, 'net': 'SIG'},
                     {'x': 9.0, 'y': 0.0, 'r_mm': 0.3, 'net': 'SIG'}])
    rows = {r['x']: r for r in pc.reference_via(cop, 'GND', 3.0)}
    assert rows[1.0]['d_mm'] == 1.0
    assert rows[9.0]['d_mm'] == 9.0


def test_segment_inductance_matches_the_asymptote():
    L, w = 10.0, 0.2
    got = cg.segment_inductance_nh((0, 0), (L, 0), w)
    want = 0.2 * L * (math.log(2 * L / (w / 2)) - 1.0)   # ~8.6 nH
    assert abs(got - want) / want < 0.02, (got, want)


def test_route_inductance_adds_vias():
    cop = _cop(segs={3: [(0, 0, 10, 0, 0.2, 'F.Cu')]},
               vias=[{'x': 10.0, 'y': 0.0, 'r_mm': 0.3, 'net': 'SIG'}])
    r = cg.route_inductance_nh(cop, 'SIG', t_board_mm=1.6)
    assert r['n_segments'] == 1 and r['n_vias'] == 1
    assert abs(r['l_nh'] - (cg.segment_inductance_nh((0, 0), (10, 0), 0.2)
                            + 0.64)) < 0.01
