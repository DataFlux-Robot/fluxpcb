"""The Step gate: proven, refuted, unknown, drift, frame — on synthetic states.

These tests build canonical states directly (no pcbnew): the Lean kernel and
the fab kernel are the only things under test.  If Lean is not installed the
kernel tests skip; the fab-only assertions still run.
"""
from __future__ import annotations

import copy

import pytest

from fluxpcb.kernels import fab
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


def test_clean_board_is_proven_by_both_kernels():
    _needs_lean()
    st = _clean()
    cl = claims.bench_claims(st)
    cs = certs.connectivity_certs(st)
    r = gate.verify(st, cl, cs)
    assert r['ok'], r['reasons']
    by = {c['id']: c for c in r['lean']['claims']}
    assert by['CONN:N']['status'] == 'proven'
    assert by['NETLIST']['status'] == 'proven'
    assert all(c['status'] == 'proven' for c in r['fab']['claims']), r['fab']


def test_open_net_is_unknown_not_refuted():
    _needs_lean()
    st = _clean()
    st['items'] = [i for i in st['items'] if i['id'] != 't1']
    st = st_mod.freeze(st)
    cl = claims.bench_claims(st)
    cs = certs.connectivity_certs(st)
    r = gate.verify(st, cl, cs)
    assert not r['ok']
    by = {c['id']: c for c in r['lean']['claims']}
    assert by['CONN:N']['status'] == 'unknown'
    assert 'not reached' in by['CONN:N']['witness']


def test_clearance_violation_is_refuted_by_the_fab_kernel():
    st = _clean()
    st['items'].append(_seg('t2', 300000, 500000, 5000000, 500000, w=100000, net='M'))
    st['netPads']['M'] = []
    st = st_mod.freeze(st)
    report = fab.check(st, [{'id': 'FAB:CLEARANCE', 'pred': 'clearance',
                             'scope': [i['id'] for i in st['items']],
                             'rule': 150000}], st['rules'])
    c = report['claims'][0]
    assert c['status'] == 'refuted', c
    assert 'gap' in c['witness']


def test_width_violation_is_refuted():
    st = _clean()
    st['items'][1]['shape']['w'] = 100000
    st = st_mod.freeze(st)
    report = fab.check(st, [{'id': 'FAB:WIDTH', 'pred': 'width',
                             'scope': [i['id'] for i in st['items']],
                             'rule': 150000}], st['rules'])
    assert report['claims'][0]['status'] == 'refuted'


def test_digest_drift_is_detected():
    _needs_lean()
    st = _clean()
    bad = copy.deepcopy(st)
    bad['items'][1]['shape']['y1'] += 1
    cl = claims.bench_claims(bad)
    cs = certs.connectivity_certs(bad)
    r = gate.verify(bad, cl, cs)
    assert r['lean']['drift'] is True
    assert not r['ok']


def test_frame_rejects_an_untouched_change():
    _needs_lean()
    before = _clean()
    after = copy.deepcopy(before)
    after['items'][2]['shape']['pts'] = [[4700000, 0], [5400000, 0],
                                         [5400000, 600000], [4700000, 600000]]
    after = st_mod.freeze(after)
    cl = claims.bench_claims(after)
    cs = certs.connectivity_certs(after)
    r = gate.verify(after, cl, cs, before=before, touched=[])
    assert r['lean']['frame_ok'] is False
    assert not r['ok']
    with pytest.raises(gate.GateDenied):
        gate.commit_step(before, after, [], cl, cs)


def test_carried_proof_scope_must_not_be_touched():
    _needs_lean()
    before = _clean()
    after = copy.deepcopy(before)
    after = st_mod.freeze(after)
    r = gate.verify(after, [], [], before=before, touched=['t1'],
                    carried=[('CONN:N', ['pa', 't1', 'pb'])])
    assert r['lean']['carried_ok'] is False
    assert not r['ok']


def test_bypass_is_denied():
    _needs_lean()
    st = _clean()
    with pytest.raises(gate.GateDenied):
        gate.commit_step(st, st, [], [], [])
