"""Domain kernels and profiles: derivations, limits, and the gate wiring."""
from __future__ import annotations

import pytest

from fluxpcb.kernels import derive, profile
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
