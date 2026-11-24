"""P3/P4/P5 tests: packets, typed policy deltas, activation gate."""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from fluxpcb.meta.feedback import FailurePacket, gradient  # noqa: E402
from fluxpcb.meta.gate import validate_candidate  # noqa: E402
from fluxpcb.meta.policy import PolicyArtifact, propose_deltas  # noqa: E402


def _cert(**kw):
    base = {
        'plan_digest': 'p1', 'policy_digest': 'q1',
        'capacity': {'verdict': 'unknown'},
        'claims_after': {'n_errors': 2, 'n_unconnected': 1, 'total': 3,
                         'type_counts': {'clearance': 2}},
        'residue': {'router_fault': 2, 'unconnected': 1},
        'txn': {'log': [{'decision': 'rollback', 'why': 'new violation types',
                         'action': {'operators_run': ['maze_escape']}}]},
        'ledger': [{'operator': 'maze_escape', 'grid_paths': 1,
                    'exact_rejected': 1,
                    'first_blocker': {'type': 'track', 'short_by_mm': 0.017}}],
    }
    base.update(kw)
    return base


def test_packet_maps_axes_and_evidence():
    p = FailurePacket.from_cert('b1', _cert(), 'd3a')
    assert p.axis == {'routing': 3}
    assert p.feasibility['maze_grid_paths'] == 1
    assert p.blockers[0]['short_by_mm'] == 0.017
    assert p.digest() == FailurePacket.from_cert('b1', _cert(),
                                                 'd3a').digest()
    g = gradient([p])
    assert g['axis']['routing'] == 3 and g['feasibility']['maze_grid_paths'] == 1
    assert g['worst'][0]['board'] == 'b1'


def test_packet_capacity_infeasible_is_feasibility_evidence():
    p = FailurePacket.from_cert('h7', _cert(capacity={'verdict': 'infeasible'}))
    assert p.feasibility['capacity_infeasible'] is True


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
