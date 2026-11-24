"""One repair round, one operator at a time, on real copper.

The round used to be one transaction.  Here one operator does a real
repair and the next one brings in a violation class the board did not
have: the first keeps its copper, the second gets its own back, and the
board ends better than it started.  As one batch both were thrown away.
"""
import json
import os
import sys

import pytest

pytest.importorskip('pcbnew')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from fluxpcb.tests import boards_local as F  # noqa: E402
from fluxpcb.repair_drc import (repair_stepwise, repair_with_operators,  # noqa: E402
                                run_drc, save_preserving_project)
from fluxpcb.verifiers import operator_spec  # noqa: E402
from fluxpcb.verifiers.operator_spec import OperatorRegistry, OperatorSpec  # noqa: E402
from fluxpcb.verifiers.operators import RepairOperators, RepairResult  # noqa: E402
from fluxpcb.verifiers.txn import claims_from_drc, transactional_repair  # noqa: E402

pytestmark = pytest.mark.board


def _board(path, with_c=True):
    """A, B and C, each two pads and a track.  A's track is thinner than
    the board asks (a width finding); C's runs too close to B's (a
    clearance finding): two problems, for two operators to disagree over."""
    import pcbnew

    b = pcbnew.NewBoard(path)

    def V(x, y):
        return pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y))

    def size(w, h):
        return pcbnew.VECTOR2I(pcbnew.FromMM(w), pcbnew.FromMM(h))

    for a, c in (((0, 0), (20, 0)), ((20, 0), (20, 14)),
                 ((20, 14), (0, 14)), ((0, 14), (0, 0))):
        sh = pcbnew.PCB_SHAPE(b)
        sh.SetShape(pcbnew.SHAPE_T_SEGMENT)
        sh.SetLayer(pcbnew.Edge_Cuts)
        sh.SetStart(V(*a))
        sh.SetEnd(V(*c))
        sh.SetWidth(pcbnew.FromMM(0.05))
        b.Add(sh)
    codes = {}
    for name in ('A', 'B', 'C') if with_c else ('A', 'B'):
        n = pcbnew.NETINFO_ITEM(b, name)
        b.Add(n)
        codes[name] = n.GetNetCode()
    pads = [(2.0, 7.0, 'A'), (18.0, 7.0, 'A'), (2.0, 11.0, 'B'), (18.0, 11.0, 'B')]
    if with_c:
        pads += [(9.0, 12.6, 'C'), (15.0, 12.6, 'C')]
    for k, (x, y, net) in enumerate(pads):
        fp = pcbnew.FOOTPRINT(b)
        fp.SetReference('J%d' % (k + 1))
        fp.SetPosition(V(x, y))
        b.Add(fp)
        p = pcbnew.PAD(fp)
        p.SetNumber('1')
        p.SetShape(pcbnew.PAD_SHAPE_RECT)
        p.SetSize(size(0.6, 0.6))
        p.SetPosition(V(x, y))
        p.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
        ls = pcbnew.LSET()
        ls.AddLayer(pcbnew.F_Cu)
        p.SetLayerSet(ls)
        p.SetNetCode(codes[net])
        fp.Add(p)
    def track(a, c, net, w):
        t = pcbnew.PCB_TRACK(b)
        t.SetStart(V(*a))
        t.SetEnd(V(*c))
        t.SetWidth(pcbnew.FromMM(w))
        t.SetLayer(pcbnew.F_Cu)
        t.SetNetCode(codes[net])
        b.Add(t)

    track((2.0, 7.0), (18.0, 7.0), 'A', 0.15)
    track((2.0, 11.0), (18.0, 11.0), 'B', 0.25)
    if with_c:
        # C dips down to run 0.15 mm from B's track (0.2 mm is the minimum)
        for a, c in (((9.0, 12.6), (9.0, 11.4)), ((9.0, 11.4), (15.0, 11.4)),
                     ((15.0, 11.4), (15.0, 12.6))):
            track(a, c, 'C', 0.25)
    pcbnew.SaveBoard(path, b)
    return path


def _sabotage(self, board, violations=None):
    """An operator that makes things worse in a new way: a track of net B
    laid across net A's track."""
    import pcbnew

    t = pcbnew.PCB_TRACK(board)
    t.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(10.0), pcbnew.FromMM(5.0)))
    t.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(10.0), pcbnew.FromMM(11.0)))
    t.SetWidth(pcbnew.FromMM(0.25))
    t.SetLayer(pcbnew.F_Cu)
    t.SetNetCode(board.FindNet('B').GetNetCode())
    board.Add(t)
    return RepairResult(operator='_sabotage', success=True, repaired_count=1)


@pytest.fixture
def registry(monkeypatch):
    RepairOperators._sabotage = _sabotage
    real = operator_spec.default_registry

    def with_sabotage():
        reg = real()
        reg.register(OperatorSpec(name='_sabotage', method='_sabotage',
                                  classes=('clearance',), priority=28))
        return reg

    monkeypatch.setattr(operator_spec, 'default_registry', with_sabotage)
    monkeypatch.setattr(sys.modules['fluxpcb.verifiers.operators'],
                        'default_registry', with_sabotage, raising=False)
    yield
    del RepairOperators._sabotage


def _evaluate(pcb):
    import pcbnew

    b = pcbnew.LoadBoard(pcb)
    pcbnew.ZONE_FILLER(b).Fill(b.Zones())
    save_preserving_project(b, pcb)
    d = run_drc(pcb, pcb + '.drc.json')
    if not d.get('ok'):
        raise RuntimeError(d.get('error'))
    return claims_from_drc(d)


def test_a_bad_operator_no_longer_throws_away_a_good_one(tmp_path, registry):
    pcb = _board(str(tmp_path / 'step.kicad_pcb'))
    before = _evaluate(pcb)
    assert before.error_types == {'track_width', 'clearance'} and before.total >= 2
    out = repair_stepwise(pcb, pcb + '.drc.json', _evaluate)
    after = _evaluate(pcb)
    steps = [(s['operator'], s['decision']) for s in out['steps']]
    assert steps[:2] == [('widen_narrow_tracks', 'commit'), ('_sabotage', 'rollback')]
    assert 'new violation types' in out['steps'][1]['why']
    assert 'widen_narrow_tracks' in out['operators_run'] and '_sabotage' in out['operators_run']
    # the width repair stayed, the short is gone with its operator
    assert 'track_width' not in after.error_types
    assert 'shorting_items' not in after.error_types
    assert after.total < before.total


def test_as_one_batch_both_are_thrown_away(tmp_path, registry):
    """The state before this change, for contrast: the same two operators
    in one transaction, judged on their sum."""
    pcb = _board(str(tmp_path / 'batch.kicad_pcb'))
    before = _evaluate(pcb)
    rep = transactional_repair(
        pcb, lambda p: repair_with_operators(p, p + '.drc.json'), _evaluate,
        max_rounds=1, initial=before)
    after = _evaluate(pcb)
    assert rep.rolled_back == 1
    # nothing kept.  (The count of clearance findings the DRC reports for
    # these three segments moves between runs of the same file — 2, 3 or
    # 4 — so the classes are compared, not the totals.)
    assert after.error_types == before.error_types == {'track_width', 'clearance'}
    assert 'shorting_items' not in after.error_types


def test_nothing_to_do_is_a_no_op(tmp_path, registry):
    pcb = _board(str(tmp_path / 'clean.kicad_pcb'), with_c=False)
    import pcbnew
    b = pcbnew.LoadBoard(pcb)
    for t in b.GetTracks():
        t.SetWidth(pcbnew.FromMM(0.25))
    pcbnew.SaveBoard(pcb, b)
    assert _evaluate(pcb).total == 0
    out = repair_stepwise(pcb, pcb + '.drc.json', _evaluate)
    assert out['no_op'] and out['operators_run'] == []


def test_a_policy_still_selects_what_may_run(tmp_path, registry):
    pcb = _board(str(tmp_path / 'policy.kicad_pcb'))
    out = repair_stepwise(pcb, pcb + '.drc.json', _evaluate,
                          allowed_ops={'nudge_clearance'})
    assert out['no_op'] and out['operators_run'] == []
    assert _evaluate(pcb).error_types == {'track_width', 'clearance'}
