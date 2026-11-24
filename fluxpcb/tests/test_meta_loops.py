"""Components 1 and 2: diagnosis, proposals, memory, selector, the gate."""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from fluxpcb.contracts import HarnessDiff  # noqa: E402
from fluxpcb.meta.arm import Arm, factorial_arms  # noqa: E402
from fluxpcb.meta.devloop import MetaDevLoop  # noqa: E402
from fluxpcb.meta.diagnose import diagnose  # noqa: E402
from fluxpcb.meta.feedback import FailurePacket  # noqa: E402
from fluxpcb.meta.memory import Memory, delta_key  # noqa: E402
from fluxpcb.meta.meta_loop import MetaMetaLoop, confirm, diff_from  # noqa: E402
from fluxpcb.meta.proposer import (candidate_arm, moves, propose,  # noqa: E402
                                   rule_proposals)
from fluxpcb.meta.strategy_store import (StrategyStore, choose,  # noqa: E402
                                         cluster_key, fit_selector)

BASE = [a for a in factorial_arms() if a.name == 'gate+batch'][0]


def _cell(board='B1', outcome='fail', **kw):
    base = {'board': board, 'set': 'd3a', 'arm': BASE.name,
            'arm_digest': BASE.digest(), 'outcome': outcome,
            'status': 'ok' if outcome in ('clean', 'fail') else outcome,
            'score': {'errors': 0 if outcome == 'clean' else 2,
                      'unconnected': 0 if outcome == 'clean' else 1,
                      'errorsByType': {} if outcome == 'clean' else {'clearance': 2}},
            'features': {'copper_layers': 2, 'pads_per_mm2': 0.3},
            'wall_seconds': 20.0, 'instrument_ok': True}
    base.update(kw)
    return base


def _trace(new_types=(), repaired=0, ops=('widen_narrow_tracks',),
           maze=None, connect=None, denials=()):
    ledger = []
    if maze is not None:
        ledger.append(dict({'operator': 'maze_escape', 'added': 0, 'failed': 1},
                           **maze))
    if connect is not None:
        ledger.append(dict({'operator': 'connect_unconnected'}, **connect))
    why = ("new violation types: [%s]" % ', '.join("'%s'" % t for t in new_types)
           if new_types else 'no improvement (weighted 4 -> 4; total 2 -> 2)')
    return {'repair': [{'stage': 'repair', 'mode': 'batch', 'committed': 0,
                        'rolled_back': 1, 'denied': 0, 'errors': 0,
                        'log': [{'round': 0, 'decision': 'rollback', 'why': why,
                                 'action': {'operators_run': list(ops),
                                            'repaired': repaired, 'failed': 1,
                                            'ledger': ledger}}]}],
            'harness': {'denials': list(denials)},
            'claims_after': {'n_errors': 2, 'n_unconnected': 1, 'total': 3,
                             'type_counts': {'clearance': 2}}}


def _packet(cert=None, **cell):
    return FailurePacket.from_cert(cell.get('board', 'B1'), cert or _trace(),
                                   'd3a', cell=_cell(**cell))


# -- diagnosis ----------------------------------------------------------
def test_diagnosis_names_the_variable_behind_each_pattern():
    p = _packet(_trace(new_types=('clearance',), repaired=8,
                       ops=('widen_narrow_tracks', 'connect_unconnected', 'maze_escape'),
                       maze={'grid_paths': 2, 'exact_rejected': 2,
                             'first_blocker': {'type': 'track', 'short_by_mm': 0.017}},
                       connect={'added': 0, 'failed': 1}))
    d = diagnose([p])
    names = {q.name: q for q in d['patterns']}
    assert names['drc_rejected_verified'].count == 1
    assert 'pad_clearance_strict' in [n for n, _ in names['drc_rejected_verified'].variables]
    assert names['widen_then_clearance'].variables[0][0] == 'blind_widen'
    assert names['maze_exact_rejected'].evidence['rejected'] == {'2': 1}
    assert 'corridor_offsets_mm' in [n for n, _ in names['connect_empty'].variables]
    assert 'residue:unconnected' in names and 'residue:clearance' in names
    assert p.blockers[0]['short_by_mm'] == 0.017


def test_diagnosis_is_fail_closed_on_a_broken_instrument():
    d = diagnose([_packet(instrument_ok=False,
                          instrument_problems=['selection leak: maze_escape'])])
    assert d['instrument_ok'] is False
    assert 'selection leak' in d['instrument_problems'][0]
    broken = _trace()
    broken['repair'][0]['errors'] = 1          # the reroute never started
    err = diagnose([_packet(broken)])
    assert err['instrument_ok'] is False
    assert 'action error' in err['instrument_problems'][0]


def test_clean_and_unscored_cells_carry_no_patterns():
    d = diagnose([_packet(outcome='clean'), _packet(status='crash', outcome='crash')])
    assert d['patterns'] == [] and d['instrument_ok'] is True


# -- proposer -----------------------------------------------------------
def test_rule_proposals_are_single_variable_and_validate():
    p = _packet(_trace(maze={'grid_paths': 3, 'exact_rejected': 3}))
    out = propose([p], BASE, memory=Memory(''), max_deltas=2, allow_llm=False)
    assert out['blocked_by'] is None
    assert len(out['proposals']) == 2
    for pr in out['proposals']:
        cand = candidate_arm(BASE, pr)
        assert pr.proposer == 'rules'
        assert len(cand.param_deltas(BASE)) == 1
        assert cand.validate() == []
    assert {pr.variable for pr in out['proposals']} <= {
        'maze_grid_extra_mm', 'maze_max_expand'}


def test_a_tried_delta_is_not_proposed_again():
    p = _packet(_trace(maze={'grid_paths': 3, 'exact_rejected': 3}))
    mem = Memory('')
    first = propose([p], BASE, memory=mem, max_deltas=1, allow_llm=False)['proposals'][0]
    mem.append('result', delta=first.key(BASE.name), outcome='rejected')
    again = propose([p], BASE, memory=mem, max_deltas=1, allow_llm=False)['proposals']
    assert again and again[0].key(BASE.name) != first.key(BASE.name)


def test_nothing_is_proposed_when_the_instrument_is_broken():
    p = _packet(instrument_ok=False, instrument_problems=['no repair log'])
    out = propose([p], BASE, memory=Memory(''), allow_llm=False)
    assert out['proposals'] == [] and out['blocked_by'] == 'instrument'


def test_moves_respect_the_schema_and_booleans_flip_once():
    for name, _schema, cur in BASE.params.variables():
        for value in moves(name, cur):
            BASE.with_param(name, value)          # raises if it does not compile
    assert moves('blind_widen', True) == [False]
    assert moves('blind_widen', False) == []
    assert moves('pad_clearance_strict', True) == []
    assert max(moves('maze_grid_extra_mm', 0.48)) <= 0.5


def test_rule_proposals_skip_variables_marked_as_instrument_issues():
    diag = {'instrument_ok': True, 'patterns': [
        {'name': 'x', 'count': 3, 'variables': [('__instrument__', 'via')]}]}
    assert rule_proposals(diag, BASE, set(), 3) == []


# -- memory -------------------------------------------------------------
def test_memory_is_append_only_chained_and_remembers_what_was_tried(tmp_path):
    path = str(tmp_path / 'memory.jsonl')
    m = Memory(path)
    key = delta_key(BASE.name, 'maze_cap', 12)
    m.append('proposal', delta=key, variable='maze_cap')
    assert key in m.pending()
    m.append('result', delta=key, outcome='rejected')
    m.append('constraint', rule='never lower a gate')
    assert m.tried() == {key: 'rejected'} and m.pending() == set()
    assert m.constraints()[0]['rule'] == 'never lower a gate'
    assert m.chain_ok
    again = Memory(path)                        # survives a restart
    assert again.tried() == {key: 'rejected'} and again.chain_ok
    # tampering with one entry breaks the chain
    lines = open(path, encoding='utf-8').read().splitlines()
    import json
    body = json.loads(lines[1])
    body['outcome'] = 'admitted'
    lines[1] = json.dumps(body)
    open(path, 'w', encoding='utf-8').write('\n'.join(lines) + '\n')
    assert not Memory(path).chain_ok


# -- selector -----------------------------------------------------------
def _pk(arm, board, outcome, layers=2, density=0.3):
    return FailurePacket.from_cert(board, {'claims_after': {}}, 'd3a', cell=_cell(
        board=board, outcome=outcome, arm=arm.name, arm_digest=arm.digest(),
        features={'copper_layers': layers, 'pads_per_mm2': density},
        score={'errors': 0 if outcome == 'clean' else 1, 'unconnected': 0,
               'errorsByType': {} if outcome == 'clean' else {'clearance': 1}}))


def test_selector_defaults_to_the_best_arm_and_needs_evidence_for_a_rule():
    arms = {a.name: a for a in factorial_arms()}
    pk = []
    for i in range(6):                      # dense boards: preroute wins them all
        pk.append(_pk(arms['gate+batch'], f'D{i}', 'fail', density=0.9))
        pk.append(_pk(arms['preroute+batch'], f'D{i}', 'clean', density=0.9))
    for i in range(4):                      # sparse boards: both pass
        pk.append(_pk(arms['gate+batch'], f'S{i}', 'clean', density=0.02))
        pk.append(_pk(arms['preroute+batch'], f'S{i}', 'clean', density=0.02))
    sel = fit_selector(pk)
    assert sel['default'] == 'preroute+batch' and sel['rules'] == []
    assert choose(sel, {'copper_layers': 2, 'pads_per_mm2': 0.9}) == 'preroute+batch'


def test_selector_rule_needs_support_and_gain():
    arms = {a.name: a for a in factorial_arms()}
    pk = []
    for i in range(5):
        pk += [_pk(arms['gate+batch'], f'A{i}', 'fail', density=0.9),
               _pk(arms['preroute+batch'], f'A{i}', 'clean', density=0.9)]
    for i in range(12):
        pk += [_pk(arms['gate+batch'], f'B{i}', 'clean', density=0.02),
               _pk(arms['preroute+batch'], f'B{i}', 'fail', density=0.02)]
    sel = fit_selector(pk)
    assert sel['default'] == 'gate+batch'
    rule = [r for r in sel['rules'] if r['key'] == cluster_key(
        {'copper_layers': 2, 'pads_per_mm2': 0.9})]
    assert rule and rule[0]['arm'] == 'preroute+batch' and rule[0]['gain'] == 5
    assert rule[0]['in_sample'] is True
    assert choose(sel, {'copper_layers': 2, 'pads_per_mm2': 0.9}) == 'preroute+batch'
    assert choose(sel, {'copper_layers': 2, 'pads_per_mm2': 0.02}) == 'gate+batch'
    # one board is not a rule
    assert fit_selector(pk[:2])['rules'] == []


def test_strategy_store_round_trips_a_selector(tmp_path):
    store = StrategyStore(str(tmp_path / 'store'))
    key = store.save({'default': 'gate+batch',
                      'rules': [{'key': 'L2_high', 'arm': 'preroute+batch'}]})
    got = store.load_for({'copper_layers': 2, 'pads_per_mm2': 0.9})
    assert got == {'arm': 'preroute+batch', 'rule': 'L2_high', 'selector': key}
    assert store.load_for({'copper_layers': 4, 'pads_per_mm2': 0.1})['rule'] == 'default'


# -- component 1 --------------------------------------------------------
def test_generation_proposes_evaluates_and_records():
    mem = Memory('')
    loop = MetaDevLoop(memory=mem, allow_llm=False, max_candidates=2)
    pk = [_packet(_trace(maze={'grid_paths': 3, 'exact_rejected': 3}))]
    seen = []

    def evaluator(candidate):
        seen.append(candidate.name)
        return {'wins': 0, 'losses': 1, 'boards': 4, 'mean_wall': 30.0}

    res = loop.generation(pk, BASE, evaluator=evaluator, generation=1)
    assert res.status == 'ok' and res.candidates_evaluated == 2
    assert res.admitted == []                 # cleaned nothing, dirtied one
    assert seen == res.candidates
    outcomes = {e['candidate']: e['outcome'] for e in mem.entries('result')}
    assert set(outcomes.values()) == {'rejected'}
    assert len(mem.entries('proposal')) == 2
    for name in res.candidates:
        arm = loop.arm(name)
        assert arm is not None and len(arm.param_deltas(BASE)) == 1


def test_generation_reports_why_it_stopped():
    loop = MetaDevLoop(memory=Memory(''), allow_llm=False)
    blocked = loop.generation([_packet(instrument_ok=False,
                                       instrument_problems=['x'])], BASE,
                              evaluator=lambda c: None, generation=1)
    assert blocked.status == 'blocked' and blocked.blocked_by == 'instrument'
    assert loop.generation([], BASE, evaluator=lambda c: None).status == 'no_data'
    res = loop.generation([_packet(outcome='clean')], BASE, evaluator=None)
    assert res.candidates == [] and res.status == 'no_proposal'


def test_select_refuses_to_invent_scores_and_picks_net_gain():
    loop = MetaDevLoop(memory=Memory(''), allow_llm=False)
    with pytest.raises(RuntimeError):
        loop.select([], evaluator=None)
    from fluxpcb.contracts import RoutingCandidate
    a = RoutingCandidate(name='a', category='one_variable', params={})
    b = RoutingCandidate(name='b', category='one_variable', params={})
    best, key = loop.select([a, b], evaluator=lambda c: {
        'a': {'wins': 3, 'losses': 1}, 'b': {'wins': 2, 'losses': 0}}[c.name])
    assert best.name == 'b' and key == (2, 0, 0)   # fewer dirty boards wins the tie-break


def test_on_policy_boards_are_where_the_incumbent_struggles():
    committed = {'repair': [{'stage': 'repair', 'mode': 'batch',
                             'log': [{'round': 0, 'decision': 'commit',
                                      'action': {'operators_run': ['widen_narrow_tracks'],
                                                 'repaired': 3, 'failed': 0}}]}]}
    pk = [_packet(outcome='clean'),                                   # easy: no repair
          _packet(committed, board='B2', outcome='clean'),            # only passes via repair
          _packet(board='B3', outcome='fail'),
          _packet(_trace(maze={}), board='B4', outcome='clean',
                  arm='direct+batch')]
    assert MetaDevLoop.on_policy(pk, BASE.name) == ['B2', 'B3']


# -- component 2 --------------------------------------------------------
def _evidence(**kw):
    base = {'kind': 'param', 'delta': 'maze_cap=12', 'instrument_ok': True,
            'instrument_problems': [], 'action_errors': 0,
            'coreset': {'wins': 3, 'losses': 0, 'boards': 12},
            'noise_flips': 0,
            'heldout': {'n': 40, 'incumbent_clean': 30, 'candidate_clean': 31,
                        'lost': [], 'gained': ['x']},
            'controls': {'n': 8, 'clean': []},
            'scope_narrowed': False,
            'ahr': {'incumbent': 0.30, 'candidate': 0.29}}
    base.update(kw)
    return base


def test_gate_admits_only_on_complete_clean_evidence():
    v = confirm(_evidence())
    assert v['ok'] and set(v['checks'].values()) == {'pass'}
    assert v['heldout_delta'] > 0


@pytest.mark.parametrize('change, fragment', [
    ({'instrument_ok': False, 'instrument_problems': ['selection leak']},
     'instrument'),
    ({'action_errors': 1}, 'failed to start'),
    ({'coreset': {'wins': 1, 'losses': 1, 'boards': 12}}, 'no net gain'),
    ({'coreset': {'wins': 3, 'losses': 1, 'boards': 12}}, 'A/A noise'),
    ({'heldout': None}, 'held-out: not measured'),
    ({'heldout': {'n': 40, 'incumbent_clean': 30, 'candidate_clean': 29,
                  'lost': ['a', 'b']}}, 'regressed'),
    ({'heldout': {'n': 40, 'incumbent_clean': 30, 'candidate_clean': 31,
                  'lost': ['a', 'b']}}, 'boards lost'),
    ({'controls': {'n': 8, 'clean': ['ctl_3']}}, 'known-bad'),
    ({'controls': None}, 'controls: not measured'),
    ({'scope_narrowed': True}, 'SII'),
    ({'ahr': {'incumbent': 0.30, 'candidate': 0.36}}, 'AHR regressed'),
    ({'ahr': None}, 'fab-profile oracle: not measured'),
])
def test_gate_rejects_each_missing_or_bad_piece_of_evidence(change, fragment):
    v = confirm(_evidence(**change))
    assert not v['ok']
    assert any(fragment in r for r in v['reasons']), v['reasons']


def test_noise_inside_which_a_loss_is_not_an_effect():
    assert confirm(_evidence(noise_flips=1, coreset={'wins': 3, 'losses': 1}))['ok']
    assert not confirm(_evidence(noise_flips=1, coreset={'wins': 3, 'losses': 2}))['ok']


def test_diff_from_carries_the_evidence_into_the_contract_type():
    d = diff_from(_evidence())
    assert MetaMetaLoop().validate(d) is True
    assert MetaMetaLoop().validate(diff_from(_evidence(heldout=None))) is False
    assert MetaMetaLoop().validate(HarnessDiff(changes={'x': 1})) is False


def test_improve_retires_what_failed_twice_and_focuses_what_worked():
    gen = {'generation': 1, 'results': [
        {'candidate': 'gate+batch~maze_cap=12', 'wins': 0, 'losses': 1},
        {'candidate': 'gate+batch~maze_max_expand=240000', 'wins': 0, 'losses': 0}],
        'proposals': [{'variable': 'maze_cap', 'value': 12},
                      {'variable': 'maze_max_expand', 'value': 240000}],
        'diagnosis': {'patterns': [{'name': 'maze_exact_rejected', 'count': 5,
                                   'variables': [('maze_cap', 'raise')]}]}}
    diff = MetaMetaLoop().improve([gen])
    assert diff.changes['largest_pattern'] == 'maze_exact_rejected'
    assert diff.changes['retire'] == []            # tried once is not a verdict
    twice = [gen, dict(gen, generation=2)]
    assert MetaMetaLoop().improve(twice).changes['retire'] == ['maze_cap', 'maze_max_expand']
    loop = MetaMetaLoop()
    loop.confirm(_evidence())
    assert len(loop.confirmations) == 1 and loop.diff_count == 0


def test_an_interrupted_generation_finishes_its_pending_proposals(tmp_path):
    """A proposal without a verdict is measured first, never skipped."""
    import json
    mem = Memory(str(tmp_path / 'memory.jsonl'))
    pk = [_packet(_trace(maze={'grid_paths': 3, 'exact_rejected': 3}))]
    loop = MetaDevLoop(memory=mem, allow_llm=False, max_candidates=2)
    first = loop.generation(pk, BASE, evaluator=lambda c: None, generation=1)
    assert first.status == 'no_evaluator' and len(first.candidates) == 2
    assert len(mem.pending()) == 2                  # proposed, never judged
    # the session dies here; a new loop over the same memory resumes
    again = MetaDevLoop(memory=Memory(str(tmp_path / 'memory.jsonl')),
                        allow_llm=False, max_candidates=2)
    judged = []
    res = again.generation(pk, BASE, generation=1,
                           evaluator=lambda c: (judged.append(c.name),
                                                {'wins': 0, 'losses': 1})[1])
    assert judged == first.candidates               # the same two, not new ones
    assert res.status == 'ok' and res.admitted == []
    mem2 = Memory(str(tmp_path / 'memory.jsonl'))
    assert mem2.pending() == set()
    # with nothing pending, the next generation proposes something else
    third = MetaDevLoop(memory=mem2, allow_llm=False, max_candidates=2)
    fresh = third.generation(pk, BASE, generation=2, evaluator=lambda c: None)
    assert fresh.status == 'no_evaluator'
    assert not set(fresh.candidates) & set(first.candidates)
