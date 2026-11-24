"""P3 tests: harness load/deny, SII, gate weakening, meta validate."""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from fluxpcb.contract.scope import Scope  # noqa: E402
from fluxpcb.contracts import HarnessDiff  # noqa: E402
from fluxpcb.harness import HarnessHook, HarnessPolicy  # noqa: E402
from fluxpcb.meta.meta_loop import MetaMetaLoop  # noqa: E402
from fluxpcb.session import DesignSession  # noqa: E402
from fluxpcb.verifiers.txn import Claims, transactional_repair  # noqa: E402


def _policy(**kw):
    base = dict(name='t', max_actions=2, max_seconds=60,
                allowed_ops=('widen_narrow_tracks',))
    base.update(kw)
    return HarnessPolicy(**base)


def _scope(declared=('a', 'b'), claims=('c1',), assumptions=()):
    return Scope(board='b', project_sha='p', dsn_sha='d', register_digest='r',
                 claim_set=claims, assumptions=assumptions,
                 declared_rule_ids=declared)


def test_policy_load_missing_fails_closed(tmp_path):
    with pytest.raises(FileNotFoundError):
        HarnessPolicy.load(str(tmp_path / 'nope.json'))


def test_action_budget_and_op_denial():
    h = HarnessHook(_policy(max_actions=1))
    assert h.before_action('repair')[0] is True
    assert h.after_action(['widen_narrow_tracks'])[0] is True
    assert h.before_action('repair')[0] is False
    h2 = HarnessHook(_policy())
    assert h2.after_action(['plane_bridge'])[0] is False


def test_sii_scope_narrowing_rejected():
    before = _scope(declared=('a', 'b'), claims=('c1', 'c2'))
    after = _scope(declared=('a',), claims=('c1',))
    hook = HarnessHook(_policy())
    ok, why = hook.before_commit(before, after)
    assert not ok and 'SII' in why
    assert hook.before_commit(before, before)[0] is True


def test_sii_scope_widening_allowed():
    before = _scope(declared=('a',))
    after = _scope(declared=('a', 'b'))
    assert after.is_superset(before)
    assert HarnessHook(_policy()).before_commit(before, after)[0] is True


def test_gate_weakening_requires_authority():
    hook = HarnessHook(_policy())
    ok, why = hook.before_commit(None, None, gate_changes={'clearance': '0.2->0.1'})
    assert not ok and 'gate changes' in why
    allow = HarnessHook(_policy(allow_gate_weakening=True))
    assert allow.before_commit(None, None,
                               gate_changes={'x': 1})[0] is True


def test_meta_validate_requires_heldout_positive_and_no_narrowing():
    m = MetaMetaLoop()
    assert m.validate(HarnessDiff(changes={'x': 1})) is False
    assert m.validate(HarnessDiff(changes={'x': 1}, heldout_delta=0.0)) is False
    assert m.validate(HarnessDiff(changes={'x': 1}, heldout_delta=-1.0)) is False
    assert m.validate(HarnessDiff(changes={'x': 1}, heldout_delta=0.1,
                                  scope_narrowed=True)) is False
    assert m.validate(HarnessDiff(changes={'x': 1}, heldout_delta=0.1)) is True


def test_session_repair_with_hook_denies(tmp_path):
    board = tmp_path / 'b.kicad_pcb'
    board.write_text('ORIGINAL')

    def action(path):
        path = str(path)
        open(path, 'w').write('MUTATED')
        return {'operators_run': ['plane_bridge']}

    def evaluate(path):
        return Claims(n_errors=1, error_types=frozenset({'track_width'}),
                      type_counts=(('track_width', 1),))

    hook = HarnessHook(_policy(max_actions=2))
    claim = evaluate(str(board))
    s = DesignSession(str(board), _scope(), hook=hook)
    rep = s.repair(action, evaluate, max_rounds=1, initial=claim)
    assert rep.denied == 1
    assert board.read_text() == 'ORIGINAL'
