"""Module contracts, scoped states and the boundary obligation (no Lean needed)."""
from __future__ import annotations

from fluxpcb.proof import modules, state as S


def _pad(pid, net, ref, x=0, y=0, w=600000):
    return {'id': pid, 'net': net, 'layers': ['F.Cu'], 'ref': ref,
            'shape': {'kind': 'poly', 'pts': [[x, y], [x + w, y],
                                              [x + w, y + w], [x, y + w]]}}


def _seg(sid, net, x1, y1, x2, y2):
    return {'id': sid, 'net': net, 'layers': ['F.Cu'],
            'shape': {'kind': 'seg', 'x1': x1, 'y1': y1, 'x2': x2, 'y2': y2,
                      'w': 200000}}


def _board():
    st = {'board': 'two-module', 'layers': ['F.Cu'],
          'items': [_pad('a1', 'N', 'U2', 0, 0), _pad('a2', 'N', 'C3', 2000000, 0),
                    _pad('b1', 'N', 'U1', 6000000, 0), _pad('b2', 'M', 'U1', 6000000, 2000000),
                    _pad('b3', 'M', 'U1', 9000000, 2000000),
                    _seg('t1', 'M', 6000000, 2000000, 9000000, 2000000)],
          'rules': {'clearance': 200000, 'track_min': 150000},
          'netPads': {'N': ['a1', 'a2', 'b1'], 'M': ['b2', 'b3']}}
    return S.freeze(st)


def test_derive_splits_internal_from_boundary():
    st = _board()
    a = modules.derive(st, 'power', ['U2', 'C3'])
    b = modules.derive(st, 'mcu', ['U1'])
    assert a.boundary == ['N'] and a.internal == []
    assert b.boundary == ['N'] and b.internal == ['M']


def test_module_state_keeps_own_pads_and_internal_tracks():
    st = _board()
    b = modules.derive(st, 'mcu', ['U1'])
    mst = modules.module_state(st, b)
    ids = {i['id'] for i in mst['items']}
    assert {'b1', 'b2', 'b3', 't1'} <= ids and 'a1' not in ids
    a = modules.derive(st, 'power', ['U2', 'C3'])
    ast = modules.module_state(st, a)
    assert {i['id'] for i in ast['items']} == {'a1', 'a2'}


def test_boundary_must_partition_the_board_pads():
    st = _board()
    a = modules.derive(st, 'power', ['U2', 'C3'])
    b = modules.derive(st, 'mcu', ['U1'])
    pa = {'pads': {'N': ['a1', 'a2']}}
    pb = {'pads': {'N': ['b1']}}
    assert modules.check_boundary(st, [pa, pb], [a, b]) == []
    bad = {'pads': {'N': ['b1', 'b1']}}
    reasons = modules.check_boundary(st, [pa, bad], [a, b])
    assert reasons and 'duplicate' in reasons[0]
    missing = {'pads': {'N': []}}
    reasons = modules.check_boundary(st, [pa, missing], [a, b])
    assert any('missing 1' in r for r in reasons)
