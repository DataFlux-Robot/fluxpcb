"""The auto-spec and the missing-input degradation, pinned.

The 5-board batch runs on boards nobody declared currents for.  What must
hold regardless: the ground net is found by the documented order, rails are
read from names (never from `Net-(...)`), no-connect nets are not signals,
and every finding that rested on a probe current says so and carries the
missing input.
"""
from __future__ import annotations

from fluxpcb.tools import audit_spec, copper_graph as cg, netnames
from fluxpcb.tools import phys_audit as pa


def _cop(nets, pads, segs=None, zones=None):
    cop = cg.Copper(board_path='synthetic', board_fp='0' * 16, cell_mm=0.2,
                    x0_mm=0.0, y0_mm=0.0, nx=80, ny=60, layers=['F.Cu'])
    cop.nets = nets
    cop.pads = pads
    cop.segs = segs or {}
    cop.zones = zones or []
    return cop


def _pad(ref, x, y, w=1.0, h=1.0):
    return {'ref': ref, 'pad': '1', 'x': x, 'y': y, 'w': w, 'h': h,
            'rot': 0.0, 'layers': ['F.Cu']}


def test_ground_detection_order():
    # 1. literal GND wins even when another net has more pads
    cop = _cop({1: 'GND', 2: 'GNDPWR', 3: 'VCC'},
               {1: [_pad('U1', 1, 1)], 2: [_pad('U2', 1, 1), _pad('U2', 2, 1),
                                           _pad('U2', 3, 1)],
                3: [_pad('U1', 5, 5)]})
    assert netnames.detect_ground(cop)['net'] == 'GND'
    assert netnames.detect_ground(cop)['why'] == 'literal GND'
    # 2. a ground-ish name when GND is absent
    cop = _cop({1: 'GNDPWR', 2: 'VCC'}, {1: [_pad('U1', 1, 1)],
                                         2: [_pad('U1', 2, 1)]})
    g = netnames.detect_ground(cop)
    assert g['net'] == 'GNDPWR' and 'ground-ish' in g['why']
    # 3. no ground name: the most-pads fallback, said out loud
    cop = _cop({1: '/NEG_OUT', 2: 'VCC'},
               {1: [_pad('U1', 1, 1), _pad('U2', 1, 1), _pad('U3', 1, 1)],
                2: [_pad('U1', 2, 1)]})
    g = netnames.detect_ground(cop)
    assert g['net'] == '/NEG_OUT' and 'fallback' in g['why']
    # 4. nothing named at all
    cop = _cop({1: 'N-000001'}, {1: [_pad('U1', 1, 1)]})
    g = netnames.detect_ground(cop)
    assert g['net'] == 'N-000001' and 'fallback' in g['why']


def test_voltages_and_rail_names():
    assert netnames.parse_voltage('+3V3') == 3.3
    assert netnames.parse_voltage('3.3V') == 3.3
    assert netnames.parse_voltage('-15V') == -15.0
    assert netnames.parse_voltage('+BATT') is None
    assert netnames.parse_voltage('VCC') is None
    assert not netnames.is_rail_name('Net-(U3-Pad13)')
    assert netnames.is_rail_name('VCC')
    assert netnames.is_rail_name('/GND') is False       # ground, not a rail


def test_derive_excludes_auto_names_and_no_connect():
    cop = _cop(
        {1: 'GND', 2: 'VCC', 3: 'Net-(U1-Pad1)', 4: 'NC'},
        {1: [_pad('U1', 1, 1), _pad('U2', 1, 1)],
         2: [_pad('U1', 2, 1)],
         3: [_pad('U1', 3, 1), _pad('U2', 3, 1)],
         4: [_pad('U1', 4, 1), _pad('U2', 4, 1)]},
        segs={3: [(5, 5, 25, 5, 0.25, 'F.Cu')],
              4: [(5, 6, 60, 6, 0.25, 'F.Cu')]})   # NC is the longest net
    spec = audit_spec.derive(cop, 'x.kicad_pcb')
    assert spec['gnd_net'] == 'GND'
    assert [r['net'] for r in spec['rails']] == ['VCC']
    assert all(r['current_a'] is None for r in spec['rails'])
    names = [s['net'] for s in spec['signals']]
    assert 'NC' not in names, names
    assert names[0] == 'Net-(U1-Pad1)', names
    s = spec['signals'][0]
    assert s['driver'] != s['load'] and s['current_a'] is None
    assert spec['parts'] and all(p['power_w'] is None for p in spec['parts'])
    assert any('rail current' in x for x in spec['needed_information'])


def test_probe_findings_are_estimates_that_name_the_missing_input():
    row = {'net': 'VCC', 'current_a': 0.5, 'declared_current_a': None,
           'v_rail': None, 'narrowest': {'width_mm': 0.25, 'at': (1.0, 1.0)},
           'j_geom_a_mm2': 57.1, 'j_geom_per_a': 114.3,
           'ipc2221_i_max_a_10c': 0.9,
           'field': {'j_robust_a_mm2': 50.0, 'j_p95_a_mm2': 48.0,
                     'j_robust_at': (1.0, 1.0)}}
    f = pa._mk(pa._j_finding('VCC', row, {'j_a_mm2': 45.0}))
    assert f['status'] == 'estimate' and f['severity'] == 'P1'
    assert 'PROBE' in f['assumption']
    assert 'the rail current' in f['needed_information']
    r = pa._mk(pa._res_finding('VCC', {'r_mohm': 19.1}))
    assert r['status'] == 'confirmed' and r['units'] == 'mOhm'
    r2 = pa._mk(pa._res_finding('VCC', {'r_mohm': None}, estimate=True))
    assert r2['status'] == 'estimate'


def test_missing_power_yields_unknown_thermal_not_a_temperature():
    seen = []
    audit = {'thermal': {'status': 'skipped',
                         'parts': [{'ref': 'U1', 'power_w': None,
                                    'tjmax_c': 125.0}]}}
    pa._thermal_findings(audit, {}, {'tj_of_max': 0.9},
                         lambda **kw: seen.append(kw))
    assert seen and seen[0]['status'] == 'unknown'
    assert seen[0]['measured'] is None
    assert seen[0]['needed_information']
