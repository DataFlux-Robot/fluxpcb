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


def _gap_state():
    st = {'board': 'gap', 'layers': ['F.Cu'],
          'items': [_pad('pa', -600000, -100000, 0, 100000),
                    _seg('s1', 0, 0, 1000000, 0),
                    _seg('s2', 1250000, 0, 2000000, 0),
                    _pad('pb', 2000000, -100000, 2600000, 100000)],
          'rules': {'clearance': 150000, 'track_min': 150000},
          'netPads': {'N': ['pa', 'pb']}}
    return st_mod.freeze(st)


def test_two_tracks_with_a_copper_gap_are_not_connected():
    # 0.2 mm wide, centre lines 0.25 mm apart -> 0.05 mm copper gap.
    # A full-width radius (0.4 mm) would have bridged them; half-widths do not.
    _needs_lean()
    st = _gap_state()
    cl = claims.bench_claims(st)
    cs = certs.connectivity_certs(st)
    edge_ids = {tuple(sorted(e)) for c in cs for e in c['edges']}
    assert ('s1', 's2') not in edge_ids
    r = gate.verify(st, cl, cs)
    by = {c['id']: c for c in r['lean']['claims']}
    assert by['CONN:N']['status'] == 'unknown', by['CONN:N']
    assert not r['ok']


def test_kernel_rejects_a_certificate_edge_across_a_copper_gap():
    _needs_lean()
    st = _gap_state()
    cl = claims.bench_claims(st)
    bad = [{'claim': 'CONN:N', 'kind': 'tree',
            'edges': [['pa', 's1'], ['s1', 's2'], ['s2', 'pb']]}]
    r = gate.verify(st, cl, bad)
    by = {c['id']: c for c in r['lean']['claims']}
    assert by['CONN:N']['status'] == 'invalid', by['CONN:N']
    assert not r['ok']


def test_board_rules_come_from_the_netclasses(tmp_path):
    import json
    pro = tmp_path / 'b.kicad_pro'
    pro.write_text(json.dumps({
        'board': {'design_settings': {'rules': {
            'min_clearance': 0.0, 'min_track_width': 0.1524}}},
        'net_settings': {'classes': [
            {'name': 'Default', 'clearance': 0.1524},
            {'name': 'Power', 'clearance': 0.254}]}}), encoding='utf-8')
    rules, src = st_mod.board_rules(tmp_path / 'b.kicad_pcb')
    assert rules['clearance'] == 152400
    assert rules['track_min'] == 152400
    assert 'netclass' in src['clearance']
    pro.write_text(json.dumps({
        'board': {'design_settings': {'rules': {'min_clearance': 0.3}}},
        'net_settings': {'classes': [{'clearance': 0.1524}]}}),
        encoding='utf-8')
    rules, _ = st_mod.board_rules(tmp_path / 'b.kicad_pcb')
    assert rules['clearance'] == 300000  # the board floor wins


def _jmp(ref1, ref2):
    st = {'board': 'jmp', 'layers': ['F.Cu'],
          'items': [{'id': 'p1', 'net': 'A', 'layers': ['F.Cu'], 'ref': ref1,
                     'shape': {'kind': 'poly', 'pts': [[0, 0], [500000, 0],
                                                       [500000, 500000], [0, 500000]]}},
                    {'id': 'p2', 'net': 'B', 'layers': ['F.Cu'], 'ref': ref2,
                     'shape': {'kind': 'poly', 'pts': [[0, 600000], [500000, 600000],
                                                       [500000, 1100000], [0, 1100000]]}}],
          'rules': {'clearance': 200000, 'track_min': 150000}, 'netPads': {}}
    return st_mod.freeze(st)


def test_same_footprint_pads_are_exempt_from_clearance():
    # Solder-jumper / fine-pitch case: 0.1 mm between pads of one footprint.
    st = _jmp('JMP1', 'JMP1')
    rep = fab.check(st, [{'id': 'FAB:CLEARANCE', 'pred': 'clearance',
                          'scope': ['p1', 'p2'], 'rule': 200000}], st['rules'])
    assert rep['claims'][0]['status'] == 'proven', rep['claims'][0]
    st = _jmp('JMP1', 'JMP2')
    rep = fab.check(st, [{'id': 'FAB:CLEARANCE', 'pred': 'clearance',
                          'scope': ['p1', 'p2'], 'rule': 200000}], st['rules'])
    assert rep['claims'][0]['status'] == 'refuted', rep['claims'][0]


def test_pred_pinning_rejects_a_misspelled_predicate():
    _needs_lean()
    st = _clean()
    cl = claims.bench_claims(st)
    for c in cl:
        if c['id'] == 'FAB:CLEARANCE':
            c['pred'] = 'clearanceX'
    want = gate.required_claims(st)
    r = gate.verify(st, cl, certs.connectivity_certs(st), obligations=want)
    assert not r['ok']
    assert any('pred' in x and 'FAB:CLEARANCE' in x for x in r['reasons']), r['reasons']
    with pytest.raises(gate.GateDenied):
        gate.commit_step(st, st, [], cl, certs.connectivity_certs(st))


def test_obligation_coverage_is_kernel_checked():
    _needs_lean()
    st = _clean()
    cl = [c for c in claims.bench_claims(st) if c['id'] != 'NETLIST']
    want = gate.required_claims(st)
    r = gate.verify(st, cl, certs.connectivity_certs(st), obligations=want)
    assert not r['ok']
    assert r['lean']['coverage_ok'] is False
    assert 'NETLIST' in r['lean']['uncovered']


def test_carried_claim_is_proven_without_a_certificate():
    _needs_lean()
    st = _clean()
    cl = claims.bench_claims(st)
    cs = [c for c in certs.connectivity_certs(st) if c['claim'] != 'CONN:N']
    scope = next(c['scope'] for c in cl if c['id'] == 'CONN:N')
    r = gate.verify(st, cl, cs, carried=[('CONN:N', list(scope))],
                    obligations=gate.required_claims(st))
    assert r['ok'], r['reasons']
    by = {c['id']: c for c in r['lean']['claims']}
    assert by['CONN:N']['status'] == 'carried'


def test_carried_scope_must_equal_the_claim_scope():
    _needs_lean()
    st = _clean()
    cl = claims.bench_claims(st)
    cs = [c for c in certs.connectivity_certs(st) if c['claim'] != 'CONN:N']
    r = gate.verify(st, cl, cs, carried=[('CONN:N', ['pa'])])
    by = {c['id']: c for c in r['lean']['claims']}
    assert by['CONN:N']['status'] == 'unknown'
    assert not r['ok']


def test_before_digest_is_checked():
    _needs_lean()
    st = _clean()
    before = copy.deepcopy(st)
    before['digest'] = '123'
    r = gate.verify(st, claims.bench_claims(st), certs.connectivity_certs(st),
                    before=before, touched=[], carried=[])
    assert r['lean']['drift_before'] is True
    assert not r['ok']


def test_rule_change_breaks_the_frame():
    _needs_lean()
    before = _clean()
    after = copy.deepcopy(before)
    after['rules'] = {'clearance': 160000, 'track_min': 150000}
    after = st_mod.freeze(after)
    r = gate.verify(after, claims.bench_claims(after),
                    certs.connectivity_certs(after), before=before, touched=[])
    assert r['lean']['frame_ok'] is False
    assert not r['ok']


def test_footprint_change_breaks_the_frame():
    _needs_lean()
    before = _clean()
    after = copy.deepcopy(before)
    after['footprints'] = {'U1': ['pa', 'pb']}
    after = st_mod.freeze(after)
    r = gate.verify(after, claims.bench_claims(after),
                    certs.connectivity_certs(after), before=before, touched=[])
    assert r['lean']['frame_ok'] is False


def test_duplicate_claim_ids_are_rejected():
    _needs_lean()
    st = _clean()
    cl = claims.bench_claims(st) + [claims.bench_claims(st)[0]]
    r = gate.verify(st, cl, certs.connectivity_certs(st))
    assert r['lean']['duplicate_ids'] is True
    assert not r['ok']


def _two_net_state(connect_m):
    items = [_pad('pa', 0, 0, 600000, 600000),
             _seg('t1', 300000, 300000, 5000000, 300000),
             _pad('pb', 4700000, 0, 5300000, 600000),
             _pad('pc', 0, 2000000, 600000, 2600000, net='M'),
             _pad('pd', 4700000, 2000000, 5300000, 2600000, net='M')]
    if connect_m:
        items.append(_seg('t2', 300000, 2300000, 5000000, 2300000, net='M'))
    st = {'board': 'two', 'layers': ['F.Cu'], 'items': items,
          'rules': {'clearance': 150000, 'track_min': 150000},
          'netPads': {'N': ['pa', 'pb'], 'M': ['pc', 'pd']}}
    return st_mod.freeze(st)


def test_preserve_keeps_proven_claims_and_denies_a_lost_proof():
    _needs_lean()
    from fluxpcb.proof import step
    before = _two_net_state(False)
    after = _two_net_state(True)
    ok = step.preserve(before, after, before['rules'])
    assert ok['ok'], ok['reasons']
    assert 'CONN:N' in ok['carried']
    assert 'CONN:M' not in ok['carried']
    # now the reverse: a proof that existed is lost
    bad = step.preserve(after, before, before['rules'])
    assert not bad['ok']
    assert any('CONN:M' in x for x in bad['reasons']), bad['reasons']
    assert 'CONN:N' in bad['carried']


def test_publish_requires_every_claim():
    _needs_lean()
    from fluxpcb.proof import step
    assert step.publish(_two_net_state(True), {'clearance': 150000,
                                              'track_min': 150000})['ok']
    r = step.publish(_two_net_state(False), {'clearance': 150000,
                                            'track_min': 150000})
    assert not r['ok']
    assert any('CONN:M' in x for x in r['reasons'])


def test_store_is_fresh_only_while_the_scope_is_unchanged(tmp_path):
    from fluxpcb.proof import store as store_mod
    st = _clean()
    s = store_mod.Store(tmp_path / 'proofs.json')
    c = next(c for c in claims.bench_claims(st) if c['id'] == 'CONN:N')
    s.put(c, 'proven', '', st)
    assert s.get(c, st) is not None
    moved = copy.deepcopy(st)
    moved['items'][1]['shape']['y1'] += 1
    moved = st_mod.freeze(moved)
    assert s.get(c, moved) is None
    rule_changed = copy.deepcopy(st)
    rule_changed['rules'] = {'clearance': 160000, 'track_min': 150000}
    rule_changed = st_mod.freeze(rule_changed)
    assert s.get(c, rule_changed) is None


def test_maintain_reuses_untouched_claims(tmp_path):
    _needs_lean()
    from fluxpcb.proof import framework
    st = _clean()
    r = gate.verify(st, claims.bench_claims(st), certs.connectivity_certs(st),
                    obligations=gate.required_claims(st))
    prior = {'schema': 'fluxpcb-cert-2', 'rules': dict(st['rules']),
             'claims': [{'id': c['id'], 'kernel': 'lean',
                         'status': c['status']} for c in r['lean']['claims']],
             'items': st_mod.item_hashes(st)}
    prior['claims'] += [{'id': c['id'], 'kernel': 'fab', 'status': c['status']}
                        for c in r['fab']['claims']]
    # untouched: everything carried, fab re-proven
    m = framework.maintain(state=st, prior=prior)
    assert m['ok'], m['reasons']
    assert 'CONN:N' in m['carried'] and 'NETLIST' in m['carried']
    # touched: moving the track changes CONN:N's scope -> it is re-proven
    moved = copy.deepcopy(st)
    moved['items'][1]['shape']['y1'] += 1
    moved = st_mod.freeze(moved)
    m2 = framework.maintain(state=moved, prior=prior)
    assert m2['ok'], m2['reasons']
    assert 'CONN:N' not in m2['carried']
    assert 'CONN:N' in {c['id'] for c in m2['claims'] if c['status'] == 'proven'}


def test_duplicate_copper_layer_names_get_layer_ids():
    from fluxpcb.proof.state import _unique_layer_keys
    assert _unique_layer_keys(['top', 'gnd', 'pwr', 'pwr', 'gnd', 'bot'],
                              [0, 4, 6, 8, 10, 2]) == \
        ['top', 'gnd#4', 'pwr#6', 'pwr#8', 'gnd#10', 'bot']
    assert _unique_layer_keys(['F.Cu', 'B.Cu'], [0, 31]) == ['F.Cu', 'B.Cu']
