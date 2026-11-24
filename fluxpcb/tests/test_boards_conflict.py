"""conflict_reroute on real copper.

Each case is an intervention that must flip, with the control that
makes the flip mean something:

  * 0571: the archived run left SD_A 0.288 mm from a battery pad that
    asks for 0.5 mm; the connection comes back on a new path through two
    vias, and the DRC that judged it is the DRC that clears it;
  * the same board with the nudge alone keeps the defect (the state
    before this change), and so does the operator switched off;
  * a small board with the same shape of problem, where the copper in
    the way is another SIGNAL: both move, and both are still connected;
  * the same with that signal boxed in its turn: nothing changes;
  * good boards are left untouched.

An OPEN connection goes through the same operator (0051: the
connector alone leaves it open; through the two tracks in its way it
closes, and both of those are routed again and still connected).

The small boards are built here with pcbnew, so they are in git.
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

SD_A = '/Controller//SD_A'


def _run(pcb, errs, operator='conflict_reroute', _unconnected=(), **params):
    no_assert_dialogs()
    board = F.load(pcb)
    # by keyword: the first positional argument of RepairOperators is a
    # distance, and passing the params object there silently runs defaults
    ops = RepairOperators(params=OperatorParams(**params))
    res = getattr(ops, operator)(board, parse_violations(
        {'violations': errs, 'unconnected_items': list(_unconnected)}))
    F.save(board, pcb)
    return res, ops


def _copper(board, skip=()):
    """Every track and via on the board as comparable geometry."""
    rows = []
    for t in board.GetTracks():
        net = t.GetNetname()
        if net in skip:
            continue
        s = t.GetStart()
        if t.GetClass() == 'PCB_VIA':
            rows.append(('via', net, round(s.x / 1e6, 3), round(s.y / 1e6, 3)))
        else:
            e = t.GetEnd()
            rows.append(('track', net, round(s.x / 1e6, 3), round(s.y / 1e6, 3),
                         round(e.x / 1e6, 3), round(e.y / 1e6, 3),
                         round(t.GetWidth() / 1e6, 3), board.GetLayerName(t.GetLayer())))
    return sorted(rows)


def _outcomes(ops):
    return [(e.get('outcome'), e.get('attempt', {}).get('conflict_set'),
             e.get('why_not', {})) for e in ops._ledger
            if e.get('operator') == 'conflict_reroute']


# ------------------------------------------------------------- the real board
def test_0571_the_boxed_connection_gets_a_new_path(tmp_path):
    pcb, errs, unconn = F.board('0571', tmp_path), None, None
    errs, unconn = F.drc(pcb)
    assert F.by_type(errs) == {'clearance': 1} and not unconn
    before = F.load(pcb)
    others = _copper(before, skip=(SD_A,))
    res, ops = _run(pcb, errs)
    errs2, unconn2 = F.drc(pcb)
    assert not errs2 and not unconn2            # the flip
    assert res.success and res.repaired_count == 1
    (outcome, conflict_set, _why), = _outcomes(ops)
    assert outcome == 'ok' and conflict_set == []
    rec, = res.certificate['records']
    assert rec['outcome'] == 'clear' and rec['plan']['vias'] == 2
    assert rec['net'] == SD_A
    # no other net's copper was touched
    assert _copper(F.load(pcb), skip=(SD_A,)) == others


def test_0571_the_nudge_alone_keeps_the_defect(tmp_path):
    pcb = F.board('0571', tmp_path)
    errs, _unconn = F.drc(pcb)
    res, _ops = _run(pcb, errs, operator='nudge_clearance',
                     nudge_steps_um=(10, 20, 40, 80, 160, 320, 640))
    assert not res.success
    assert F.by_type(F.drc(pcb)[0]) == {'clearance': 1}


def test_0571_switched_off_the_defect_stays(tmp_path):
    pcb = F.board('0571', tmp_path)
    errs, _unconn = F.drc(pcb)
    before = _copper(F.load(pcb))
    res, _ops = _run(pcb, errs, conflict_reroute=False)
    assert not res.success and res.notes == ['disabled by policy']
    assert F.by_type(F.drc(pcb)[0]) == {'clearance': 1}
    assert _copper(F.load(pcb)) == before


def test_a_clean_board_is_left_alone(tmp_path):
    pcb = F.board('0023', tmp_path)
    errs, unconn = F.drc(pcb)
    assert not errs and not unconn
    before = _copper(F.load(pcb))
    res, _ops = _run(pcb, errs)
    assert not res.success and not res.certificate.get('records')
    assert _copper(F.load(pcb)) == before


# ------------------------------------------------------------ a small board
def _scene(path, floor=False, lift_pad=0.0):
    """20 x 14 mm, F.Cu signals.  A runs 0.30 mm under a battery pad
    that asks for 0.5 mm; B runs behind A; net C walls the pad in (and
    fills B.Cu under it), so the only way out is through B.  `floor`
    boxes B in its turn.  `lift_pad` moves the pad up until the board
    is clean."""
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
    for name in ('A', 'B', 'C', 'D'):
        n = pcbnew.NETINFO_ITEM(b, name)
        b.Add(n)
        codes[name] = n.GetNetCode()

    count = [0]

    def pad(x, y, w, h, net, pth=False, local=None):
        count[0] += 1
        fp = pcbnew.FOOTPRINT(b)
        fp.SetReference('J%d' % count[0])
        fp.SetPosition(V(x, y))
        b.Add(fp)
        p = pcbnew.PAD(fp)
        p.SetNumber('1')
        p.SetShape(pcbnew.PAD_SHAPE_RECT)
        p.SetSize(size(w, h))
        p.SetPosition(V(x, y))
        p.SetAttribute(pcbnew.PAD_ATTRIB_PTH if pth else pcbnew.PAD_ATTRIB_SMD)
        p.SetLayerSet(p.PTHMask() if pth else p.SMDMask())
        if pth:
            p.SetDrillSize(size(0.6, 0.6))
        if local is not None:
            p.SetLocalClearance(pcbnew.FromMM(local))
        p.SetNetCode(codes[net])
        fp.Add(p)

    def track(a, c, net, layer=pcbnew.F_Cu, w=0.25):
        t = pcbnew.PCB_TRACK(b)
        t.SetStart(V(*a))
        t.SetEnd(V(*c))
        t.SetWidth(pcbnew.FromMM(w))
        t.SetLayer(layer)
        t.SetNetCode(codes[net])
        b.Add(t)

    def signal(y, net):
        pad(1.0, y, 0.5, 0.2, net)
        pad(19.0, y, 0.5, 0.2, net)
        track((1.0, y), (19.0, y), net)

    signal(6.0, 'A')
    signal(5.45, 'B')
    if floor:
        signal(4.99, 'D')
    top = 10.425 + lift_pad
    pad(10.0, top - 2.0, 1.0, 4.0, 'C', pth=True, local=0.5)
    for layer in (pcbnew.F_Cu, pcbnew.B_Cu):
        track((10.0, top), (10.0, 11.2), 'C', layer)
        track((0.8, 11.2), (19.2, 11.2), 'C', layer)
    # B.Cu is net C from 0.55 to 6.45 mm: no hop to the other layer.  A
    # track's ends are round, so the straight part stops short of where
    # the copper has to stop.
    track((3.75, 3.5), (16.25, 3.5), 'C', pcbnew.B_Cu, w=5.9)
    track((10.0, 6.0), (10.0, top - 2.0), 'C', pcbnew.B_Cu)   # band to pad
    pcbnew.SaveBoard(path, b)
    return path


def test_a_connection_boxed_in_by_a_signal_moves_both(tmp_path):
    pcb = _scene(str(tmp_path / 'boxed.kicad_pcb'))
    errs, unconn = F.drc(pcb)
    assert F.by_type(errs) == {'clearance': 1} and not unconn
    res, ops = _run(pcb, errs)
    errs2, unconn2 = F.drc(pcb)
    assert not errs2 and not unconn2            # the flip
    assert res.success
    (outcome, conflict_set, _why), = _outcomes(ops)
    assert outcome == 'ok' and conflict_set == ['B']
    rec, = res.certificate['records']
    assert rec['outcome'] == 'conflict_set' and rec['net'] == 'A'
    # B moved and is still one run from pad to pad
    board = F.load(pcb)
    moved = [r for r in _copper(board) if r[1] == 'B' and r[0] == 'track']
    assert len(moved) >= 2                      # a run, pad to pad
    ends = {(r[2], r[3]) for r in moved} | {(r[4], r[5]) for r in moved}
    assert ends >= {(1.0, 5.45), (19.0, 5.45)}
    assert min(r[3] for r in moved) <= 5.35     # it went down, under A


def test_the_small_board_nudge_alone_keeps_the_defect(tmp_path):
    pcb = _scene(str(tmp_path / 'nudge.kicad_pcb'))
    errs, _unconn = F.drc(pcb)
    res, _ops = _run(pcb, errs, operator='nudge_clearance',
                     nudge_steps_um=(10, 20, 40, 80, 160, 320, 640))
    assert not res.success
    assert F.by_type(F.drc(pcb)[0]) == {'clearance': 1}


def test_when_the_set_is_boxed_in_too_nothing_changes(tmp_path):
    pcb = _scene(str(tmp_path / 'floor.kicad_pcb'), floor=True)
    errs, unconn = F.drc(pcb)
    assert F.by_type(errs) == {'clearance': 1} and not unconn
    before = _copper(F.load(pcb))
    res, ops = _run(pcb, errs)
    (outcome, _set, why), = _outcomes(ops)
    assert outcome == 'no_path' and not res.success
    assert _copper(F.load(pcb)) == before
    errs2, unconn2 = F.drc(pcb)
    assert F.by_type(errs2) == {'clearance': 1} and not unconn2


def test_the_small_board_without_the_defect_is_left_alone(tmp_path):
    pcb = _scene(str(tmp_path / 'clean.kicad_pcb'), lift_pad=0.4)
    errs, unconn = F.drc(pcb)
    assert not errs and not unconn
    before = _copper(F.load(pcb))
    res, _ops = _run(pcb, errs)
    assert not res.success
    assert _copper(F.load(pcb)) == before
    assert not F.drc(pcb)[0]


# ------------------------------------------------------- open connections
def test_0051_the_open_goes_through_the_copper_in_its_way(tmp_path):
    pcb = F.board('0051', tmp_path)
    errs, unconn = F.drc(pcb)
    assert not errs and len(unconn) == 1
    others = sorted(_copper(F.load(pcb)))
    res, ops = _run(pcb, errs, _unconnected=unconn)
    errs2, unconn2 = F.drc(pcb)
    assert not errs2 and not unconn2            # the flip
    (outcome, conflict_set, _why), = _outcomes(ops)
    assert outcome == 'ok' and sorted(conflict_set) == ['+3V3', 'CE']
    rec, = res.certificate['records']
    assert rec['net'] == 'CSN' and rec['outcome'] == 'conflict_set'
    # the tracks that moved belong to the connection and to the two nets
    # named in the set, to no other net
    after = _copper(F.load(pcb))
    moved = {r[1] for r in after} ^ {r[1] for r in others}
    assert moved <= {'+3V3', 'CE', 'CSN'}, moved
    keep = lambda rows: [r for r in rows  # noqa: E731
                         if r[1] not in ('+3V3', 'CE', 'CSN')]
    assert keep(after) == keep(others)


def test_0051_the_connector_alone_leaves_it_open(tmp_path):
    """The state before this change: the archived run's own operators on
    the archived board."""
    pcb = F.board('0051', tmp_path)
    errs, unconn = F.drc(pcb)
    no_assert_dialogs()
    board = F.load(pcb)
    ops = RepairOperators()
    vs = parse_violations({'violations': errs, 'unconnected_items': unconn})
    ops.connect_unconnected_geomir(board, vs)
    ops.maze_escape(board, vs)
    F.save(board, pcb)
    errs2, unconn2 = F.drc(pcb)
    assert not errs2 and len(unconn2) == 1


def test_an_open_that_is_already_closed_is_left_alone(tmp_path):
    pcb = F.board('0051', tmp_path)
    errs, unconn = F.drc(pcb)
    res, _ops = _run(pcb, errs, _unconnected=unconn)
    assert res.success
    board = F.load(pcb)
    before = _copper(board)
    ops2 = RepairOperators()
    again = ops2.conflict_reroute(board, parse_violations(
        {'violations': errs, 'unconnected_items': unconn}))
    assert not again.success and not again.certificate.get('records')
    assert [o[0] for o in _outcomes(ops2)] == ['not_applicable']
    assert _copper(F.load(pcb)) == before


def _open_scene(path, band=True):
    """Net A has two pads and no track; net B is a wall across the board
    between them; with `band` the back layer is net C, so the only way
    from one A pad to the other is through B."""
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
    for name in ('A', 'B', 'C'):
        n = pcbnew.NETINFO_ITEM(b, name)
        b.Add(n)
        codes[name] = n.GetNetCode()
    count = [0]

    def pad(x, y, net, layer=pcbnew.F_Cu, w=0.5, h=0.5):
        count[0] += 1
        fp = pcbnew.FOOTPRINT(b)
        fp.SetReference('J%d' % count[0])
        fp.SetPosition(V(x, y))
        b.Add(fp)
        p = pcbnew.PAD(fp)
        p.SetNumber('1')
        p.SetShape(pcbnew.PAD_SHAPE_RECT)
        p.SetSize(size(w, h))
        p.SetPosition(V(x, y))
        p.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
        ls = pcbnew.LSET()
        ls.AddLayer(layer)                    # SMD on the layer given
        p.SetLayerSet(ls)
        p.SetNetCode(codes[net])
        fp.Add(p)

    def track(a, c, net, layer=pcbnew.F_Cu, w=0.25):
        t = pcbnew.PCB_TRACK(b)
        t.SetStart(V(*a))
        t.SetEnd(V(*c))
        t.SetWidth(pcbnew.FromMM(w))
        t.SetLayer(layer)
        t.SetNetCode(codes[net])
        b.Add(t)

    pad(2.0, 7.0, 'A')
    pad(18.0, 7.0, 'A')
    # the wall reaches to where a track can no longer pass its end
    pad(10.0, 0.85, 'B')
    pad(10.0, 13.15, 'B')
    track((10.0, 0.85), (10.0, 13.15), 'B')
    if band:                                  # one big SMD pad: a
        pad(10.0, 7.0, 'C', pcbnew.B_Cu, 18.5, 12.5)   # track's ends are round
    pcbnew.SaveBoard(path, b)
    return path


def test_an_open_boxed_by_a_signal_goes_through_and_the_signal_moves(tmp_path):
    pcb = _open_scene(str(tmp_path / 'open.kicad_pcb'))
    errs, unconn = F.drc(pcb)
    assert not errs and len(unconn) == 1
    res, ops = _run(pcb, errs, _unconnected=unconn)
    errs2, unconn2 = F.drc(pcb)
    assert not errs2 and not unconn2            # the flip
    (outcome, conflict_set, _why), = _outcomes(ops)
    assert outcome == 'ok' and conflict_set == ['B']
    rec, = res.certificate['records']
    assert rec['outcome'] == 'conflict_set' and rec['plan']['vias'] == 0
    assert not [r for r in _copper(F.load(pcb)) if r[0] == 'via']


def test_an_open_on_a_free_back_layer_takes_two_vias(tmp_path):
    pcb = _open_scene(str(tmp_path / 'hop.kicad_pcb'), band=False)
    errs, unconn = F.drc(pcb)
    assert not errs and len(unconn) == 1
    res, ops = _run(pcb, errs, _unconnected=unconn)
    errs2, unconn2 = F.drc(pcb)
    assert not errs2 and not unconn2
    (outcome, conflict_set, _why), = _outcomes(ops)
    assert outcome == 'ok' and conflict_set == []
    rec, = res.certificate['records']
    assert rec['outcome'] == 'clear' and rec['plan']['vias'] == 2
    assert len([r for r in _copper(F.load(pcb)) if r[0] == 'via']) == 2
