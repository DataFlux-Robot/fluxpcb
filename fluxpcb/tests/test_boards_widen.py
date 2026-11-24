"""Measured widening on real boards.

Each case is an intervention that must flip, with the control that makes
the flip mean something:

  * a board left with N narrow tracks comes back with none, and the
    DRC that judged it is the DRC that clears it;
  * the SAME board with the measurement off keeps its defect — the
    blind widen alone still conflicts (the state before this change);
  * a good board is left untouched;
  * thin one legal track on the good board: one finding appears, the
    operator removes it;
  * a sealed control (a pad pushed onto another net's pad) stays
    flagged: pads are not this operator's to move.
"""
import os
import sys

import pytest

pytest.importorskip('pcbnew')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from fluxpcb.tests import boards_local as F  # noqa: E402
from fluxpcb.repair_drc import no_assert_dialogs, parse_violations  # noqa: E402
from fluxpcb.verifiers.operators import RepairOperators  # noqa: E402
from fluxpcb.verifiers.params import OperatorParams  # noqa: E402

pytestmark = pytest.mark.board

# (fixture, narrow tracks the archived run left)
CASES = [('0400', 9), ('0520', 50), ('0054', 7)]


def _prepare(tmp_path, key):
    pcb = F.board(key, tmp_path)
    errs, unconn = F.drc(pcb)
    return pcb, errs, unconn


def _widen(pcb, errs, **params):
    no_assert_dialogs()
    board = F.load(pcb)
    # by keyword: the first positional argument of RepairOperators is a
    # distance, and passing the params object there silently runs defaults
    ops = RepairOperators(params=OperatorParams(**params))
    res = ops.widen_narrow_tracks(board, parse_violations({'violations': errs}))
    F.save(board, pcb)
    return res, ops


@pytest.mark.parametrize('key,narrow', CASES)
def test_measured_widening_cleans_the_real_board(tmp_path, key, narrow):
    pcb, errs, unconn = _prepare(tmp_path, key)
    assert F.by_type(errs) == {'track_width': narrow} and not unconn
    res, ops = _widen(pcb, errs)
    errs2, unconn2 = F.drc(pcb)
    assert errs2 == [] and unconn2 == []                 # the flip
    cert = res.certificate
    assert cert['widened'] == narrow and cert['refused'] == 0
    assert cert['measured'] is True
    # nothing was refused, so nothing claims a blocker
    assert not [e for e in ops._ledger if e.get('outcome')]


@pytest.mark.parametrize('key,narrow', CASES)
def test_without_the_measurement_the_same_board_still_conflicts(tmp_path, key, narrow):
    """The control: the blind widen alone makes the same board worse."""
    pcb, errs, _unconn = _prepare(tmp_path, key)
    _res, _ops = _widen(pcb, errs, widen_measure=False)
    errs2, _ = F.drc(pcb)
    kinds = F.by_type(errs2)
    assert kinds.get('track_width', 0) == 0          # everything was widened ...
    assert kinds.get('clearance', 0) >= 1            # ... into its neighbours


def test_a_good_board_is_left_alone(tmp_path):
    pcb, errs, unconn = _prepare(tmp_path, '0023')
    assert errs == [] and unconn == []
    res, _ops = _widen(pcb, errs)
    errs2, unconn2 = F.drc(pcb)
    assert errs2 == [] and unconn2 == []
    assert res.certificate['widened'] == 0


def test_thinning_one_track_creates_a_finding_and_the_operator_removes_it(tmp_path):
    pcb, errs, _unconn = _prepare(tmp_path, '0023')
    assert errs == []
    no_assert_dialogs()
    board = F.load(pcb)
    ops = RepairOperators()
    minimum = ops.load_board_rules(board)['min_track_width']
    victim = next(t for t in board.GetTracks()
                  if type(t).__name__ != 'PCB_VIA'
                  and t.GetWidth() >= minimum * 1e6)      # any legal track
    victim.SetWidth(int(round((minimum - 0.05) * 1e6)))
    F.save(board, pcb)
    errs1, _ = F.drc(pcb)
    assert F.by_type(errs1) == {'track_width': 1}      # the defect, made on purpose
    res, _ops = _widen(pcb, errs1)
    errs2, unconn2 = F.drc(pcb)
    assert errs2 == [] and unconn2 == []
    assert res.certificate['widened'] == 1


def test_pcbnew_stays_usable_after_the_operator_took_tracks_off(tmp_path):
    """The re-plan discards tracks the board loaded from its file.  Done
    with BOARD::Remove that corrupted the heap and the next LoadBoard
    came back a raw SwigPyObject — the 0054 cells died on '.Zones'."""
    pcb, errs, _unconn = _prepare(tmp_path, '0054')
    res, _ops = _widen(pcb, errs)
    assert res.certificate['replanned'] == 1          # the path that discards
    board = F.load(pcb)
    assert type(board).__name__ == 'BOARD'
    assert board.Save  and len(list(board.GetTracks())) > 0


def test_a_board_that_already_has_a_clearance_finding(tmp_path):
    """The operator reads the findings the board already had, to tell
    its own conflicts from older ones.  Those arrive as parsed Violation
    objects; treating them as dicts raised, on every board that had a
    clearance finding when its tracks were widened (0571 is one)."""
    pcb, errs, _unconn = _prepare(tmp_path, '0571')
    assert F.by_type(errs) == {'clearance': 1}
    no_assert_dialogs()
    board = F.load(pcb)
    ops = RepairOperators()
    minimum = ops.load_board_rules(board)['min_track_width']
    victim = next(t for t in board.GetTracks()
                  if type(t).__name__ != 'PCB_VIA'
                  and t.GetWidth() >= minimum * 1e6)
    victim.SetWidth(int(round((minimum - 0.05) * 1e6)))
    F.save(board, pcb)
    errs1, _ = F.drc(pcb)
    kinds = F.by_type(errs1)
    assert kinds.get('track_width', 0) >= 1 and kinds.get('clearance') == 1
    res, _ops = _widen(pcb, errs1)                   # must not raise
    errs2, _unconn2 = F.drc(pcb)
    assert F.by_type(errs2).get('track_width', 0) == 0
    # the older finding is not this operator's to remove, and it is not
    # counted as one the widening caused
    assert F.by_type(errs2).get('clearance', 0) >= 1
    assert res.certificate['refused'] == 0


@pytest.mark.parametrize('name', F.control_names())
def test_a_sealed_control_stays_flagged(tmp_path, name):
    pcb = F.control(name, tmp_path)
    errs, _unconn = F.drc(pcb)
    assert F.by_type(errs).get('shorting_items', 0) >= 1
    _res, _ops = _widen(pcb, errs)
    errs2, _unconn2 = F.drc(pcb)
    assert F.by_type(errs2).get('shorting_items', 0) >= 1


def test_the_cheaper_pre_check_sees_vias(tmp_path):
    """The pre-check used to ignore vias.  With a via in the way the
    track must be a SUSPECT (measured), never a silent widen."""
    pcb, errs, _unconn = _prepare(tmp_path, '0054')
    res, _ops = _widen(pcb, errs)
    assert res.certificate['suspects'] >= 1
