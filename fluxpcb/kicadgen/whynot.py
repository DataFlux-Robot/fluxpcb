"""whynot — quantitative answers to "why did this fail".

Every repair attempt that fails should leave numbers, not a count.  This
module computes those numbers from the same geometry the verifier uses
(edge-to-edge distances, half-extents in mm), so the answer a meta loop
reads is the answer the router acted on.

Conventions
  segs  : [(net, (x1,y1), (x2,y2), half_w)]           (one layer)
  rects : [(net, (cx,cy,hw,hh,ang[,is_round]))]       (one layer)
  all distances in mm; a "margin" is d - needed, negative = violation.
"""
from __future__ import annotations

import math


def _track_needed(clearance, obs_hw, own_hw):
    return clearance + obs_hw + own_hw


def _pad_needed(clearance, own_hw):
    return clearance + own_hw


def seg_clearance_profile(p, q, width, nc, segs, rects,
                          clearance, pad_clearance=None):
    """Min clearance margin of segment p->q at `width` against one layer.

    Returns the binding obstacle, the minimum margin, and the widest track
    that would still fit (`w_max_mm`).  Pad distance is sampled like the
    verifier does (every 0.1 mm), so the answer matches what was judged.
    """
    from . import routegrid
    seg_seg_dist = routegrid.seg_seg_dist

    pc = pad_clearance if pad_clearance is not None else clearance
    own = width / 2.0
    rows = []

    for (net, s, e, hw) in segs:
        if net == nc:
            continue
        d = seg_seg_dist(p, q, s, e)
        needed = _track_needed(clearance, hw, own)
        rows.append((d - needed, d, needed, 'track', net, s, e))

    for (net, rect) in rects:
        if net == nc:
            continue
        n = max(2, int(math.hypot(q[0] - p[0], q[1] - p[1]) / 0.1))
        best = float('inf')
        for k in range(n + 1):
            t = k / n
            x = p[0] + (q[0] - p[0]) * t
            y = p[1] + (q[1] - p[1]) * t
            best = min(best, _pt_pad_dist(x, y, rect))
        needed = _pad_needed(pc, own)
        rows.append((best - needed, best, needed, 'pad', net,
                     (rect[0], rect[1]), rect[2]))

    return _finish(rows, width)


def via_profile(x, y, nc, segs, rects, via_r, clearance):
    """Min clearance margin of a through-via at (x,y), per layer.

    `segs`/`rects` are the per-layer lists (like geomir.via_ok gets); a
    through-via must clear every layer, so the binding layer is reported.
    """
    from . import routegrid
    pt_seg_dist = routegrid.pt_seg_dist

    out = []
    for li, (segs_li, rects_li) in enumerate(zip(segs, rects)):
        rows = []
        for (net, s, e, hw) in segs_li:
            if net == nc:
                continue
            d = pt_seg_dist(x, y, s[0], s[1], e[0], e[1])
            needed = _track_needed(clearance, hw, via_r)
            rows.append((d - needed, d, needed, 'track', net, s, e))
        for (net, rect) in rects_li:
            if net == nc:
                continue
            d = _pt_pad_dist(x, y, rect)
            needed = clearance + via_r
            rows.append((d - needed, d, needed, 'pad', net,
                         (rect[0], rect[1]), rect[2]))
        if rows:
            m, d, needed, kind, net, at, hw = min(rows)
            out.append({'layer': li, 'margin_mm': round(m, 4),
                        'distance_mm': round(d, 4),
                        'needed_mm': round(needed, 4),
                        'blocker': {'kind': kind, 'net': net, 'at': at}})
    if not out:
        return {'feasible': True, 'margin_mm': 0.0, 'layers': []}
    bind = min(out, key=lambda r: r['margin_mm'])
    return {'feasible': all(r['margin_mm'] >= 0 for r in out),
            'margin_mm': bind['margin_mm'],
            'layer': bind['layer'], 'blocker': bind['blocker'],
            'layers': out}


def via_sites(x, y, nc, layers, segs, rects, radii=(0.3, 0.5, 0.8, 1.1, 1.5),
              angles=12, via_r=0.25, clearance=0.2):
    """Is the anchor sealed?  Legal via sites around (x,y), per layer.

    Tries `len(radii) * angles` candidate sites per layer (like the escape
    ladders do) and reports, for every layer: how many are legal, where the
    nearest legal site is, and its distance.  `sealed` = no legal site on
    any layer.
    """
    cands = [(x, y)] + [
        (x + r * math.cos(2 * math.pi * k / angles),
         y + r * math.sin(2 * math.pi * k / angles))
        for r in radii for k in range(angles)]
    best = []
    for li in range(len(layers)):
        legal = []
        for (cx, cy) in cands:
            if via_profile(cx, cy, nc, segs, rects, via_r,
                           clearance)['feasible']:
                legal.append((math.hypot(cx - x, cy - y), cx, cy))
        if legal:
            d, cx, cy = min(legal)
            best.append({'layer': li, 'legal': len(legal),
                         'tried': len(cands),
                         'nearest_mm': round(d, 3),
                         'nearest': [round(cx, 3), round(cy, 3)]})
        else:
            best.append({'layer': li, 'legal': 0, 'tried': len(cands),
                         'nearest_mm': None, 'nearest': None})
    return {'sealed': all(b['legal'] == 0 for b in best), 'layers': best,
            'any_legal': sum(b['legal'] for b in best)}


def near_miss_profile(results):
    """Did the search ladder improve, or is the corridor walled?

    `results` is the per-step output an operator ladder collected:
    [{'step': <value>, 'margin_mm': <best margin of that step>}, ...].
    A monotone ladder that never improved is a wall; any improvement means
    the search was too coarse.
    """
    if not results:
        return {'verdict': 'no_data', 'best_margin_mm': None, 'improving': None}
    # "best" = least violated, i.e. the largest margin
    best = max(results, key=lambda r: r.get('margin_mm', 0.0))
    improved = any(r.get('margin_mm', 0.0) > results[0].get('margin_mm', 0.0)
                   for r in results[1:])
    return {'verdict': ('walled' if not improved else 'search_too_coarse'),
            'best_margin_mm': best.get('margin_mm'),
            'best_step': best.get('step'),
            'improving': improved,
            'steps': results}


def search_budget(expanded, limit, found):
    """Was the search cut off by its own budget instead of by geometry?"""
    return {'exhausted': bool(not found and expanded >= limit),
            'found': bool(found),
            'expanded': int(expanded or 0), 'limit': int(limit or 0),
            'verdict': ('budget_exhausted'
                        if not found and expanded >= limit else
                        'geometry' if not found else 'found')}


def resolve_blocker(board, blocker):
    """Board-level identity of a geomir blocker: layer name, uuid, ref.

    `blocker` is a dict like {'kind': 'track'|'pad', 'net': <netcode>,
    'at': [x, y]} — the caller's layer index tells which layer it bound on.
    Lazy pcbnew import: the pure functions above stay pcbnew-free.
    """
    import pcbnew
    ax, ay = (blocker.get('at') or [0.0, 0.0])[:2]
    want = blocker.get('net')
    kind = blocker.get('kind') or ''
    layer = blocker.get('layer')
    if kind == 'pad':
        for fp in board.GetFootprints():
            for pad in fp.Pads():
                if pad.GetNetCode() != want:
                    continue
                q = pad.GetPosition()
                if math.hypot(q.x / 1e6 - ax, q.y / 1e6 - ay) < 0.3:
                    return {'kind': 'pad', 'net': pad.GetNetname(),
                            'ref': fp.GetReference(), 'pad': pad.GetNumber(),
                            'uuid': pad.m_Uuid.AsString(),
                            'layer': _layer_name(board, layer)}
    for t in board.GetTracks():
        if t.GetNetCode() != want:
            continue
        s, e = t.GetStart(), t.GetEnd()
        d = _pt_seg_mm(ax, ay, s.x / 1e6, s.y / 1e6, e.x / 1e6, e.y / 1e6)
        if d < 0.3:
            is_via = (t.Type() == pcbnew.PCB_VIA_T)
            return {'kind': 'via' if is_via else 'track',
                    'net': t.GetNetname(),
                    'uuid': t.m_Uuid.AsString(),
                    'layer': _layer_name(board, t.GetLayer() if not is_via
                                         else None),
                    'width_mm': round(t.GetWidth() / 1e6, 4)}
    return {'kind': kind, 'net': blocker.get('net'), 'unresolved': True}


# ---------------------------------------------------------------- helpers
def _pt_pad_dist(x, y, rect):
    from . import geomir
    return geomir.pt_pad_dist(x, y, rect)


def _finish(rows, width):
    if not rows:
        return {'feasible': True, 'margin_mm': 0.0, 'w_max_mm': None,
                'blocker': None, 'rows': []}
    m, d, needed, kind, net, at, hw = min(rows)
    rows.sort(key=lambda r: r[0])
    return {'feasible': m >= 0, 'margin_mm': round(m, 4),
            'w_max_mm': round(width + 2.0 * m, 4),
            'blocker': {'kind': kind, 'net': net, 'at': [round(at[0], 3),
                                                         round(at[1], 3)],
                        'distance_mm': round(d, 4),
                        'needed_mm': round(needed, 4),
                        'short_by_mm': round(-m, 4) if m < 0 else 0.0,
                        'move_by_mm': round(-m, 4) if m < 0 else 0.0},
            'near_misses': [{'margin_mm': round(x[0], 4), 'kind': x[3],
                             'net': x[4]}
                            for x in rows[:4]]}


def _pt_seg_mm(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy
    if l2 == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / l2))
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


def _layer_name(board, lid):
    import pcbnew
    if lid is None:
        return 'all'
    try:
        return board.GetLayerName(lid)
    except Exception:
        return str(lid)
