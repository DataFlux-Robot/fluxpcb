"""Every transaction round is logged with what the action did.

A meta loop that only sees rolled-back rounds cannot attribute a pass, and
an action that failed to start is not evidence that a repair does not help.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from fluxpcb.harness import HarnessHook, HarnessPolicy  # noqa: E402
from fluxpcb.verifiers.txn import Claims, transactional_repair  # noqa: E402


def _claims(n, types):
    return Claims(n_errors=n, n_unconnected=0, error_types=frozenset(types),
                  type_counts=tuple(sorted((t, n) for t in types)))


def _write(path, text):
    with open(str(path), 'w') as fh:
        fh.write(text)


def test_commit_records_the_action():
    board = 'b.kicad_pcb'
    _write(board, 'start')
    state = {'n': 2}

    def action(path):
        state['n'] = 0
        return {'operators_run': ['widen_narrow_tracks'], 'repaired': 2,
                'failed': 0, 'ledger': [{'operator': 'widen_narrow_tracks'}]}

    def evaluate(path):
        return _claims(state['n'], ['track_width'] * state['n'])

    rep = transactional_repair(board, action, evaluate, max_rounds=2)
    assert rep.committed == 1
    entry = rep.log[0]
    assert entry['decision'] == 'commit' and entry['stage'] == 'repair'
    assert entry['action']['operators_run'] == ['widen_narrow_tracks']
    assert entry['action']['ledger'][0]['operator'] == 'widen_narrow_tracks'


def test_action_that_fails_to_start_is_an_error_not_a_rollback(tmp_path):
    board = str(tmp_path / 'b.kicad_pcb')
    _write(board, 'x')
    calls = {'eval': 0}

    def action(path):
        return {'operators_run': [], 'error': 'ModuleNotFoundError: fluxpcb'}

    def evaluate(path):
        calls['eval'] += 1
        return _claims(1, ['clearance'])

    rep = transactional_repair(board, action, evaluate, max_rounds=3)
    assert rep.errors == 1 and rep.rolled_back == 0
    assert rep.log[0]['decision'] == 'action_error'
    assert 'ModuleNotFoundError' in rep.log[0]['why']
    # the loop ends; it does not spend two more evaluation rounds
    assert calls['eval'] == 1
    assert rep.as_dict()['errors'] == 1


def test_no_op_action_ends_the_loop_without_an_evaluation():
    board = 'b2.kicad_pcb'
    _write(board, 'x')
    calls = {'eval': 0}

    def action(path):
        return {'no_op': True, 'operators_run': []}

    def evaluate(path):
        calls['eval'] += 1
        return _claims(1, ['clearance'])

    rep = transactional_repair(board, action, evaluate, max_rounds=5)
    assert calls['eval'] == 1 and rep.log == []


def test_denial_is_logged_with_its_reason():
    board = 'b3.kicad_pcb'
    _write(board, 'x')
    hook = HarnessHook(HarnessPolicy(name='t', max_actions=0))
    rep = transactional_repair(
        board, lambda p: {}, lambda p: _claims(1, ['clearance']),
        hook=hook, kind='ripup')
    assert rep.denied == 1
    assert rep.log[0]['decision'] == 'denied'
    assert rep.log[0]['stage'] == 'ripup'
    assert 'budget' in rep.log[0]['why']


def test_stages_do_not_share_an_action_budget():
    hook = HarnessHook(HarnessPolicy(name='t', max_actions=1))
    assert hook.before_action('repair')[0] is True
    assert hook.before_action('repair')[0] is False      # repair spent its own
    assert hook.before_action('ripup')[0] is True        # another stage
    assert hook.before_action('reroute')[0] is True
    assert hook.as_dict()['actions'] == 3


def test_stop_on_rollback_off_continues_to_the_next_round():
    board = 'b4.kicad_pcb'
    _write(board, 'x')
    seen = []

    def action(path):
        seen.append(len(seen))
        if len(seen) == 1:
            return {'operators_run': ['nudge_clearance'], 'no_help': True}
        return {'no_op': True, 'operators_run': []}

    rep = transactional_repair(board, action,
                               lambda p: _claims(1, ['clearance']),
                               max_rounds=4, stop_on_rollback=False)
    assert len(seen) == 2 and rep.rolled_back == 1


def test_a_hook_veto_is_recorded_as_denied_with_the_action():
    board = 'b5.kicad_pcb'
    _write(board, 'x')
    hook = HarnessHook(HarnessPolicy(name='t', allowed_ops=('widen',)))
    rep = transactional_repair(
        board, lambda p: {'operators_run': ['maze_escape']},
        lambda p: _claims(1, ['clearance']), hook=hook)
    assert rep.denied == 1
    assert rep.log[0]['action']['operators_run'] == ['maze_escape']
