"""P3/P4/P5 tests: packets, typed policy deltas, activation gate."""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from fluxpcb.meta.feedback import (FailurePacket, gradient,  # noqa: E402
                                    residue_class)
from fluxpcb.meta.gate import validate_candidate  # noqa: E402
from fluxpcb.meta.policy import PolicyArtifact, propose_deltas  # noqa: E402


def _cert(**kw):
    """A cert in the current format: every round logged with its ledger."""
    base = {
        'plan_digest': 'p1', 'policy_digest': 'q1', 'arm_digest': 'q1',
        'arm': {'name': 'gate+batch'},
        'capacity': {'verdict': 'unknown'},
        'claims_after': {'n_errors': 2, 'n_unconnected': 1, 'total': 3,
                         'type_counts': {'clearance': 2}},
        'residue': {'router_fault': 2, 'unconnected': 1},
        'repair': [{
            'stage': 'repair', 'mode': 'batch', 'committed': 0,
            'rolled_back': 1, 'denied': 0, 'errors': 0,
            'log': [{'round': 0, 'decision': 'rollback',
                     'why': "new violation types: ['clearance']",
                     'action': {'operators_run': ['maze_escape'],
                                'repaired': 1, 'failed': 1,
                                'ledger': [{
                                    'operator': 'maze_escape',
                                    'grid_paths': 1, 'exact_rejected': 1,
                                    'first_blocker': {'type': 'track',
                                                      'short_by_mm': 0.017}}]}}]}],
        'harness': {'denials': []},
    }
    base.update(kw)
    return base


def _cell(**kw):
    base = {'board': 'b1', 'set': 'd3a', 'arm': 'gate+batch',
            'arm_digest': 'q1', 'outcome': 'fail', 'status': 'ok',
            'score': {'errors': 2, 'unconnected': 1,
                      'errorsByType': {'clearance': 2}},
            'features': {'pads': 10}, 'wall_seconds': 12.0,
            'instrument_ok': True}
    base.update(kw)
    return base


def _packet(**cell):
    return FailurePacket.from_cert('b1', _cert(), 'd3a', cell=_cell(**cell))


def test_packet_maps_axes_and_evidence():
    p = _packet()
    assert p.axis == {'routing': 3}
    assert p.feasibility['maze_grid_paths'] == 1
    assert p.feasibility['maze_exact_rejected'] == 1
    assert p.blockers[0]['short_by_mm'] == 0.017
    assert p.rounds[0]['new_types'] == ['clearance']
    assert [e['operator'] for e in p.operator_trace] == ['maze_escape']
    assert p.digest() == _packet().digest()
    g = gradient([p])
    assert g['axis']['routing'] == 3
    assert g['feasibility']['maze_grid_paths'] == 1
    assert g['residue'] == {'clearance': 1, 'unconnected': 1}
    assert g['worst'][0]['board'] == 'b1'


def test_packet_reads_the_older_txn_format():
    cert = {'claims_after': {'n_errors': 1, 'n_unconnected': 0, 'total': 1,
                             'type_counts': {'track_width': 1}},
            'txn': {'log': [{'round': 0, 'decision': 'commit',
                             'action': {'operators_run': ['widen_narrow_tracks'],
                                        'repaired': 1, 'failed': 0, 'ledger': []}}]}}
    p = FailurePacket.from_cert('b0', cert, 'd3a', cell=_cell(
        board='b0', outcome='clean',
        score={'errors': 0, 'unconnected': 0, 'errorsByType': {}}))
    assert p.rounds[0]['decision'] == 'commit'
    assert p.rounds[0]['stage'] == 'repair'
    assert residue_class(p) == []


def test_policy_deltas_are_typed_and_digest_changes():
    p = PolicyArtifact()
    q = p.with_delta({'kind': 'budget', 'value': {'maze_seconds': 60}}, 'x')
    assert q.budgets['maze_seconds'] == 60
    assert q.rationale_log == ('x',)
    assert q.digest() != p.digest()
    r = q.with_delta({'kind': 'disable_op', 'value': 'maze_escape'})
    assert 'maze_escape' not in r.operator_portfolio
    with pytest.raises(ValueError):
        p.with_delta({'kind': 'nonsense', 'value': 1})


def test_proposer_only_emits_compilable_deltas():
    infeasible = FailurePacket.from_cert('h7', _cert(
        capacity={'verdict': 'infeasible'},
        claims_after={'n_errors': 0, 'n_unconnected': 0, 'total': 0,
                      'type_counts': {}}))
    deltas = propose_deltas([infeasible])
    assert {d['kind'] for d in deltas} == {'planning'}
    for d in deltas:
        PolicyArtifact().with_delta(d)      # compiles


def test_gate_requires_positive_heldout_and_no_narrowing():
    inc = {'routing_clean': 5, 'boards': 10, 'total_errors': 10,
           'seconds': 1.0}
    cand = {'routing_clean': 6, 'boards': 10, 'total_errors': 9,
            'seconds': 1.0}
    ok = validate_candidate(inc, cand, incumbent_heldout=inc,
                            candidate_heldout=cand)
    assert ok['ok'] and ok['heldout_delta'] > 0
    flat = validate_candidate(inc, cand, incumbent_heldout=inc,
                              candidate_heldout=inc)
    assert not flat['ok'] and 'held-out' in ' '.join(flat['reasons'])
    narrow = validate_candidate(inc, cand, incumbent_heldout=inc,
                                candidate_heldout=cand, scope_narrowed=True)
    assert not narrow['ok']
    ahr = validate_candidate(inc, cand, incumbent_heldout=inc,
                             candidate_heldout=cand, ahr_delta=0.05)
    assert not ahr['ok']
