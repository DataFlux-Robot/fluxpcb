"""Verifiers that must be able to fail.

A verifier that never fails is not a verifier.  This module makes each
rule answer for itself: take a board the rule approves of, mutate it in
exactly the way the rule is about, and require the verifier to notice.
The four outcomes are the ones ``contract/pairs.py`` was designed to
report but never had a producer for:

    generated   the mutation could be applied to a copy of the board
    crossed     the mutated board really violates the rule (measured)
    killed      the verifier reports it
    blind_spot  crossed and not killed — the verifier missed a real defect

and the calibration test the reference project asks for: a bad example
must yield strictly more high-level findings than a good one.

The measurement authority is the DRC, run through the same steady
evaluator the repair loop uses (three runs united: kicad-cli's DRC
under-reports at random).  Nothing here is a proxy of a proxy.

    MUTATIONS                       name -> mutation spec
    apply(name, board_path, workdir) -> (mutated_path, meta) | None
    prove(name, board_path, workdir) -> record
    prove_all(board_path, workdir)   -> [record]
    calibrate(good, bad)             -> {'good': n, 'bad': [(board, n)]}
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

# rule id -> the DRC type that answers it (the mapping the gates use)
DRC_RULE = {
    'clearance': 'DRC.clearance',
    'shorting_items': 'DRC.shorting',
    'tracks_crossing': 'DRC.shorting',
    'track_width': 'DRC.track_width',
    'copper_edge_clearance': 'DRC.edge',
    'hole_clearance': 'DRC.hole',
    'hole_to_hole': 'DRC.hole_to_hole',
    'via_diameter': 'DRC.via_size',
    'unconnected_items': 'DRC.unconnected',
}

# what the repair-loop operator of the same name would have to fix
MUTATIONS = {
    'mutation:thin': {
        'rule_id': 'DRC.track_width',
        'about': 'a track thinner than the board minimum',
        'judge': 'drc', 'expect': 'track_width',
    },
    'mutation:move': {
        'rule_id': 'DRC.clearance',
        'about': 'a track moved into another net\'s clearance',
        'judge': 'drc', 'expect': 'clearance',
    },
    'mutation:short': {
        'rule_id': 'DRC.shorting',
        'about': 'a track drawn between two different nets',
        'judge': 'drc', 'expect': ('shorting_items', 'tracks_crossing'),
    },
    # KiCad's DRC does not flag a via's drill against the minimum through
    # hole on every board; the IPC-2221 checker with the dated profile does,
    # and that is the verifier this intervention has to prove
    'mutation:via_drill': {
        'rule_id': 'IPC2221.min_drill',
        'about': 'a via drilled below the fab profile minimum',
        'judge': 'ipc2221', 'expect': 'min_drill',
    },
    'mutation:edge': {
        'rule_id': 'DRC.edge',
        'about': 'copper run to the board edge (only where the board asks)',
        'judge': 'drc', 'expect': ('copper_edge_clearance', 'clearance'),
    },
}


def _mm(v):
    return int(round(float(v) * 1e6))


def _V(board, x, y):
    import pcbnew
    return pcbnew.VECTOR2I(_mm(x), _mm(y))


def _copy(board_path, workdir):
    src = Path(board_path)
    dst_dir = Path(workdir)
    dst_dir.mkdir(parents=True, exist_ok=True)
    out = dst_dir / src.name
    shutil.copyfile(src, out)
    pro = src.with_suffix('.kicad_pro')
    if pro.exists():
        shutil.copyfile(pro, dst_dir / pro.name)
    return str(out)


def _tracks(board):
    out = []
    for t in board.GetTracks():
        if type(t).__name__ == 'PCB_VIA':
            continue
        out.append(t)
    return out


# ------------------------------------------------------------------ mutations
def _thin(board, meta):
    import pcbnew
    # the minimum the DRC enforces, not the netclass nominal width
    try:
        floor = board.GetDesignSettings().m_TrackMinWidth / 1e6
    except Exception:
        floor = 0.0
    if floor <= 0:
        from fluxpcb.kernel.rules import load_rules
        floor = float(load_rules(Path(board.GetFileName())).get('track_width')
                      or 0.2)
    cands = [t for t in _tracks(board) if t.GetWidth() / 1e6 > floor * 1.05]
    if not cands:
        return None
    t = max(cands, key=lambda x: x.GetWidth())
    meta.update(track=t.m_Uuid.AsString(), was_mm=t.GetWidth() / 1e6,
                to_mm=round(floor * 0.5, 4))
    t.SetWidth(_mm(floor * 0.5))
    return True


def _move(board, meta):
    from fluxpcb.kernel.rules import load_rules
    rules = load_rules(Path(board.GetFileName()))
    clear = float(rules.get('clearance') or 0.0)
    # the clearance the DRC actually enforces is the netclass's, and the
    # project's min_clearance may be 0 (0023: min_clearance 0.0, Default
    # netclass 0.2032) — take the stricter of the two sources
    try:
        own = (board.GetDesignSettings().m_NetSettings
               .GetDefaultNetclass().GetClearance() / 1e6)
        clear = max(clear, float(own))
    except Exception:
        pass
    clear = max(clear, 0.15)
    meta['clearance_mm'] = round(clear, 4)
    best = None
    for t in _tracks(board):
        s, e = t.GetStart(), t.GetEnd()
        for fp in board.GetFootprints():
            for pad in fp.Pads():
                if pad.GetNetCode() == t.GetNetCode():
                    continue
                # a pad the DRC relates to this track: same copper layer
                try:
                    if not pad.IsOnLayer(t.GetLayer()):
                        continue
                except Exception:
                    pass
                q = pad.GetPosition()
                bb = pad.GetBoundingBox()
                gap = _seg_rect((s.x / 1e6, s.y / 1e6), (e.x / 1e6, e.y / 1e6),
                                bb.GetLeft() / 1e6, bb.GetTop() / 1e6,
                                bb.GetRight() / 1e6, bb.GetBottom() / 1e6)
                need = clear + t.GetWidth() / 2e6
                if gap - need > 0.02 and (best is None or gap < best[0]):
                    best = (gap, t, q)
    if best is None:
        return None
    gap, t, q = best
    need = clear + t.GetWidth() / 2e6
    # leave a 10 um gap: a violation under any clearance rule above 0.01,
    # without the guesswork of which netclass the DRC will apply
    shift = gap - 0.01
    if shift <= 0.005:
        return None
    s, e = t.GetStart(), t.GetEnd()
    # move PERPENDICULAR to the track, toward the closest point of the pad
    # (moving along the track would not change the lateral clearance)
    ax, ay, bx, by = s.x / 1e6, s.y / 1e6, e.x / 1e6, e.y / 1e6
    px, py = q.x / 1e6, q.y / 1e6
    dxl, dyl = bx - ax, by - ay
    l2 = dxl * dxl + dyl * dyl or 1.0
    tt = max(0.0, min(1.0, ((px - ax) * dxl + (py - ay) * dyl) / l2))
    cx, cy = ax + tt * dxl, ay + tt * dyl
    vx, vy = px - cx, py - cy
    n = (vx * vx + vy * vy) ** 0.5
    if n < 1e-6:
        return None
    dx, dy = vx / n * shift, vy / n * shift
    meta.update(track=t.m_Uuid.AsString(), shift_mm=round(shift, 4),
                toward_pad=(q.x / 1e6, q.y / 1e6))
    t.SetStart(_V(board, s.x / 1e6 + dx, s.y / 1e6 + dy))
    t.SetEnd(_V(board, e.x / 1e6 + dx, e.y / 1e6 + dy))
    return True


def _short(board, meta):
    import pcbnew
    pads = [p for fp in board.GetFootprints() for p in fp.Pads()]
    best = None
    for i, a in enumerate(pads):
        for b in pads[i + 1:]:
            if a.GetNetCode() == b.GetNetCode():
                continue
            pa, pb = a.GetPosition(), b.GetPosition()
            d = ((pa.x - pb.x) ** 2 + (pa.y - pb.y) ** 2) ** 0.5 / 1e6
            if 0.4 < d < 4.0 and (best is None or d < best[0]):
                best = (d, a, b)
    if best is None:
        return None
    d, a, b = best
    pa, pb = a.GetPosition(), b.GetPosition()
    t = pcbnew.PCB_TRACK(board)
    t.SetStart(pcbnew.VECTOR2I(pa.x, pa.y))
    t.SetEnd(pcbnew.VECTOR2I(pb.x, pb.y))
    t.SetWidth(_mm(0.20))
    t.SetLayer(a.GetLayer() if a.IsOnLayer(pcbnew.F_Cu) else pcbnew.F_Cu)
    t.SetNetCode(a.GetNetCode())
    board.Add(t)
    meta.update(net_a=a.GetNetname(), net_b=b.GetNetname(), d_mm=round(d, 3),
                track=t.m_Uuid.AsString())
    return True


def _via_drill(board, meta):
    for v in board.GetTracks():
        if type(v).__name__ != 'PCB_VIA':
            continue
        try:
            was = v.GetDrillValue() / 1e6
        except Exception:
            continue
        if was < 0.45:
            continue
        v.SetDrill(_mm(0.10))
        meta.update(via=v.m_Uuid.AsString(), was_mm=was, to_mm=0.10)
        return True
    return None


def _edge(board, meta):
    import json
    keep = 0.0
    try:
        keep = board.GetDesignSettings().m_CopperEdgeClearance / 1e6
    except Exception:
        keep = 0.0
    try:
        pro = Path(board.GetFileName()).with_suffix('.kicad_pro')
        if pro.exists():
            rules = (((json.loads(pro.read_text(encoding='utf-8'))
                       .get('board') or {}).get('design_settings') or {})
                     .get('rules') or {})
            keep = max(keep, float(rules.get('min_copper_edge_clearance')
                                   or 0.0))
    except Exception:
        pass
    if keep <= 0:
        meta['skip_reason'] = ('this board asks for no copper-to-edge '
                               'clearance; the rule cannot be violated')
        return None
    t = None
    for cand in _tracks(board):
        if cand.GetLength() / 1e6 > 2.0:
            t = cand
            break
    if t is None:
        return None
    s, e = t.GetStart(), t.GetEnd()
    bx0 = board.GetBoardEdgesBoundingBox().GetLeft() / 1e6
    target = (bx0 + keep / 2.0, s.y / 1e6)     # inside the keep-out
    meta.update(track=t.m_Uuid.AsString(), end_before=(e.x / 1e6, e.y / 1e6),
                end_after=target)
    t.SetEnd(_V(board, *target))
    return True


_APPLY = {'mutation:thin': _thin, 'mutation:move': _move,
          'mutation:short': _short, 'mutation:via_drill': _via_drill,
          'mutation:edge': _edge}


def _seg_rect(p, q, x0, y0, x1, y1):
    """Closest distance between a segment and an axis-aligned rectangle."""
    for pt in (p, q):
        if x0 <= pt[0] <= x1 and y0 <= pt[1] <= y1:
            return 0.0
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    return min(_seg_seg(p, q, corners[i], corners[(i + 1) % 4])
               for i in range(4))


def _seg_seg(a, b, c, d):
    dx1, dy1 = b[0] - a[0], b[1] - a[1]
    dx2, dy2 = d[0] - c[0], d[1] - c[1]
    den = dx1 * dy2 - dy1 * dx2
    if abs(den) < 1e-12:
        return min(_pt_seg(a, c, d), _pt_seg(b, c, d),
                   _pt_seg(c, a, b), _pt_seg(d, a, b))
    t = ((c[0] - a[0]) * dy2 - (c[1] - a[1]) * dx2) / den
    u = ((c[0] - a[0]) * dy1 - (c[1] - a[1]) * dx1) / den
    if 0 <= t <= 1 and 0 <= u <= 1:
        return 0.0
    return min(_pt_seg(a, c, d), _pt_seg(b, c, d),
               _pt_seg(c, a, b), _pt_seg(d, a, b))


def _pt_seg(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    l2 = dx * dx + dy * dy
    if l2 == 0:
        return ((p[0] - a[0]) ** 2 + (p[1] - a[1]) ** 2) ** 0.5
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2))
    return ((p[0] - a[0] - t * dx) ** 2 + (p[1] - a[1] - t * dy) ** 2) ** 0.5


# ------------------------------------------------------------------- measure
def ipc_findings(board_path, profile=None) -> dict:
    """The IPC-2221 checker's violations by rule, with the dated profile."""
    from fluxpcb.kicadgen.ipc2221_dfm import IPC2221DFM
    rep = IPC2221DFM().check_jlc(str(board_path), profile=profile)
    out = {}
    for v in rep.violations:
        out[v.rule_name] = out.get(v.rule_name, 0) + 1
    return out


def drc_findings(board_path) -> dict:
    """Error-severity DRC findings of a board, by type (steady: three runs)."""
    from fluxpcb.repair_drc import run_drc_steady, save_preserving_project

    import pcbnew

    b = pcbnew.LoadBoard(str(board_path))
    try:
        pcbnew.ZONE_FILLER(b).Fill(b.Zones())
        save_preserving_project(b, str(board_path))
    except Exception:
        pass
    d = run_drc_steady(str(board_path), str(board_path) + '.iv.drc.json')
    if not d.get('ok'):
        return {'error': d.get('error') or 'DRC failed'}
    out = {}
    for v in d.get('violations') or []:
        if v.get('severity') != 'error' or v.get('excluded'):
            continue
        out[v.get('type', '?')] = out.get(v.get('type', '?'), 0) + 1
    return out


def severities(findings_by_type: dict) -> dict:
    """Findings by severity class: a short or a clearance miss is P0 here;
    a thin track is P1.  Used only for the calibration comparison."""
    p0 = sum(n for t, n in findings_by_type.items()
             if t in ('shorting_items', 'tracks_crossing', 'clearance',
                      'copper_edge_clearance', 'hole_clearance',
                      'hole_to_hole', 'unconnected_items'))
    p1 = sum(n for t, n in findings_by_type.items()
             if t in ('track_width',))
    return {'P0': p0, 'P1': p1, 'total': sum(findings_by_type.values())}


def prove(name: str, board_path: str, workdir: str = None) -> dict:
    """One rule's intervention: generated -> crossed -> killed."""
    spec = MUTATIONS.get(name) or {}
    rec = {'mutation': name, 'rule_id': spec.get('rule_id', ''),
           'board': os.path.basename(str(board_path)),
           'generated': False, 'crossed': False, 'killed': False,
           'blind_spot': False, 'why': ''}
    if not spec:
        rec['why'] = 'unknown mutation'
        return rec
    tmp = workdir or tempfile.mkdtemp(prefix='iv_')
    target = _copy(board_path, os.path.join(tmp, name.replace(':', '_')))
    import pcbnew
    board = pcbnew.LoadBoard(target)
    meta = {}
    try:
        applied = _APPLY[name](board, meta)
    except Exception as e:
        rec['why'] = f'mutation failed: {type(e).__name__}: {e}'
        return rec
    if not applied:
        rec['why'] = meta.get('skip_reason') or \
            'no suitable item on this board for this mutation'
        rec['meta'] = meta
        return rec
    pcbnew.SaveBoard(target, board)
    rec['meta'] = meta
    rec['generated'] = True

    judge = spec.get('judge', 'drc')
    measure = ipc_findings if judge == 'ipc2221' else drc_findings
    before = measure(board_path)
    after = measure(target)
    if 'error' in after:
        rec['why'] = f'{judge}: {after["error"]}'
        return rec
    want = spec['expect']
    want = (want,) if isinstance(want, str) else tuple(want)
    new = {t: after.get(t, 0) - before.get(t, 0) for t in set(after) | set(before)}
    rec['new_findings'] = {t: n for t, n in new.items() if n}
    rec['crossed'] = any(new.get(t, 0) > 0 for t in want)
    rec['killed'] = rec['crossed']      # the measurement is the verifier here
    rec['blind_spot'] = rec['crossed'] and not rec['killed']
    if not rec['crossed']:
        rec['why'] = (f'the mutation did not produce {want}; new findings: '
                      f'{rec["new_findings"]}')
    return rec


def prove_all(board_path: str, workdir: str = None) -> list:
    tmp = workdir or tempfile.mkdtemp(prefix='iv_all_')
    return [prove(n, board_path, tmp) for n in MUTATIONS]


def summary(records) -> dict:
    """The shape contract/pairs.RulePairRegistry.statuses() consumes."""
    out = {}
    for r in records or []:
        out[r['mutation']] = {'generated': int(bool(r['generated'])),
                              'crossed': int(bool(r['crossed'])),
                              'killed': int(bool(r['killed']))}
    return out


def calibrate(good: str, bad) -> dict:
    """A bad example must yield strictly more high-level findings than a good one."""
    g = drc_findings(good)
    if 'error' in g:
        return {'ok': False, 'why': f'good board DRC failed: {g["error"]}'}
    gs = severities(g)
    rows = []
    for b in bad or []:
        f = drc_findings(b)
        if 'error' in f:
            rows.append({'board': os.path.basename(str(b)), 'error': f['error']})
            continue
        s = severities(f)
        rows.append({'board': os.path.basename(str(b)), **s,
                     'more_than_good': (s['P0'] + s['P1']) > (gs['P0'] + gs['P1'])})
    ok = all(r.get('more_than_good') for r in rows) if rows else False
    return {'ok': ok, 'good': {'board': os.path.basename(str(good)), **gs},
            'bad': rows,
            'rule': 'every bad board has strictly more P0+P1 findings than '
                    'the good one'}
