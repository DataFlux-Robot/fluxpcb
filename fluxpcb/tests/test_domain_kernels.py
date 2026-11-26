"""Domain kernels and profiles: derivations, limits, and the gate wiring."""
from __future__ import annotations

import pytest

from fluxpcb.kernels import derive, fab, profile
from fluxpcb.proof import bridge, certs, claims, gate, state as st_mod


def _pad(pid, x0, y0, x1, y1, net='N'):
    return {'id': pid, 'net': net, 'layers': ['F.Cu'],
            'shape': {'kind': 'poly', 'pts': [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]}}


def _seg(sid, x1, y1, x2, y2, w=200000, net='N'):
    return {'id': sid, 'net': net, 'layers': ['F.Cu'],
            'shape': {'kind': 'seg', 'x1': x1, 'y1': y1, 'x2': x2, 'y2': y2, 'w': w}}


def _clean():
    st = {'board': 'synthetic', 'layers': ['F.Cu'],
          'items': [_pad('pa', 0, 0, 600000, 600000),
                    _seg('t1', 300000, 300000, 5000000, 300000),
                    _pad('pb', 4700000, 0, 5300000, 600000)],
          'rules': {'clearance': 150000, 'track_min': 150000},
          'netPads': {'N': ['pa', 'pb']}}
    return st_mod.from_dict(st)


def _needs_lean():
    if not bridge.available() and bridge._lake() is None:
        pytest.skip('Lean kernel is not installed')


def test_power_drop_matches_hand_calculation():
    d = derive.power_drop(1.0, 10.0, 0.2, 0.035)
    # R = (1/5.8e7) * 0.01 / (2e-4 * 3.5e-5) = 24.63 mOhm -> 24.63 mV
    assert d['status'] == 'derived'
    assert abs(d['measured'] - 24.63) < 0.05
    assert d['units'] == 'mV'
    assert d['evidence']['model'].startswith('R = rho')
    assert len(d['evidence']['inputs_hash']) == 16


def test_missing_inputs_are_unknown_and_named():
    d = derive.power_drop(1.0, None, 0.2)
    assert d['status'] == 'unknown'
    assert d['needed_information'] == ['length_mm']
    t = derive.thermal_tj(None)
    assert t['needed_information'] == ['power_w']


def test_thermal_and_impedance_math():
    t = derive.thermal_tj(1.0, theta_ja=50.0)
    assert t['measured'] == 75.0
    z = derive.z0_microstrip(0.2, 0.2, 4.2)
    assert 60.0 < z['measured'] < 72.0


def test_profile_disabled_is_reported_not_dropped():
    cl = [{'id': 'POWER:DROP:3V3', 'pred': 'power.drop',
           'params': {'current_a': 1.0, 'length_mm': 10.0, 'width_mm': 0.2}}]
    lean = profile.run_domain(cl, profile.parse('lean+fab'))
    assert lean['claims'][0]['status'] == 'disabled'
    assert lean['ok']
    full = profile.run_domain(cl, profile.parse('full'))
    assert full['claims'][0]['status'] == 'derived'
    assert full['claims'][0]['measured'] > 20
    assert full['ok']


def test_limit_exceeded_is_refuted():
    cl = [{'id': 'POWER:DROP:3V3', 'pred': 'power.drop', 'limit': 10.0,
           'params': {'current_a': 1.0, 'length_mm': 10.0, 'width_mm': 0.2}}]
    full = profile.run_domain(cl, profile.parse('full'))
    assert full['claims'][0]['status'] == 'refuted'
    assert not full['ok']


def test_gate_full_profile_denies_underivable_domain_claim():
    _needs_lean()
    st = _clean()
    cl = claims.bench_claims(st)
    cs = certs.connectivity_certs(st)
    dom = [{'id': 'THERMAL:TJ:U1', 'pred': 'thermal.tj', 'params': {}}]
    r = gate.verify(st, cl, cs, profile='lean+fab', domain_claims=dom)
    assert r['ok'], r['reasons']
    assert r['domain']['claims'][0]['status'] == 'disabled'
    r = gate.verify(st, cl, cs, profile='full', domain_claims=dom)
    assert not r['ok']
    assert any('THERMAL:TJ:U1' in x and 'power_w' in x for x in r['reasons']), r['reasons']
    dom = [{'id': 'THERMAL:TJ:U1', 'pred': 'thermal.tj', 'limit': 85.0,
            'params': {'power_w': 0.5, 'theta_ja': 60.0}}]
    r = gate.verify(st, cl, cs, profile='full', domain_claims=dom)
    assert r['ok'], r['reasons']
    assert r['domain']['claims'][0]['status'] == 'derived'
    dom[0]['limit'] = 40.0
    r = gate.verify(st, cl, cs, profile='full', domain_claims=dom)
    assert not r['ok']
    assert any('refuted' in x for x in r['reasons'])


def test_unknown_profile_name_is_an_error():
    with pytest.raises(ValueError):
        profile.parse('fast')


def test_conjectures_read_the_state_geometry():
    from fluxpcb.proof import conjectures
    st = _clean()
    length, width = conjectures.net_geometry(st, 'N')
    assert abs(length - 4.7) < 1e-6
    assert abs(width - 0.2) < 1e-6
    spec = {'rails': [{'name': '3V3', 'net': 'N', 'current_a': 1.0,
                       'v_rail': 3.3, 'drop_limit_mv': 100}],
            'requirements': [{'id': 'R1', 'text': '2-layer, 1.6 mm'}]}
    claims_, unf = conjectures.from_spec(spec, st)
    assert len(claims_) == 1 and claims_[0]['id'] == 'POWER:DROP:3V3'
    p = claims_[0]['params']
    assert abs(p['length_mm'] - 4.7) < 1e-6 and abs(p['width_mm'] - 0.2) < 1e-6
    r = profile.run_domain(claims_, profile.parse('full'))
    assert r['claims'][0]['status'] == 'derived'
    assert abs(r['claims'][0]['measured'] - 11.58) < 0.05
    assert unf and unf[0]['id'] == 'R1'


def test_conjecture_on_an_unrouted_net_is_unknown():
    from fluxpcb.proof import conjectures
    st = _clean()
    st = dict(st, items=[i for i in st['items'] if i['id'] != 't1'])
    st = st_mod.freeze(st)
    length, width = conjectures.net_geometry(st, 'N')
    assert length == 0 and width is None
    claims_, _ = conjectures.from_spec({'rails': [{'name': '3V3', 'net': 'N',
                                                   'current_a': 1.0}]}, st)
    r = profile.run_domain(claims_, profile.parse('full'))
    assert r['claims'][0]['status'] == 'unknown'
    assert 'length_mm' in r['claims'][0]['needed_information']


def _chain_state():
    st = {'board': 'chain', 'layers': ['F.Cu'],
          'items': [_pad('pa', 0, 0, 600000, 600000),
                    _seg('t1', 300000, 300000, 10300000, 300000),
                    _pad('pb', 10000000, 0, 10600000, 600000)],
          'rules': {'clearance': 150000, 'track_min': 150000},
          'netPads': {'N': ['pa', 'pb']}}
    return st_mod.freeze(st)


def test_worst_path_drop_matches_one_segment():
    st = _chain_state()
    d = derive.power_drop_path(st, 'N', 1.0)
    # 10 mm, 0.2 mm wide, 35 um copper: R = 24.6 mOhm -> 24.6 mV
    assert d['status'] == 'derived'
    assert abs(d['measured'] - 24.63) < 0.1, d
    assert d['min_width_mm'] == 0.2
    assert abs(d['current_density'] - 1.0 / (0.2 * 0.035)) < 0.1


def test_unrouted_rail_is_unknown_not_derived():
    st = _chain_state()
    st = dict(st, items=[i for i in st['items'] if i['id'] != 't1'])
    st = st_mod.freeze(st)
    d = derive.power_drop_path(st, 'N', 1.0)
    assert d['status'] == 'unknown'


def test_via_theta_stripline_and_skew_math():
    v = derive.via_array_theta(9, 0.3)
    assert v['status'] == 'derived' and 0 < v['measured'] < 20
    z = derive.z0_stripline(0.2, 0.2, 4.2)
    assert 35 < z['measured'] < 45
    st = {'board': 'pair', 'layers': ['F.Cu'],
          'items': [_seg('p1', 0, 0, 10000000, 0, net='P'),
                    _seg('n1', 0, 1000000, 10500000, 1000000, net='N')],
          'rules': {}, 'netPads': {'P': [], 'N': []}}
    st = st_mod.freeze(st)
    s = derive.diff_skew(st, 'P', 'N')
    assert s['status'] == 'derived' and abs(s['measured'] - 0.5) < 1e-6


def test_via_array_lowers_the_junction_temperature():
    base = derive.thermal_tj(1.0, theta_ja=60.0)
    with_vias = derive.thermal_tj(1.0, theta_ja=60.0, theta_via=10.0)
    assert with_vias['measured'] < base['measured']


def test_profile_dispatches_path_skew_and_stripline():
    st = _chain_state()
    cl = [{'id': 'P', 'pred': 'power.drop_path',
           'params': {'net': 'N', 'current_a': 1.0}}]
    r = profile.run_domain(cl, profile.parse('full'), st)
    assert r['claims'][0]['status'] == 'derived'
    cl = [{'id': 'S', 'pred': 'signal.skew',
           'params': {'p_net': 'P', 'n_net': 'N'}}]
    st2 = {'board': 'pair', 'layers': ['F.Cu'],
           'items': [_seg('p1', 0, 0, 10000000, 0, net='P'),
                     _seg('n1', 0, 1000000, 10000000, 1000000, net='N')],
           'rules': {}, 'netPads': {}}
    r = profile.run_domain(cl, profile.parse('full'), st_mod.freeze(st2))
    assert r['claims'][0]['status'] == 'derived'
    cl = [{'id': 'Z', 'pred': 'signal.z0',
           'params': {'kind': 'stripline', 'width_mm': 0.2, 'h_mm': 0.2,
                      'er': 4.2}}]
    r = profile.run_domain(cl, profile.parse('full'))
    assert 35 < r['claims'][0]['measured'] < 45


def _via(pid, x, y, r, drill):
    return {'id': pid, 'net': 'N', 'layers': ['F.Cu', 'B.Cu'],
            'drill': drill, 'span': 'F.Cu-B.Cu',
            'shape': {'kind': 'circle', 'cx': x, 'cy': y, 'r': r}}


def test_fab_annular_drill_hole_and_edge():
    st = {'board': 'holes', 'layers': ['F.Cu', 'B.Cu'],
          'items': [_via('v1', 1000000, 1000000, 300000, 400000),
                    _via('v2', 1500000, 1000000, 300000, 400000),
                    _via('v3', 6000000, 1000000, 200000, 400000)],
          'rules': {'annular': 75000, 'drill_min': 200000,
                    'hole_to_hole': 250000},
          'netPads': {'N': []}}
    st = st_mod.freeze(st)
    rep = fab.check(st, [{'id': 'A', 'pred': 'annular', 'scope': None,
                          'rule': 75000}], st['rules'])
    assert rep['claims'][0]['status'] == 'refuted'
    assert 'v3' in rep['claims'][0]['witness']
    rep = fab.check(st, [{'id': 'H', 'pred': 'hole', 'scope': None,
                          'rule': 250000}], st['rules'])
    assert rep['claims'][0]['status'] == 'refuted'
    rep = fab.check(st, [{'id': 'D', 'pred': 'drill', 'scope': None,
                          'rule': 500000}], st['rules'])
    assert rep['claims'][0]['status'] == 'refuted'
    # edge: a track 0.1 mm inside the outline is refuted at 0.3 mm, 0.5 is ok
    st2 = {'board': 'edge', 'layers': ['F.Cu'],
           'items': [_seg('t', 300000, 5000000, 9700000, 5000000)],
           'rules': {}, 'netPads': {},
           'outline': [[0, 0], [10000000, 0], [10000000, 10000000],
                       [0, 10000000]]}
    st2 = st_mod.freeze(st2)
    rep = fab.check(st2, [{'id': 'E', 'pred': 'edge', 'scope': None,
                            'rule': 300000}], st2['rules'])
    assert rep['claims'][0]['status'] == 'refuted'
    rep = fab.check(st2, [{'id': 'E', 'pred': 'edge', 'scope': None,
                            'rule': 200000}], st2['rules'])
    assert rep['claims'][0]['status'] == 'proven', rep['claims'][0]


def test_zones_make_clearance_unknown_not_silent():
    st = {'board': 'z', 'layers': ['F.Cu'], 'items': [], 'rules': {},
          'netPads': {}, 'zones': 1}
    st = st_mod.freeze(st)
    rep = fab.check(st, [{'id': 'C', 'pred': 'clearance', 'scope': None,
                          'rule': 150000}], st['rules'])
    assert rep['claims'][0]['status'] == 'unknown'
    assert 'zone' in rep['claims'][0]['witness']


def test_process_claims_come_from_the_fab_profile():
    from fluxpcb.proof import claims as C
    st = {'board': 'p', 'layers': ['F.Cu'], 'items': [], 'rules': {},
          'netPads': {}}
    st = st_mod.freeze(st)
    ids = {c['id'] for c in C.process_claims(st, 'jlc-1oz-2to4l-2026-11-25')}
    assert ids == {'PROC:ANNULAR', 'PROC:HOLE', 'PROC:EDGE', 'PROC:DRILL'}
