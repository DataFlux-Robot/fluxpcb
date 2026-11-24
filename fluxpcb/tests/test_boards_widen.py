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


def _boxed(path):
    """A thin track between two tracks of other nets, 0.41 mm from each.

    At 0.15 mm it clears both by 0.21 mm; at the required 0.2 mm by
    0.185 mm.  Its two neighbours are tracks between pads that reach
    almost to the board's top and bottom edges, so there is no way
    round on this layer; a pad of a fourth net covers the back layer
    under it all, so there is no way round on the other one either.
    The neighbours themselves have to move."""
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
    for name in ('A', 'D', 'E', 'C'):
        n = pcbnew.NETINFO_ITEM(b, name)
        b.Add(n)
        codes[name] = n.GetNetCode()
    for k, (x, y, net) in enumerate(((3.0, 7.0, 'A'), (17.0, 7.0, 'A'),
                                     (4.5, 13.1, 'D'), (15.5, 13.1, 'D'),
                                     (4.5, 0.9, 'E'), (15.5, 0.9, 'E'))):
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

    track((3.0, 7.0), (17.0, 7.0), 'A', 0.15)
    for y_pad, y_run, net in ((13.1, 7.41, 'D'), (0.9, 6.59, 'E')):
        track((4.5, y_pad), (4.5, y_run), net, 0.25)
        track((4.5, y_run), (15.5, y_run), net, 0.25)
        track((15.5, y_run), (15.5, y_pad), net, 0.25)
    # the back layer, as one big pad of a fourth net
    fp = pcbnew.FOOTPRINT(b)
    fp.SetReference('J7')
    fp.SetPosition(V(10.0, 7.0))
    b.Add(fp)
    band = pcbnew.PAD(fp)
    band.SetNumber('1')
    band.SetShape(pcbnew.PAD_SHAPE_RECT)
    band.SetSize(size(18.5, 12.5))
    band.SetPosition(V(10.0, 7.0))
    band.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
    ls = pcbnew.LSET()
    ls.AddLayer(pcbnew.B_Cu)
    band.SetLayerSet(ls)
    band.SetNetCode(codes['C'])
    fp.Add(band)
    pcbnew.SaveBoard(path, b)
    return path


def _boxed_note():
    """Why there is no synthetic test of the hand-off succeeding.

    A thin track boxed in on both layers is easy to draw, but the scene
    also trips KiCad's copper-to-edge rule (the back-layer pad that
    closes the second layer runs 0.725 mm from the board edge), and the
    check that follows the re-plan then refuses the very change the
    test wants.  The hand-off is evidenced on the real board instead:
    0599's SPI_MISO, 40 thin tracks, one refused with one run's DRC and
    none with three (experiment log 6.10, probe_widen1).
    """
    return True


def test_when_only_one_neighbour_may_move_the_thin_track_goes_back_narrow(tmp_path):
    """The control: the same board, the conflict set limited to one
    track.  The re-route is sealed, and the track is really put back at
    its thin width — no clearance miss is left behind for the gate."""
    pcb = _boxed(str(tmp_path / 'sealed.kicad_pcb'))
    errs, _unconn = F.drc(pcb)
    res, _ops = _widen(pcb, errs, conflict_max_set=1)
    errs2, _unconn2 = F.drc(pcb)
    assert F.by_type(errs2) == {'track_width': 1}
    assert res.certificate['refused'] == 1 and res.certificate['replanned'] == 0
