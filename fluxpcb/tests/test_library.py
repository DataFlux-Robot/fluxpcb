"""The module library: loading, retrieval, instantiation, registration."""
from __future__ import annotations

import json

import pytest

from fluxpcb.proof import library, state as S


def _entry(mid, roles, deps=(), params=None, expects=None):
    return {'schema': 'fluxpcb-module-2', 'module_id': mid, 'version': '9',
            'function': mid, 'contract': {'roles': roles},
            'expects': expects or [], 'lemma_refs': ['connectivity'],
            'params': params or {}, 'deps': list(deps), 'instances': []}


def _state():
    st = {'board': 'b', 'layers': ['F.Cu'],
          'items': [{'id': 'a1', 'net': 'VPP', 'layers': ['F.Cu'], 'ref': 'U2',
                     'shape': {'kind': 'poly', 'pts': [[0, 0], [1, 0],
                                                       [1, 1], [0, 1]]}},
                    {'id': 'a2', 'net': 'VPP', 'layers': ['F.Cu'], 'ref': 'C2',
                     'shape': {'kind': 'poly', 'pts': [[2, 0], [3, 0],
                                                       [3, 1], [2, 1]]}}],
          'rules': {}, 'netPads': {'VPP': ['a1', 'a2']},
          'footprints': {'U2': ['a1'], 'C2': ['a2']}}
    return S.freeze(st)


def _write(root, d):
    p = root / d['module_id'].split('.')[-1]
    p.mkdir(parents=True)
    (p / 'entry.json').write_text(json.dumps(d), encoding='utf-8')


def test_load_find_and_dependency_graph(tmp_path):
    root = tmp_path / 'lib'
    _write(root, _entry('power.ldo', {'regulator': ['U2'],
                                      'input_cap': ['C2']},
                        params={'input_net': 'VPP'}))
    _write(root, _entry('core', {'module': ['U2']}, deps=['power.ldo']))
    lib = library.Library(root)
    assert lib.list() == ['core', 'power.ldo']
    adj, cycles = lib.graph()
    assert adj['core'] == ['power.ldo'] and not cycles
    cands = lib.find(_state())
    assert cands[0]['module'] == 'power.ldo'
    assert any('VPP' in w for w in cands[0]['why'])


def test_instantiate_rejects_refs_not_on_the_board(tmp_path):
    root = tmp_path / 'lib'
    _write(root, _entry('power.ldo', {'regulator': ['U99']}))
    lib = library.Library(root)
    with pytest.raises(ValueError):
        lib.get('power.ldo').instantiate(_state())
    c = lib.get('power.ldo').instantiate(_state(), {'regulator': 'U2'})
    assert c.refs == ['U2']


def test_register_roundtrip_and_rule_provenance(tmp_path):
    root = tmp_path / 'lib'
    _write(root, _entry('power.ldo', {'regulator': ['U2']}))
    lib = library.Library(root)
    e = lib.get('power.ldo')
    e.register({'digest': 'd1', 'ok': True, 'summary': {}, 'seconds': 0.1,
                'theorem': {'x': 1}}, e.instantiate(_state()), 'board.kicad_pcb',
               'fp1', rules={'clearance': 1})
    lib.save(e)
    again = library.Library(root).get('power.ldo')
    assert again.instances[0]['board_fp'] == 'fp1'
    assert again.instances[0]['rules'] == {'clearance': 1}
    assert again.instances[0]['proof']['ok']


def test_dependency_cycle_is_reported(tmp_path):
    root = tmp_path / 'lib'
    _write(root, _entry('a', {}, deps=['b']))
    _write(root, _entry('b', {}, deps=['a']))
    _, cycles = library.Library(root).graph()
    assert cycles


def test_legacy_entry_still_loads(tmp_path):
    root = tmp_path / 'lib'
    p = root / 'legacy'
    p.mkdir(parents=True)
    (p / 'entry.json').write_text(json.dumps({
        'schema': 'fluxpcb-module-1', 'module_id': 'legacy',
        'refs': ['U1', 'D1-D3'],
        'proven_instances': [{'board': 'x'}]}), encoding='utf-8')
    e = library.Library(root).get('legacy')
    assert e.refs() == ['D1', 'D2', 'D3', 'U1']
    assert e.instances[0]['board'] == 'x'
