"""Spec -> netlist refinement: the intent level above the given netlist."""
from __future__ import annotations

from fluxpcb.proof import spec, state as S


def _pad(pid, net, ref):
    return {'id': pid, 'net': net, 'layers': ['F.Cu'], 'ref': ref,
            'shape': {'kind': 'poly', 'pts': [[0, 0], [1, 0], [1, 1], [0, 1]]}}


def _state(u1_net='+3V3'):
    st = {'board': 's', 'layers': ['F.Cu'],
          'items': [_pad('a', '+3V3', 'U2'), _pad('b', '+3V3', 'C2'),
                    _pad('c', u1_net, 'U1'), _pad('d', '+5V', 'F1'),
                    _pad('e', '+5V', 'U2')],
          'rules': {}, 'netPads': {'+3V3': ['a', 'b'] + (['c'] if u1_net == '+3V3' else []),
                                   '+5V': ['d', 'e'] + (['c'] if u1_net == '+5V' else [])},
          'footprints': {'U2': ['a', 'e'], 'C2': ['b'], 'U1': ['c'], 'F1': ['d']}}
    return S.freeze(st)


SPEC = {'power': {'rails': [
            {'name': '3V3', 'net': '+3V3', 'source_ref': 'U2',
             'sink_refs': ['C2', 'U1']}]},
        'parts': ['U1', 'U2', 'F1', 'C2'],
        'forbidden': [['+5V', '+3V3']],
        'interfaces': [{'name': 'TX', 'net': '+3V3', 'type': 'single_ended'}],
        'requirements': [{'id': 'R1', 'text': '2-layer'}]}


def test_spec_is_satisfied_by_the_netlist():
    sp = spec.load(SPEC)
    r = spec.check(sp, _state())
    assert r['ok'], r['reasons']
    assert r['theorem']['ok'] and r['theorem']['kind'] if 'kind' in r['theorem'] else True
    assert len(r['unformalized']) == 1


def test_a_sink_on_the_wrong_rail_breaks_the_refinement():
    r = spec.check(spec.load(SPEC), _state(u1_net='+5V'))
    assert not r['ok']
    assert any('RAIL:3V3:sink:U1' in x for x in r['reasons']), r['reasons']


def test_missing_part_forbidden_short_and_missing_interface():
    sp = spec.load(dict(SPEC, parts=['U1', 'U9']))
    r = spec.check(sp, _state())
    assert any('PART:U9' in x for x in r['reasons'])
    sp = spec.load(dict(SPEC, forbidden=[['+3V3', '+3V3']]))
    r = spec.check(sp, _state())
    assert any('SEP:' in x for x in r['reasons'])
    sp = spec.load(dict(SPEC, interfaces=[{'name': 'RX', 'net': 'RXD'}]))
    r = spec.check(sp, _state())
    assert any('IFACE:RX' in x for x in r['reasons'])


def test_refine_composes_spec_and_layout():
    r = spec.refine(spec.load(SPEC), _state(), True)
    assert r['ok'] and r['spec_ok'] and r['layout_ok']
    r = spec.refine(spec.load(SPEC), _state(), False)
    assert not r['ok'] and r['spec_ok'] and not r['layout_ok']
    r = spec.refine(spec.load(SPEC), _state(u1_net='+5V'), True)
    assert not r['ok'] and not r['spec_ok']
