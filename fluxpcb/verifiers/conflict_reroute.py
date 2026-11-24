"""conflict_reroute — a connection that cannot move alone gets a new path.

Second-order congestion (PathFinder's term): the track behind a
clearance finding cannot be nudged because another net sits where it
would have to go.  The nudge and the fine-grid re-plan both keep the
connection on its layer and every other net where it is, so all they
can report is "boxed in".  0571: SD_A passes a battery pad that asks
for 0.5 mm at 0.288 mm, and IN_A runs 0.084 mm behind it.

This operator lifts the offending run of tracks — from anchor to anchor,
so nothing else is detached — and gives it a new path, free to change
layer through vias.  When its own layer is closed, a priced search says
WHICH copper closes it (the conflict set); that copper is lifted too and
routed again afterwards, in several orders.

Per finding the outcome is one of
  clear          the connection alone found a path (its own layer, or
                 another layer through vias at its anchors)
  conflict_set   k foreign tracks were lifted and all k + 1 connections
                 were routed again
  already_clear  the finding no longer exists (an earlier operator moved
                 the track); nothing was touched
  sealed         no path alone, and the conflict set is larger than allowed
  no_path        a set was found but could not all be routed again
  drc_rejected   the DRC did not confirm the change; everything went back

Nothing is kept on the geometric verifier's word alone: one DRC run
judges the whole batch, and a new finding on a net that was touched, or
one more open, puts it all back.
"""
from __future__ import annotations

import itertools
import math
import os

from .certs import DRC_REJECTED, NOT_APPLICABLE, NO_PATH, OK, SEALED

TOL = 0.06          # mm: track ends this close are one junction
VIA_COST = 4.0      # mm: what one via is worth against detour length
RIP_PRICE = 10.0    # mm: what one foreign connection is worth against detour

OUTCOMES = {'clear': OK, 'conflict_set': OK, 'already_clear': NOT_APPLICABLE,
            'sealed': SEALED, 'no_path': NO_PATH, 'drc_rejected': DRC_REJECTED}


def _mm(p):
    return (p.x / 1e6, p.y / 1e6)


def _near(p, q):
    return math.hypot(p[0] - q[0], p[1] - q[1]) <= TOL


def _same(p, q):
    return abs(p[0] - q[0]) <= 0.002 and abs(p[1] - q[1]) <= 0.002


def _pt_seg(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    l2 = dx * dx + dy * dy
    if l2 == 0:
        return math.hypot(p[0] - a[0], p[1] - a[1])
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


def _len(pts):
    return sum(math.dist(p[:2], q[:2]) for p, q in zip(pts, pts[1:]))


def _where(ex):
    """file:line function of the frame that raised — a failure
    certificate says where, not only what."""
    # the last frame that is ours (a SWIG wrapper raising is not where)
    where, tb = None, ex.__traceback__
    while tb is not None:
        name = os.path.basename(tb.tb_frame.f_code.co_filename)
        if name != 'pcbnew.py':
            code = tb.tb_frame.f_code
            where = '%s:%d %s' % (name, tb.tb_lineno, code.co_name)
        tb = tb.tb_next
    return where


def connection(board, t):
    """The run of same-net, same-layer tracks through `t`, anchor to anchor.

    Returns [(track, p_in, p_out), ...] ordered from one anchor to the
    other (p_out of a member is p_in of the next, all in mm), or None
    when another track of the net branches off the middle of a member —
    lifting it would take that branch's ride away.  The anchors are the
    two outer ends: where the run meets copper that stays (a pad, a via,
    a junction) or a free end.

    Two ends within TOL are one junction.  Routers round coordinates: on
    0571 consecutive SD_A tracks end 20 µm apart, which is one
    connection to the DRC and to the netlist both, and an anchor
    nowhere.
    """
    nc, lid = t.GetNetCode(), t.GetLayer()
    tracks = [o for o in board.GetTracks()
              if o is not t and type(o).__name__ != 'PCB_VIA'
              and o.GetNetCode() == nc and o.GetLayer() == lid]
    pads = [p.GetBoundingBox() for fp in board.GetFootprints()
            for p in fp.Pads() if p.GetNetCode() == nc]
    vias = [v for v in board.GetTracks()
            if type(v).__name__ == 'PCB_VIA' and v.GetNetCode() == nc]
    def anchored(pt):
        for bb in pads:
            if (bb.GetLeft() / 1e6 - TOL <= pt[0] <= bb.GetRight() / 1e6 + TOL
                    and bb.GetTop() / 1e6 - TOL <= pt[1] <= bb.GetBottom() / 1e6 + TOL):
                return True
        for v in vias:
            q = v.GetPosition()
            if (abs(q.x / 1e6 - pt[0]) <= TOL
                    and abs(q.y / 1e6 - pt[1]) <= TOL):
                return True
        return False

    def walk(end, used):
        """Follow the run away from `end`, the free end of the last member
        added.  Returns ([members], anchor), each member oriented away
        from `t`, or (None, None) if the run does not end."""
        out, cur = [], end
        for _ in range(400):
            if anchored(cur):
                return out, cur
            joins, bodies = [], []
            for o in tracks:
                if o.m_Uuid.AsString() in used:
                    continue
                s, e = _mm(o.GetStart()), _mm(o.GetEnd())
                if _near(cur, s) or _near(cur, e):
                    joins.append((o, s, e))
                elif _pt_seg(cur, s, e) <= o.GetWidth() / 2e6 + TOL:
                    bodies.append(o)
            if bodies or len(joins) != 1:
                return out, cur      # a junction, or a free end: copper that stays
            o, s, e = joins[0]
            used.add(o.m_Uuid.AsString())
            nxt = e if _near(cur, s) else s
            out.append((o, cur, nxt))
            cur = nxt
        return None, None

    s, e = _mm(t.GetStart()), _mm(t.GetEnd())
    used = {t.m_Uuid.AsString()}
    back, _a = walk(s, used)
    if back is None:
        return None
    fwd, _b = walk(e, used)
    if fwd is None:
        return None
    run = [(o, nxt, cur) for (o, cur, nxt) in reversed(back)]
    run.append((t, s, e))
    run.extend(fwd)
    # a branch that ends in the MIDDLE of a member relies on that member
    for o in tracks:
        if o.m_Uuid.AsString() in used:
            continue
        for p in (_mm(o.GetStart()), _mm(o.GetEnd())):
            for (m, p_in, p_out) in run:
                if (_pt_seg(p, p_in, p_out) <= m.GetWidth() / 2e6 + TOL
                        and not _near(p, p_in) and not _near(p, p_out)):
                    return None
    return run


def lifts(i, n, count):
    """(ka, kb) pairs: how many members to take beyond `t` on each side,
    smallest first, growing both ends in turn (the far side of a boxed
    track is as often the way out as the near one)."""
    out, ka, kb, turn = [], 0, 0, 0
    for _ in range(count):
        out.append((ka, kb))
        grew = False
        for side in (turn, 1 - turn):
            if side == 0 and i + kb + 1 < n:
                kb += 1
                grew = True
                break
            if side == 1 and i - ka > 0:
                ka += 1
                grew = True
                break
        if not grew:
            break
        turn = 1 - turn
    return out


def record(net):
    return {'lifted': [], 'created': [], 'gone': set(), 'net': net}


def lift(board, rec, members):
    """Take the members off the board, their geometry on record.  They
    never come back as themselves (kicadgen/boardops.py)."""
    from ..kicadgen.boardops import discard

    for (tr, _p_in, _p_out) in members:
        s, e = tr.GetStart(), tr.GetEnd()
        rec['lifted'].append((s.x, s.y, e.x, e.y, int(tr.GetWidth()),
                              tr.GetLayer(), tr.GetNetCode(),
                              tr.m_Uuid.AsString()))
        rec['gone'].add(tr.m_Uuid.AsString())
        discard(board, tr)


def put_back(board, rec):
    """The lifted tracks back as new tracks with their original geometry.
    Returns {uuid it had: the new track}: a caller holding the old
    wrappers must swap them for these before it touches them again."""
    import pcbnew

    fresh = {}
    for (sx, sy, ex, ey, w, lid, nc, uid) in rec['lifted']:
        tr = pcbnew.PCB_TRACK(board)
        tr.SetStart(pcbnew.VECTOR2I(sx, sy))
        tr.SetEnd(pcbnew.VECTOR2I(ex, ey))
        tr.SetWidth(w)
        tr.SetLayer(lid)
        tr.SetNetCode(nc)
        board.Add(tr)
        fresh[uid] = tr
    rec['lifted'] = []
    return fresh


def unmake(board, rec):
    """A record's whole change off the board: what it drew out, what it
    lifted back.  Returns put_back's {old uuid: new track}."""
    _remove(board, rec['created'])
    rec['created'] = []
    rec.pop('plan', None)
    return put_back(board, rec)


def _remove(board, items):
    for it in items:
        try:
            board.Remove(it)
        except Exception:
            pass


def candidates(board, layers, a, b, las, lbs, width, nc, rt, bounds, via,
               margin=1.5):
    """Paths for a connection that the verifier finds free, cheapest first.

    las, lbs: the layers the connection may start and end on (a lifted
    track gives its own layer on both sides; a through-hole pad gives
    every layer it is on).  Cost is length plus VIA_COST per via, so a
    detour on one layer is preferred to a hop unless it is 4 mm longer.
    Every candidate starts and ends on its anchors and is judged by
    geomir against the board as it is now (a lifted connection is not on
    it): what comes back can be drawn.
    """
    from ..kicadgen import geomir
    from ..kicadgen.microroute import plan_layers, plan_local

    segs, rects = geomir.extract_objects(board, layers)
    out = _candidates(board, layers, a, b, las, lbs, width, nc, rt, bounds,
                      via, margin, segs, rects)
    if not out:
        # nothing within `margin` of the anchors' box: the way round is
        # further out (0051's open needs 3 mm around a 10 mm connection)
        out = _candidates(board, layers, a, b, las, lbs, width, nc, rt,
                          bounds, via, 2 * margin, segs, rects)
    return out, segs, rects


def _candidates(board, layers, a, b, las, lbs, width, nc, rt, bounds, via,
                margin, segs, rects):
    from ..kicadgen import geomir
    from ..kicadgen.microroute import plan_layers, plan_local

    out = []

    def add(pts, mode):
        pts = [(round(x, 4), round(y, 4), li) for x, y, li in pts]
        pts[0] = (round(a[0], 4), round(a[1], 4), pts[0][2])
        pts[-1] = (round(b[0], 4), round(b[1], 4), pts[-1][2])
        out.append({'pts': pts, 'length_mm': round(_len(pts), 3),
                    'vias': sum(1 for p, q in zip(pts, pts[1:]) if p[2] != q[2]),
                    'mode': mode, 'width': width})

    # heuristic shapes (a candidate may hop to the quiet layer in the
    # middle; its ends have to sit on layers the anchors offer)
    try:
        plans, _ = geomir.plan_paths(
            a, b, width, nc, segs, rects, bounds=bounds,
            width_ladder=(width,), rules=rt, planes_by_layer=None)
    except Exception:
        plans = []
    for pl in plans:
        pts = [(p[0], p[1], int(p[2])) for p in pl.get('pts') or []]
        if len(pts) >= 2 and pts[0][2] in las and pts[-1][2] in lbs:
            # a near-miss plan comes back shifted sideways, ends included:
            # it starts and ends on the anchors or it is an open
            add(pts, 'shapes:%s' % pl.get('mode', ''))
    # one layer, fine grid
    for li in sorted(set(las) & set(lbs)):
        try:
            got = plan_local(a, b, width, nc, segs[li], rects[li], rt,
                             margin=margin, edge=bounds, edge_keep=via['edge'])
        except Exception:
            got = None
        if got:
            add([(x, y, li) for x, y in got['pts']], 'fine-grid')
    # through vias, wherever one is legal: a hop to another layer and
    # back, or the whole connection moved
    try:
        got = plan_layers(a, b, tuple(las), tuple(lbs), width, nc, segs, rects,
                          rt, via_r=via['dia'] / 2.0, via_cost=VIA_COST,
                          margin=margin + 0.5, edge=bounds, edge_keep=via['edge'])
    except Exception:
        got = None
    if got and got['vias']:
        add(got['pts'], 'vias:%d' % got['vias'])
    out = [c for c in out if _free(c, nc, segs, rects, rt, via['dia'] / 2.0)]
    out.sort(key=lambda c: (c['length_mm'] + VIA_COST * c['vias'], c['mode']))
    return out


def _free(cand, nc, segs, rects, rt, via_r):
    """Does every segment and every via of a candidate pass the exact
    verifier as the candidate stands (its ends on the anchors)?"""
    from ..kicadgen import geomir

    for p, q in zip(cand['pts'], cand['pts'][1:]):
        if p[2] != q[2]:
            if not geomir.via_ok(p[0], p[1], nc, segs, rects, via_r=via_r,
                                 rules=rt)[0]:
                return False
        elif (abs(p[0] - q[0]) > 1e-9 or abs(p[1] - q[1]) > 1e-9) and not \
                geomir.verify_polyline([p[:2], q[:2]], cand['width'], nc,
                                       segs[p[2]], rects[p[2]], rt)[0]:
            return False
    return True


def draw(board, cand, width, nc, layers, segs, rects, rt, via):
    """Put one candidate on the board, verifying every piece as it goes.
    Returns the items made, or None (and nothing left behind)."""
    import pcbnew

    from ..kicadgen import geomir

    made, keep = [], False
    try:
        w_iu = int(round(width * 1e6))
        for p, q in zip(cand['pts'], cand['pts'][1:]):
            li, lj = int(p[2]), int(q[2])
            if li != lj:
                if not geomir.via_ok(p[0], p[1], nc, segs, rects,
                                     via_r=via['dia'] / 2.0, rules=rt)[0]:
                    raise ValueError('via not legal')
                v = pcbnew.PCB_VIA(board)
                v.SetPosition(pcbnew.VECTOR2I(int(round(p[0] * 1e6)),
                                              int(round(p[1] * 1e6))))
                v.SetWidth(int(round(via['dia'] * 1e6)))
                v.SetDrill(int(round(via['drill'] * 1e6)))
                v.SetNetCode(nc)
                v.SetLayerPair(layers[0], layers[-1])
                board.Add(v)
                made.append(v)
                continue
            if abs(p[0] - q[0]) < 1e-6 and abs(p[1] - q[1]) < 1e-6:
                continue
            if not geomir.verify_polyline([(p[0], p[1]), (q[0], q[1])], width,
                                          nc, segs[li], rects[li], rt)[0]:
                raise ValueError('segment not legal')
            tr = pcbnew.PCB_TRACK(board)
            tr.SetStart(pcbnew.VECTOR2I(int(round(p[0] * 1e6)),
                                        int(round(p[1] * 1e6))))
            tr.SetEnd(pcbnew.VECTOR2I(int(round(q[0] * 1e6)),
                                      int(round(q[1] * 1e6))))
            tr.SetWidth(w_iu)
            tr.SetLayer(layers[li])
            tr.SetNetCode(nc)
            board.Add(tr)
            made.append(tr)
        keep = bool(made)
    except Exception:
        keep = False
    if not keep:
        _remove(board, made)
        return None
    return made


def draw_first(board, rec, cands, width, nc, layers, rt, via):
    """Draw the first candidate that goes on cleanly; record it."""
    from ..kicadgen import geomir

    segs, rects = geomir.extract_objects(board, layers)
    for c in cands:
        made = draw(board, c, width, nc, layers, segs, rects, rt, via)
        if made is not None:
            rec['created'].extend(made)
            rec['plan'] = {k: c[k] for k in ('mode', 'length_mm', 'vias')}
            return made
    return None


def find_track(board, layers, li, item):
    """The board track behind one entry of the obstacle lists."""
    net, s, e = item[0], item[1], item[2]
    for t in board.GetTracks():
        if (type(t).__name__ == 'PCB_VIA' or t.GetNetCode() != net
                or t.GetLayer() != layers[li]):
            continue
        a, b = _mm(t.GetStart()), _mm(t.GetEnd())
        if ((_same(a, s) and _same(b, e)) or (_same(a, e) and _same(b, s))):
            return t
    return None


def still_conflicts(board, layers, t, rt):
    """Does this track still sit too close to foreign copper on its layer?
    (An earlier operator may already have moved it.)"""
    from ..kicadgen import geomir

    segs, rects = geomir.extract_objects(board, layers)
    li = layers.index(t.GetLayer())
    s, e = _mm(t.GetStart()), _mm(t.GetEnd())
    ok, _ = geomir.verify_polyline([s, e], t.GetWidth() / 1e6,
                                   t.GetNetCode(), segs[li], rects[li], rt)
    return not ok


def judge(board):
    """The board as it is in memory, judged by the real DRC (zones refilled
    first, as the transaction's evaluation does): (errors, opens), or
    None when the DRC could not run."""
    import pcbnew

    from ..repair_drc import run_drc, save_preserving_project

    path = board.GetFileName()
    if not path or not os.path.exists(path):
        return None
    try:
        pcbnew.ZONE_FILLER(board).Fill(board.Zones())
        save_preserving_project(board, path)
    except Exception:
        return None
    d = run_drc(path, path + '.conflict.drc.json')
    if not d.get('ok'):
        return None
    errs = [v for v in d.get('violations') or []
            if v.get('severity') == 'error' and not v.get('excluded')]
    opens = len([u for u in d.get('unconnected_items') or []
                 if not u.get('excluded')])
    return errs, opens


def edge_keepout(board):
    """Clearance copper must keep from the board outline: what the
    project says (0.0 is a value, and the DRC judges by it), else the
    board's design setting.  KiCad's default of 0.5 mm is not assumed:
    0571 asks for none, and its SD_A ends 0.56 mm from the edge — taken
    as a wall, that end could not be reached and the track stayed put."""
    keep = None
    try:
        import json

        pro = os.path.splitext(board.GetFileName())[0] + '.kicad_pro'
        if os.path.exists(pro):
            with open(pro, encoding='utf-8') as fh:
                rules = (((json.load(fh).get('board') or {})
                          .get('design_settings') or {}).get('rules') or {})
            for key in ('copper_edge_clearance', 'min_copper_edge_clearance'):
                if rules.get(key) is not None:
                    keep = max(0.0, float(rules[key]))
                    break
    except Exception:
        keep = None
    if keep is None:
        try:
            keep = max(0.0, board.GetDesignSettings().m_CopperEdgeClearance / 1e6)
        except Exception:
            keep = 0.5
    return keep


def _item(board, uid):
    """The track, via or pad with this uuid."""
    for t in board.GetTracks():
        if t.m_Uuid.AsString() == uid:
            return t
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            if pad.m_Uuid.AsString() == uid:
                return pad
    return None


def _item_layers(item, layers):
    """The router layers an item is copper on (a via: all of them)."""
    kind = type(item).__name__
    if kind == 'PCB_VIA':
        return list(range(len(layers)))
    if kind != 'PAD':                 # a track: the one layer it is on
        return [layers.index(item.GetLayer())]
    return [li for li, lid in enumerate(layers) if item.IsOnLayer(lid)]


def _anchor(item, other):
    """Where a connection to `item` starts: a pad's centre, a via's
    position, the end of a track nearest to `other`."""
    if type(item).__name__ in ('PCB_VIA', 'PAD'):
        return _mm(item.GetPosition())
    s, e = _mm(item.GetStart()), _mm(item.GetEnd())
    return s if math.dist(s, other) <= math.dist(e, other) else e


def still_open(board, v):
    """Does the DRC's missing connection still exist?  (An earlier
    operator may have drawn it.)  KiCad's own connectivity answers."""
    items = [_item(board, str(i.get('uuid') or '')) for i in (v.items or [])]
    items = [x for x in items if x is not None]
    if len(items) != 2:
        return True                    # unknown: treat as open, never skip
    board.BuildConnectivity()
    conn = board.GetConnectivity()
    want = items[1].m_Uuid.AsString()
    for x in conn.GetConnectedItems(items[0], int(items[1].Type())):
        try:
            if x.m_Uuid.AsString() == want:
                return False
        except Exception:
            continue
    return True


def net_table(board):
    """uuid -> netcode for every track, via and pad on the board."""
    table = {t.m_Uuid.AsString(): t.GetNetCode() for t in board.GetTracks()}
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            table[pad.m_Uuid.AsString()] = pad.GetNetCode()
    return table


def blamed(finding, mine, nets, table):
    """Is a finding this operator's doing?  It names copper the operator
    drew, or an item on a net it re-routed."""
    for i in finding.get('items') or []:
        u = str(i.get('uuid') or '')
        if u in mine or table.get(u) in nets:
            return True
    return False


def run(ops, board, violations):
    """The operator.  `ops` is the RepairOperators instance (its params and
    ledger are used); everything else is on the board."""
    from .certs import cert_entry
    from .operators import RepairResult, verifier_rules
    from .via_geom import via_geometry
    from ..kicadgen import routegrid as rg
    from ..kicadgen.microroute import plan_local
    from ..kernel.rules import load_rules

    result = RepairResult(operator='conflict_reroute')
    if not board or not violations:
        return result
    try:
        from pathlib import Path

        layers = rg.signal_layer_ids(board)
        rt = verifier_rules(load_rules(Path(board.GetFileName())),
                            ops.params.pad_clearance_strict)
        floor = float(ops.load_board_rules(board).get('min_track_width') or 0.0)
        dia, drill, _src = via_geometry(board, rt)
    except Exception as e:
        result.notes.append(f'rules unavailable: {e}')
        return result
    # + the half-width of the outline's own line: the edge box is that
    # much larger than the board on every side
    via = {'dia': dia or 0.6, 'drill': drill or 0.3,
           'edge': edge_keepout(board) + 0.03}
    bb = board.GetBoardEdgesBoundingBox()
    bounds = (bb.GetLeft() / 1e6, bb.GetTop() / 1e6,
              bb.GetRight() / 1e6, bb.GetBottom() / 1e6)
    p = ops.params
    by_uuid = {t.m_Uuid.AsString(): t for t in board.GetTracks()}
    before = getattr(ops, '_drc_before', None) or list(violations)
    opens_before = sum(1 for v in before if v.type == 'unconnected')
    gone = set()
    state = {'kept': [], 'ledger': [], 'tried': 0, 'dead': set()}

    def solve(t):
        """One mover.  Returns (record | None, outcome, why_not).  Whatever
        it lifted is back on the board unless a record is returned."""
        nc, net = t.GetNetCode(), t.GetNetname()
        la = layers.index(t.GetLayer())
        width = max(t.GetWidth() / 1e6, floor)
        s, e = _mm(t.GetStart()), _mm(t.GetEnd())
        run_ = connection(board, t) or [(t, s, e)]
        i = next(k for k, m in enumerate(run_) if m[0] is t)
        uids = [m[0].m_Uuid.AsString() for m in run_]
        tried, priced, k = [], {'why': 'not_tried', 'pts': None}, 0
        rec = None
        try:
            for ka, kb in lifts(i, len(run_), p.conflict_lifts):
                members = run_[i - ka:i + kb + 1]
                a, b = members[0][1], members[-1][2]
                rec = record(net)
                rec.update(a=[round(a[0], 3), round(a[1], 3)],
                           b=[round(b[0], 3), round(b[1], 3)],
                           lifted_n=len(members))
                lift(board, rec, members)
                gone.update(rec['gone'])
                state['tried'] += 1
                cands, segs, rects = candidates(board, layers, a, b, (la,),
                                                (la,), width, nc, rt, bounds,
                                                via)
                best = (cands[0]['length_mm'] + VIA_COST * cands[0]['vias']
                        if cands else None)
                done, priced, k = through_sets(
                    rec, a, b, (la,), (la,), width, nc, [la], 2.0, segs, rects,
                    best, tried, {'lift': [ka, kb], 'anchors': [rec['a'], rec['b']],
                                  'candidates': len(cands)})
                if done:
                    rec['outcome'] = 'conflict_set'
                    return rec, 'conflict_set', {'tried': tried}
                if draw_first(board, rec, cands, width, nc, layers, rt, via):
                    rec['outcome'] = 'clear'
                    return rec, 'clear', {'tried': tried}
                # back as new tracks (with new uuids): the next, larger
                # lift must hold those, not the wrappers just deleted
                fresh = unmake(board, rec)
                rec = None
                old = run_
                run_ = [(fresh.get(uids[j], old[j][0]), old[j][1], old[j][2])
                        for j in range(len(old))]
                uids = [m[0].m_Uuid.AsString() for m in run_]
        except Exception:
            if rec is not None:
                unmake(board, rec)
            raise
        sealed = priced['why'] == 'set_too_large'
        return None, ('sealed' if sealed else 'no_path'), {
            'tried': tried, 'conflict_set': k if sealed else None}

    def lift_base(vt, la):
        """Lift one track of the set and remember the run it is part of
        (its wrappers go stale with the lift).  Returns (record, info)."""
        vnc, vnet = vt.GetNetCode(), vt.GetNetname()
        va, vb = _mm(vt.GetStart()), _mm(vt.GetEnd())
        vw = max(vt.GetWidth() / 1e6, floor)
        run_ = connection(board, vt) or [(vt, va, vb)]
        # `vt` is about to be lifted: its wrapper goes stale, so whatever
        # is needed from it is taken now
        info = (vnc, vw, run_,
                next(k for k, m in enumerate(run_) if m[0] is vt),
                vt.m_Uuid.AsString(),
                [m[0].m_Uuid.AsString() for m in run_], vt, vnet)
        vr = record(vnet)
        vr.update(a=[round(va[0], 3), round(va[1], 3)],
                  b=[round(vb[0], 3), round(vb[1], 3)], lifted_n=1)
        lift(board, vr, [(vt, va, vb)])
        gone.update(vr['gone'])
        return vr, info

    def re_route(vr, info, la):
        """A victim — its crossed track already lifted in `vr` — on a new
        path: that track alone, then with more of its run, as for the
        connection that pushed it.  False leaves `vr` as it was."""
        vnc, vw, run_, i, vt_uid, uids, vt, vnet = info
        for ka, kb in lifts(i, len(run_), p.conflict_lifts):
            members = run_[i - ka:i + kb + 1]
            a_, b_ = members[0][1], members[-1][2]
            sub = record(vnet)          # what this step lifts on top
            extra = [m for m in members if m[0] is not vt]
            if extra:
                lift(board, sub, extra)
                gone.update(sub['gone'])
            cands, _, _ = candidates(board, layers, a_, b_, (la,), (la,), vw,
                                     vnc, rt, bounds, via)
            if cands and draw_first(board, sub, cands, vw, vnc, layers, rt, via):
                vr['lifted'] = vr['lifted'] + sub['lifted']
                vr['created'] = sub['created'] + vr['created']
                vr.update(a=[round(a_[0], 3), round(a_[1], 3)],
                          b=[round(b_[0], 3), round(b_[1], 3)],
                          lifted_n=len(members), plan=sub.get('plan'))
                return True
            fresh = unmake(board, sub)
            old = run_
            run_ = [(fresh.get(uids[k], old[k][0]), old[k][1], old[k][2])
                    for k in range(len(old))]
            uids = [vt_uid if m[0] is vt else m[0].m_Uuid.AsString()
                    for m in run_]
        return False

    def through_set(rec, a, b, las, lbs, width, nc, la, crossed):
        """The copper in the way is lifted, this connection routed through,
        and that copper routed again after it, in several orders.  False
        leaves everything as it was and remembers the set as one that
        does not work."""
        key = frozenset((x[0], x[1], x[2]) for x in crossed)
        done, victims = False, []
        try:
            for item in crossed:
                vt = find_track(board, layers, la, item)
                if vt is None:
                    return False
                victims.append(lift_base(vt, la))
            cands, _, _ = candidates(board, layers, a, b, las, lbs, width,
                                     nc, rt, bounds, via)
            if not cands or not draw_first(board, rec, cands, width, nc,
                                           layers, rt, via):
                return False
            for order in list(itertools.permutations(range(len(crossed))))[:p.conflict_trials]:
                ok = True
                for idx in order:
                    if not re_route(*victims[idx], la):
                        ok = False
                        break
                if ok:
                    rec['set'] = [{'net': vr['net'], 'lifted': vr['lifted_n'],
                                   'plan': vr.get('plan')}
                                  for (vr, _info) in victims]
                    done = True
                    return True
                for (vr, _info) in victims:   # this order's copper comes off
                    _remove(board, vr['created'])
                    vr['created'], vr['plan'] = [], None
            return False
        finally:
            if not done:
                for (vr, _info) in victims:
                    unmake(board, vr)
                _remove(board, rec['created'])
                rec['created'] = []
                rec.pop('plan', None)
                state['dead'].add(key)

    def through_sets(rec, a, b, las, lbs, width, nc, start_layers, margin,
                    segs, rects, best, tried, tag):
        """Try conflict sets for a connection, cheapest first, and when one
        cannot be routed again the next one: the set that failed is a
        wall for the next search.  `best` is what the connection costs
        alone (None: it has no path alone); a set is not tried when the
        alone path is the cheaper way.  Returns (done, last search, its
        set size)."""
        avoid, sets, priced, k = [], 0, {'why': 'not_tried', 'pts': None}, 0
        for la in start_layers:
            while sets < p.conflict_sets:
                priced = plan_local(a, b, width, nc, segs[la], rects[la], rt,
                                    margin=margin, edge=bounds,
                                    edge_keep=via['edge'],
                                    cross={'cost': RIP_PRICE,
                                           'max': p.conflict_max_set,
                                           'inside': RIP_PRICE / 2,
                                           'avoid': avoid})
                dead = frozenset((x[0], x[1], x[2]) for x in priced['crossed'])
                k = len(priced['crossed'])
                if priced['why'] != 'crosses':
                    break
                if dead in state['dead']:
                    avoid.extend(dead)      # failed for another finding
                    continue
                tried.append(dict(tag, start_layer=board.GetLayerName(layers[la]),
                                  priced=priced['why'], crossed=k, set=sets,
                                  path_mm=round(_len(priced['pts']), 3)))
                if best is not None and best <= _len(priced['pts']) + RIP_PRICE * k:
                    break                   # alone is the cheaper way
                if through_set(rec, a, b, las, lbs, width, nc, la,
                               priced['crossed']):
                    return True, priced, k
                avoid.extend(dead)          # it failed: the next set
                sets += 1
            if priced['why'] != 'crosses':
                break
        return False, priced, k

    def solve_open(v):
        """A connection the DRC reports missing, with no free path.  The
        same two ways out as for a track: alone (any layers its anchors
        offer), or through the copper that closes it."""
        items = [_item(board, u) for u in [str(i.get('uuid') or '')
                                           for i in (v.items or [])]]
        items = [x for x in items if x is not None]
        if len(items) != 2:
            return None, 'no_path', {'why': 'the open does not name two items'}
        (pa, pb) = items
        try:
            nc = pa.GetNetCode()
            net = pa.GetNetname()
            a = _anchor(pa, _mm(pb.GetPosition()))
            b = _anchor(pb, _mm(pa.GetPosition()))
            las = tuple(_item_layers(pa, layers))
            lbs = tuple(_item_layers(pb, layers))
        except Exception as ex:
            return None, 'no_path', {'error': f'{type(ex).__name__}: {ex}'}
        width = max(float(rt.get('track_width') or 0.0), floor)
        rec = record(net)
        rec.update(a=[round(a[0], 3), round(a[1], 3)],
                   b=[round(b[0], 3), round(b[1], 3)], lifted_n=0)
        state['tried'] += 1
        tried = []
        cands, segs, rects = candidates(board, layers, a, b, las, lbs, width,
                                        nc, rt, bounds, via)
        best = (cands[0]['length_mm'] + VIA_COST * cands[0]['vias']
                if cands else None)
        done, priced, k = through_sets(
            rec, a, b, las, lbs, width, nc, sorted(set(las)), 3.0, segs,
            rects, best, tried, {'candidates': len(cands)})
        if done:
            rec['outcome'] = 'conflict_set'
            return rec, 'conflict_set', {'tried': tried}
        if cands and draw_first(board, rec, cands, width, nc, layers, rt, via):
            rec['outcome'] = 'clear'
            return rec, 'clear', {'tried': tried}
        sealed = priced['why'] == 'set_too_large'
        return None, ('sealed' if sealed else 'no_path'), {
            'tried': tried, 'conflict_set': k if sealed else None}

    for v in violations[:p.conflict_cap]:
        if v.type == 'unconnected':
            try:
                if not still_open(board, v):
                    rec, outcome, why = None, 'already_clear', {}
                else:
                    rec, outcome, why = solve_open(v)
            except Exception as ex:
                rec, outcome, why = None, 'no_path', {
                    'error': f'{type(ex).__name__}: {ex}', 'where': _where(ex)}
            if rec is not None:
                state['kept'].append(rec)
            state['ledger'].append((v, None, rec, outcome, why))
            continue
        if v.type != 'clearance':
            continue
        movers = []
        for u in [v.track_uuid] + [str(i.get('uuid') or '') for i in (v.items or [])]:
            t = by_uuid.get(u)
            # `gone`: lifted by an earlier finding (or a failed attempt on
            # this one) — what is on the board now is a different object
            if (t is not None and type(t).__name__ != 'PCB_VIA'
                    and u not in gone and all(t is not m for m in movers)):
                movers.append(t)
        rec, outcome, why = None, 'no_path', {}
        for t in movers:
            if t.m_Uuid.AsString() in gone:
                continue
            try:
                if not still_conflicts(board, layers, t, rt):
                    outcome, why = 'already_clear', {}
                    break
                rec, outcome, why = solve(t)
            except Exception as ex:
                rec, outcome, why = None, 'no_path', {
                    'error': f'{type(ex).__name__}: {ex}', 'where': _where(ex)}
            if rec is not None:
                break
        if rec is not None:
            state['kept'].append(rec)
        state['ledger'].append((v, t if movers else None, rec, outcome, why))

    kept = state['kept']
    verdict = 'nothing_to_judge'
    if kept:
        judged = judge(board)
        if judged is None:
            verdict = 'drc_unavailable'
        else:
            errs, opens = judged
            mine = {it.m_Uuid.AsString() for r in kept for it in r['created']}
            nets = {r['net'] for r in kept}
            table = net_table(board)
            if any(blamed(x, mine, nets, table) for x in errs):
                verdict = 'new_findings'
            elif opens > opens_before:
                verdict = 'more_opens'
            else:
                verdict = 'ok'
        if verdict != 'ok':
            for r in kept:
                unmake(board, r)
            kept = []

    counts = {'clear': 0, 'conflict_set': 0, 'already_clear': 0,
              'sealed': 0, 'no_path': 0, 'drc_rejected': 0}
    for (v, t, rec, outcome, why) in state['ledger']:
        if rec is not None and not any(rec is k for k in kept):
            outcome = 'drc_rejected'
            why = dict(why, verdict=verdict,
                       lifts=state['tried'])
        counts[outcome] += 1
        pos = v.location or (0.0, 0.0)
        if rec is not None:
            pos = rec['a']
        ops._ledger.append(cert_entry(
            'conflict_reroute',
            {'net': v.net, 'pos': [float(pos[0]), float(pos[1])],
             'type': v.type, 'uuid': v.track_uuid},
            OUTCOMES[outcome],
            why_not={} if outcome in ('clear', 'conflict_set') else why,
            attempt={'lifted': rec['lifted_n'] if rec else 0,
                     'anchors': [rec['a'], rec['b']] if rec else None,
                     'plan': rec.get('plan') if rec else None,
                     'conflict_set': [s['net'] for s in rec.get('set', [])] if rec else []},
            requirement={'clearance_mm': float(rt.get('clearance') or 0.0)}))
    result.success = bool(kept)
    result.repaired_count = len(kept)
    # a finding an earlier operator had already moved is not a failure
    result.failed_count = sum(1 for (_v, _t, _r, o, _w) in state['ledger']
                               if o != 'already_clear') - len(kept)
    result.certificate = dict(
        counts, verdict=verdict,
        records=[{k: r.get(k) for k in ('net', 'a', 'b', 'lifted_n', 'plan', 'set', 'outcome')}
                 for r in kept])
    result.notes.append(
        'clear %d, conflict set %d, already clear %d, sealed %d, no path %d'
        % (counts['clear'], counts['conflict_set'], counts['already_clear'],
           counts['sealed'], counts['no_path'])
        + (', DRC refused %d (%s)' % (counts['drc_rejected'], verdict)
           if counts['drc_rejected'] else ''))
    return result
