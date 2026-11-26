"""The conjecture-theorem-lemma calculus, on synthetic states."""
from __future__ import annotations

import copy

import pytest

from fluxpcb.kernels import fab
from fluxpcb.proof import (calculus, certs, claims, gate, modules, state as S)
from test_proof_gate import _clean, _needs_lean


def _theorem(st, lemma_refs=('connectivity', 'netlist', 'fab.clearance',
                             'fab.width')):
    cl = claims.bench_claims(st)
    return calculus.Theorem(id='T1', conjecture_id='C1', claims=cl,
                            lemma_refs=list(lemma_refs))


def test_discharge_covers_the_conjecture():
    _needs_lean()
    st = _clean()
    cl = claims.bench_claims(st)
    c = calculus.Conjecture(id='C1', statement='synthetic board', claims=cl)
    t = calculus.Theorem(id='T1', conjecture_id='C1', claims=cl,
                         lemma_refs=['connectivity', 'netlist',
                                     'fab.clearance', 'fab.width'])
    r = calculus.discharge(c, [t], st, st['rules'])
    assert r['ok'], r['reasons']
    assert not r['coverage']['missing']
    assert 'fab.clearance' in r['lemmas']


def test_a_missing_claim_is_reported_not_hidden():
    _needs_lean()
    st = _clean()
    cl = claims.bench_claims(st)
    c = calculus.Conjecture(id='C1', statement='synthetic', claims=cl)
    t = calculus.Theorem(id='T1', conjecture_id='C1',
                         claims=[x for x in cl if x['id'] == 'CONN:N'])
    r = calculus.discharge(c, [t], st, st['rules'])
    assert not r['ok']
    assert any('uncovered' in x for x in r['reasons']), r['reasons']


def test_dependency_cycle_and_unknown_lemma_are_rejected():
    _needs_lean()
    st = _clean()
    cl = claims.bench_claims(st)
    c = calculus.Conjecture(id='C1', statement='synthetic', claims=cl)
    a = calculus.Theorem(id='A', conjecture_id='C1', claims=cl, deps=['B'])
    b = calculus.Theorem(id='B', conjecture_id='C1', claims=[], deps=['A'])
    r = calculus.discharge(c, [a, b], st, st['rules'])
    assert any('cycle' in x for x in r['reasons']), r['reasons']
    a = calculus.Theorem(id='A', conjecture_id='C1', claims=cl,
                         lemma_refs=['fab.nope'])
    r = calculus.discharge(c, [a], st, st['rules'])
    assert any('unknown lemmas' in x for x in r['reasons']), r['reasons']


def _two_module_state():
    def pad(pid, net, ref, x, y):
        return {'id': pid, 'net': net, 'layers': ['F.Cu'], 'ref': ref,
                'shape': {'kind': 'poly', 'pts': [[x, y], [x + 600000, y],
                                                  [x + 600000, y + 600000],
                                                  [x, y + 600000]]}}
    st = {'board': 'two', 'layers': ['F.Cu'],
          'items': [pad('a1', 'N', 'U2', 0, 0), pad('a2', 'N', 'C3', 2000000, 0),
                    pad('b1', 'N', 'U1', 6000000, 0),
                    pad('b2', 'M', 'U1', 6000000, 2000000),
                    pad('b3', 'M', 'U1', 9000000, 2000000),
                    {'id': 't1', 'net': 'M', 'layers': ['F.Cu'],
                     'shape': {'kind': 'seg', 'x1': 6300000, 'y1': 2300000,
                               'x2': 9000000, 'y2': 2300000, 'w': 200000}},
                    {'id': 't2', 'net': 'N', 'layers': ['F.Cu'],
                     'shape': {'kind': 'seg', 'x1': 300000, 'y1': 300000,
                               'x2': 2300000, 'y2': 300000, 'w': 200000}},
                    {'id': 't3', 'net': 'N', 'layers': ['F.Cu'],
                     'shape': {'kind': 'seg', 'x1': 2300000, 'y1': 300000,
                               'x2': 6300000, 'y2': 300000, 'w': 200000}}],
          'rules': {'clearance': 200000, 'track_min': 150000},
          'netPads': {'N': ['a1', 'a2', 'b1'], 'M': ['b2', 'b3']}}
    return S.freeze(st)


def _theorem_for(st, name, refs):
    c = modules.derive(st, name, refs)
    mst = modules.module_state(st, c)
    return c, {'id': f'{name}@x', 'contract': c.as_dict(),
               'board_fp': S.board_fp(mst), 'digest': mst['digest'],
               'claims': modules.module_claims(mst, c),
               'lemma_refs': ['connectivity', 'pads', 'fab.clearance']}


def test_merge_carries_module_theorems_and_rejects_stale_ones(tmp_path):
    _needs_lean()
    st = _two_module_state()
    ca, tha = _theorem_for(st, 'A', ['U2', 'C3'])
    cb, thb = _theorem_for(st, 'B', ['U1'])
    pa, pb = str(tmp_path / 'a.json'), str(tmp_path / 'b.json')
    import json
    for path, c in ((pa, ca), (pb, cb)):
        path_ = tmp_path / (c.name + '.json')
        path_.write_text(json.dumps(c.as_dict()), encoding='utf-8')
        if c.name == 'A':
            pa = str(path_)
        else:
            pb = str(path_)
    proofs = [{'module': c.name, 'ok': True, 'summary': {},
               'pads': {n: modules.module_state(st, c)['netPads'].get(n, [])
                        for n in c.boundary}}
              for c in (ca, cb)]
    r = modules.merge(None, [pa, pb], st['rules'], proofs=proofs,
                      theorems=[tha, thb], state=st)
    assert r['ok'], r['reasons']
    assert r['carried'] >= 1
    thb_bad = copy.deepcopy(thb)
    thb_bad['digest'] = '0'
    r2 = modules.merge(None, [pa, pb], st['rules'], proofs=proofs,
                       theorems=[tha, thb_bad], state=st)
    assert not r2['ok']
    assert any('stale theorem' in x for x in r2['reasons']), r2['reasons']
