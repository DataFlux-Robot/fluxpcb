"""The policy selects operators when dispatch CHOOSES them.

A policy used to be enforced only afterwards, as a veto over the whole
batch: one disallowed operator threw away every repair on that board
(13 boards in one round).  These tests pin the selection-at-dispatch rule.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from fluxpcb.verifiers.operator_spec import TEXT_TO_SILK  # noqa: E402
from fluxpcb.verifiers.operators import (RepairOperators,  # noqa: E402
                                         RepairResult, Violation)

CHAIN = ('width_cascade_surgery', 'policy_waiver_probe', 'nudge_clearance')


class RecordingOperators(RepairOperators):
    """Every operator replaced by a recorder: no pcbnew, no geometry."""

    def __init__(self):
        super().__init__()
        self.calls = []

    def _rec(self, name, failed=0, notes=None):
        self.calls.append(name)
        return RepairResult(operator=name, repaired_count=1, success=True,
                            failed_count=failed, notes=notes or [])

    def copper_text_to_silk(self, board, violations=None):
        return self._rec(TEXT_TO_SILK)

    def short_rip_reroute(self, board, violations=None):
        return self._rec('short_rip_reroute')

    def widen_narrow_tracks(self, board, violations=None):
        return self._rec('widen_narrow_tracks')

    def width_cascade_surgery(self, board, violations):
        # narrows some tracks and reports which nets ended at the floor
        return self._rec('width_cascade_surgery', failed=1,
                         notes=['/N: floor reached'])

    def policy_waiver_probe(self, board, violations, dru_path=None):
        return self._rec('policy_waiver_probe')

    def nudge_clearance(self, board, violations=None):
        return self._rec('nudge_clearance')

    def endpoint_attach(self, board, floating_traces):
        return self._rec('endpoint_attach')

    def connect_unconnected_geomir(self, board, violations=None):
        return self._rec('connect_unconnected')

    def maze_escape(self, board, violations=None):
        return self._rec('maze_escape')


def _violations():
    return [Violation(type='track_width'), Violation(type='unconnected'),
            Violation(type='clearance', net='/N', has_measurement=True),
            Violation(type='shorting_items')]


def test_without_a_policy_everything_runs():
    ops = RecordingOperators()
    ops.dispatch_repair(object(), _violations())
    assert ops.calls[0] == TEXT_TO_SILK
    assert set(CHAIN) <= set(ops.calls)
    assert 'maze_escape' in ops.calls and 'short_rip_reroute' in ops.calls


def test_a_disallowed_operator_is_never_selected():
    # the legacy policy: no maze, no rip-up, no clearance steps
    legacy = {TEXT_TO_SILK, 'widen_narrow_tracks', 'endpoint_attach',
              'connect_unconnected'}
    ops = RecordingOperators()
    results = ops.dispatch_repair(object(), _violations(), allowed=legacy)
    assert ops.calls == [TEXT_TO_SILK, 'widen_narrow_tracks',
                         'connect_unconnected']
    # what WAS allowed still ran and still reported -- nothing is vetoed
    assert {r.operator for r in results} == set(ops.calls)
    skipped = {e['operator'] for e in ops.ledger if e.get('skipped')}
    assert {'maze_escape', 'short_rip_reroute'} <= skipped


def test_clearance_chain_runs_only_its_permitted_steps():
    ops = RecordingOperators()
    ops.dispatch_repair(object(), [Violation(type='clearance', net='/N',
                                             has_measurement=True)],
                        allowed={TEXT_TO_SILK, 'nudge_clearance'})
    assert ops.calls == [TEXT_TO_SILK, 'nudge_clearance']
    skipped = {e['operator'] for e in ops.ledger
               if e.get('skipped') == 'not permitted by policy'}
    assert skipped == {'width_cascade_surgery', 'policy_waiver_probe'}


def test_class_restriction_handles_one_morphology_per_round():
    ops = RecordingOperators()
    ops.dispatch_repair(object(), _violations(), classes={'track_width'})
    assert ops.calls == ['widen_narrow_tracks']


def test_nothing_permitted_means_nothing_runs():
    ops = RecordingOperators()
    assert ops.dispatch_repair(object(), _violations(), allowed=set()) == []
    assert ops.calls == []
