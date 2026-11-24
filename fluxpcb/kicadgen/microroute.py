"""Local fine-grid re-plan — for corridors tens of microns wide.

geomir proposes a handful of polyline shapes with offsets of half a
millimetre and up; the grid maze steps on routegrid's fixed resolution.
Both miss a corridor like the one on 0054, where a 0.2 mm track fits
between two vias with 0.06 mm to spare.  This planner searches only the
window around the connection that has to move, on a grid fine enough to
see such a corridor, and hands what it finds to the exact verifier.

The grid only proposes.  The path it finds is shortcut with
geomir.verify_polyline, so what comes out is legal by the same test the
commit gate applies; a grid cell is "free" under the same two distances
that test uses.

With `cross` the search may enter a foreign TRACK's clearance envelope
at a price (PathFinder's rip-up, priced into the search).  The items a
priced path runs through are the conflict set: the copper that has to
move for this connection to pass.  Pads and vias are never entered.
"""
from __future__ import annotations

import heapq
import math

from . import geomir
from . import routegrid as rg

SQRT2 = math.sqrt(2)
MOVES = ((1, 0), (-1, 0), (0, 1), (0, -1),
         (1, 1), (1, -1), (-1, 1), (-1, -1))


def _cells(x0, y0, step, nx, ny, box, test):
    """(j, i) of the cells inside `box` (mm) that fail `test`."""
    i0 = max(0, int(math.floor((box[0] - x0) / step)))
    i1 = min(nx - 1, int(math.ceil((box[2] - x0) / step)))
    j0 = max(0, int(math.floor((box[1] - y0) / step)))
    j1 = min(ny - 1, int(math.ceil((box[3] - y0) / step)))
    out = []
    for j in range(j0, j1 + 1):
        y = y0 + j * step
        for i in range(i0, i1 + 1):
            if test(x0 + i * step, y):
                out.append((j, i))
    return out


def _span(n, lo, hi, o0, step):
    """First and last index with o0 + k * step inside [lo, hi]."""
    return (max(0, min(n, int(math.ceil((lo - o0) / step) - 1e-9))),
            min(n - 1, max(-1, int(math.floor((hi - o0) / step) + 1e-9))))


def _wall_edge(grid, owner, nx, ny, x0, y0, step, edge, keep):
    """Block the cells whose copper would come within `keep` of the board
    edge.  Without this a plan can leave the board: the exact verifier
    knows copper, not the outline, and only the DRC would refuse it."""
    if not edge:
        return
    ia, ib = _span(nx, edge[0] + keep, edge[2] - keep, x0, step)
    ja, jb = _span(ny, edge[1] + keep, edge[3] - keep, y0, step)
    out = list(range(0, ia)) + list(range(ib + 1, nx))
    for j in range(ny):
        row, own = grid[j], owner[j]
        if j < ja or j > jb:
            row[:] = bytes(nx)
            own[:] = bytes(nx)
            continue
        for i in out:
            row[i] = 0
            own[i] = 0


def _raster(nx, ny, x0, y0, step, width, nc, segs, rects, rules, priced,
            edge=None, keep=0.0, avoid=()):
    """(grid, owner, items).  grid: 1 where the centreline may go.  In
    priced mode owner names the foreign track whose envelope a blocked
    cell is in (items[owner - 1]) and 0 means a wall.  `avoid` names
    tracks that are walls even in priced mode."""
    own, clear, smd = width / 2, (rules or {}).get('clearance', geomir.CLEAR), \
        (rules or {}).get('clearance_smd', geomir.CLEAR_SMD)
    grid = [bytearray(b'\x01' * nx) for _ in range(ny)]
    owner = [bytearray(nx) for _ in range(ny)]
    items = []
    psd, ppd = rg.pt_seg_dist, geomir.pt_pad_dist

    def envelope(net, s, e, hw, idx):
        reach = clear + hw + own
        if idx:
            items.append((net, s, e, hw))
        for (j, i) in _cells(x0, y0, step, nx, ny,
                             (min(s[0], e[0]) - reach, min(s[1], e[1]) - reach,
                              max(s[0], e[0]) + reach, max(s[1], e[1]) + reach),
                             lambda x, y: psd(x, y, s[0], s[1], e[0], e[1]) < reach):
            grid[j][i] = 0
            owner[j][i] = idx

    for g in segs:
        net, s, e, hw = g[:4]
        if net == nc:
            continue
        # a via (s == e) is never entered; a byte cannot name item 256
        if (priced and s != e and len(items) < 255
                and (net, s, e) not in avoid):
            envelope(net, s, e, hw, len(items) + 1)
        else:
            envelope(net, s, e, hw, 0)
    for g in rects:
        net, rect = g[0], g[1]
        if net == nc:
            continue
        need = geomir.pad_need(rect, smd) + own
        hw, hh = rect[2], rect[3]
        if (len(rect) > 4 and rect[4]) and not (len(rect) > 5 and rect[5]):
            hw = hh = math.hypot(hw, hh)     # the box that holds it at any angle
        cx, cy = rect[0], rect[1]
        for (j, i) in _cells(x0, y0, step, nx, ny,
                             (cx - hw - need, cy - hh - need,
                              cx + hw + need, cy + hh + need),
                             lambda x, y: ppd(x, y, rect) < need):
            grid[j][i] = 0
            owner[j][i] = 0          # a wall, even over a priced envelope
    _wall_edge(grid, owner, nx, ny, x0, y0, step, edge, keep)
    return grid, owner, items


def _window(a, b, margin):
    return (min(a[0], b[0]) - margin, min(a[1], b[1]) - margin,
            max(a[0], b[0]) + margin, max(a[1], b[1]) + margin)


def plan_local(a, b, width, nc, segs, rects, rules=None, step=None,
               margin=0.45, max_cells=250000, cross=None, edge=None,
               edge_keep=0.5):
    """A same-layer path from a to b at `width`, or None.

    edge is the board outline's box and edge_keep the clearance copper
    must keep from it; cells outside are walls.

    a, b: (x, y) anchors in mm.  segs, rects: this layer's obstacle lists
    as geomir.extract_objects returns them (the copper being replaced is
    the caller's to leave out).  step: grid pitch; by default 20 µm,
    coarsened until the window fits `max_cells`.

    Returns {'pts': [(x, y), ...], 'step', 'cells', 'expanded'} with
    every segment exactly verified, or None when no path was found.

    cross={'cost': c, 'max': k, 'inside': r, 'avoid': [...]} prices the
    clearance envelope of a foreign track at `c` mm to enter and `r` mm
    per mm spent inside it, and returns instead

        {'pts': [(x, y), ...] or None, 'crossed': [(net, s, e, hw), ...],
         'step', 'cells', 'expanded', 'why'}

    with `crossed` the conflict set of the priced path (at most `k`
    items: a path that needs more comes back with pts None and
    why 'set_too_large').  The points are the raw grid path — nothing
    that crosses is legal until the set is lifted and the connection is
    planned again without `cross`.  Tracks named in `avoid`
    ((net, s, e) as the obstacle lists carry them) are walls, which is
    how a caller asks for the next set after one failed.
    """
    priced = cross is not None
    cross_cost = float((cross or {}).get('cost', 10.0))
    cross_max = int((cross or {}).get('max', 2))
    inside = float((cross or {}).get('inside', cross_cost / 2.0))
    x0, y0, x1, y1 = _window(a, b, margin)
    segs, rects = _clip([segs], [rects], width, rules, x0, y0, x1, y1)
    segs, rects = segs[0], rects[0]
    if step is None or step <= 0:
        step = 0.02
    while True:
        nx = int(math.ceil((x1 - x0) / step)) + 1
        ny = int(math.ceil((y1 - y0) / step)) + 1
        if nx * ny <= max_cells:
            break
        step *= 1.5

    def cell(x, y):
        return (min(nx - 1, max(0, int(round((x - x0) / step)))),
                min(ny - 1, max(0, int(round((y - y0) / step)))))

    def point(i, j):
        return (x0 + i * step, y0 + j * step)

    grid, owner, items = _raster(nx, ny, x0, y0, step, width, nc, segs,
                                 rects, rules, priced, edge,
                                 edge_keep + width / 2,
                                 {tuple(x[:3]) for x in (cross or {}).get('avoid') or ()})

    def open(i, j):
        return bool(grid[j][i]) or bool(owner[j][i] and priced)

    def near_free(p, rmax=6):
        """Nearest free cell to an anchor, or its own cell when nothing
        free is that close (the exact verifier will say if the anchor
        itself cannot take the width)."""
        ci, cj = cell(*p)
        for r in range(rmax + 1):
            for di in range(-r, r + 1):
                for dj in range(-r, r + 1):
                    if max(abs(di), abs(dj)) != r:
                        continue
                    i, j = ci + di, cj + dj
                    if 0 <= i < nx and 0 <= j < ny and grid[j][i]:
                        return (i, j)
        return (ci, cj)

    start, goal = cell(*a), cell(*b)
    if not priced:
        start, goal = near_free(a), near_free(b)
        if start == goal:
            return _finish([a, b], width, nc, segs, rects, rules, step, 0, 0)
    gi, gj = goal

    def h(i, j):
        dx, dy = abs(i - gi), abs(j - gj)
        return (max(dx, dy) + (SQRT2 - 1) * min(dx, dy)) * step

    g0 = cross_cost if owner[start[1]][start[0]] else 0.0
    heap = [(h(*start) + g0, g0, start)]
    gscore = {start: g0}
    parent = {}
    expanded = 0
    found = False
    while heap:
        _f, g, (i, j) = heapq.heappop(heap)
        if g > gscore.get((i, j), math.inf) + 1e-12:
            continue
        if (i, j) == goal:
            found = True
            break
        expanded += 1
        here = owner[j][i]
        for di, dj in MOVES:
            k, l = i + di, j + dj
            if not (0 <= k < nx and 0 <= l < ny) or not open(k, l):
                continue
            if di and dj and not (open(k, j) and open(i, l)):
                continue                      # no corner cutting
            run = (SQRT2 if di and dj else 1.0) * step
            ng = g + run
            there = owner[l][k]
            if priced and there:
                ng += inside * run
                if there != here:
                    ng += cross_cost
            if ng < gscore.get((k, l), math.inf):
                gscore[(k, l)] = ng
                parent[(k, l)] = (i, j)
                heapq.heappush(heap, (ng + h(k, l), ng, (k, l)))
    out = {'pts': None, 'step': step, 'cells': nx * ny,
           'expanded': expanded}
    if not found:
        out['why'] = 'no_path'
        out['crossed'] = []
        return out if priced else None
    path = [goal]
    while path[-1] != start:
        path.append(parent[path[-1]])
    path.reverse()
    if priced:
        crossed = sorted({owner[j][i] for i, j in path if owner[j][i]})
        out['crossed'] = [items[k - 1] for k in crossed]
        if len(crossed) > cross_max:
            out['why'] = 'set_too_large'
            return out
        out['why'] = 'crosses' if crossed else 'clear'
        out['pts'] = [a] + [point(i, j) for i, j in path[1:-1]] + [b]
        return out
    pts = [a] + [point(i, j) for i, j in path[1:-1]] + [b]
    return _finish(pts, width, nc, segs, rects, rules, step, nx * ny, expanded)


def plan_layers(a, b, la, lb, width, nc, segs, rects, rules=None, via_r=0.3,
                via_cost=4.0, step=None, margin=1.5, max_cells=125000,
                edge=None, edge_keep=0.5):
    """A path from a on layer `la` to b on layer `lb`, changing layer
    through vias wherever one is legal, at `via_cost` mm per via.

    Same grids as plan_local, one per layer, plus one that says where a
    through-via's barrel clears foreign copper on every layer (pads are
    judged at their own clearance, as geomir.via_ok judges them).

    Returns {'pts': [(x, y, layer), ...], 'vias', 'length_mm', 'step',
    'cells', 'expanded'} or None.  A point that appears twice in a row
    marks the via between its two layers.  Every segment and every via
    is exactly verified; the search only proposes.
    """
    nlay = len(segs)
    clear = (rules or {}).get('clearance', geomir.CLEAR)
    x0, y0, x1, y1 = _window(a, b, margin)
    segs, rects = _clip(segs, rects, width, rules, x0, y0, x1, y1)
    if step is None or step <= 0:
        step = 0.02
    while True:
        nx = int(math.ceil((x1 - x0) / step)) + 1
        ny = int(math.ceil((y1 - y0) / step)) + 1
        if nx * ny <= max_cells:
            break
        step *= 1.5
    grids = [_raster(nx, ny, x0, y0, step, width, nc, segs[li], rects[li],
                     rules, False, edge, edge_keep + width / 2)[0]
             for li in range(nlay)]

    # where a via may go: one grid for every layer, a barrel crosses all
    ok_via = [bytearray(b'\x01' * nx) for _ in range(ny)]
    psd, ppd = rg.pt_seg_dist, geomir.pt_pad_dist
    for li in range(nlay):
        for g in segs[li]:
            net, s, e, hw = g[:4]
            if net == nc:
                continue
            reach = clear + hw + via_r
            for (j, i) in _cells(x0, y0, step, nx, ny,
                                 (min(s[0], e[0]) - reach, min(s[1], e[1]) - reach,
                                  max(s[0], e[0]) + reach, max(s[1], e[1]) + reach),
                                 lambda x, y: psd(x, y, s[0], s[1], e[0], e[1]) < reach):
                ok_via[j][i] = 0
        for g in rects[li]:
            net, rect = g[0], g[1]
            if net == nc:
                continue
            reach = max(clear, geomir.pad_need(rect, 0.0)) + via_r
            hw, hh = rect[2], rect[3]
            if rect[4] and not (len(rect) > 5 and rect[5]):
                hw = hh = math.hypot(hw, hh)
            for (j, i) in _cells(x0, y0, step, nx, ny,
                                 (rect[0] - hw - reach, rect[1] - hh - reach,
                                  rect[0] + hw + reach, rect[1] + hh + reach),
                                 lambda x, y: ppd(x, y, rect) < reach):
                ok_via[j][i] = 0
    if edge:                                   # a barrel is copper too
        ia, ib = _span(nx, edge[0] + edge_keep + via_r, edge[2] - edge_keep - via_r, x0, step)
        ja, jb = _span(ny, edge[1] + edge_keep + via_r, edge[3] - edge_keep - via_r, y0, step)
        for j in range(ny):
            if j < ja or j > jb:
                ok_via[j][:] = bytes(nx)
            else:
                for i in list(range(0, ia)) + list(range(ib + 1, nx)):
                    ok_via[j][i] = 0

    def cell(x, y):
        return (min(nx - 1, max(0, int(round((x - x0) / step)))),
                min(ny - 1, max(0, int(round((y - y0) / step)))))

    def near_free(p, li, rmax=6):
        """Nearest cell on layer `li` where the centreline may stand, or
        the anchor's own cell when nothing free is that close."""
        ci, cj = cell(*p)
        grid = grids[li]
        for r in range(rmax + 1):
            for di in range(-r, r + 1):
                for dj in range(-r, r + 1):
                    if max(abs(di), abs(dj)) != r:
                        continue
                    i, j = ci + di, cj + dj
                    if 0 <= i < nx and 0 <= j < ny and grid[j][i]:
                        return (i, j)
        return (ci, cj)

    gi, gj = cell(*b)
    sa, sb = near_free(a, la)
    goal = (lb, gi, gj)
    if la == lb and (sa, sb) == (gi, gj):
        return _finish_layers([[(a[0], a[1], la), (b[0], b[1], lb)]], width, nc,
                              segs, rects, rules, via_r, step, nx * ny * nlay, 0)

    def h(li, i, j):
        dx, dy = abs(i - gi), abs(j - gj)
        base = (max(dx, dy) + (SQRT2 - 1) * min(dx, dy)) * step
        return base + (via_cost if li != lb else 0.0)

    def index(li, i, j):
        return (li * ny + j) * nx + i

    INF = float('inf')
    gscore = [INF] * (nlay * nx * ny)
    parent = [-1] * (nlay * nx * ny)
    sid, gid = index(la, sa, sb), index(lb, gi, gj)
    gscore[sid] = 0.0
    heap = [(h(la, sa, sb), 0.0, (la, sa, sb))]
    expanded = 0
    found = False
    while heap:
        _f, g, (li, i, j) = heapq.heappop(heap)
        here = index(li, i, j)
        if g > gscore[here] + 1e-12:
            continue
        if here == gid:
            found = True
            break
        expanded += 1
        grid = grids[li]
        for di, dj in MOVES:
            k, l = i + di, j + dj
            if not (0 <= k < nx and 0 <= l < ny) or not grid[l][k]:
                continue
            if di and dj and not (grid[j][k] and grid[l][i]):
                continue                      # no corner cutting
            ng = g + (SQRT2 if di and dj else 1.0) * step
            there = index(li, k, l)
            if ng < gscore[there]:
                gscore[there] = ng
                parent[there] = here
                heapq.heappush(heap, (ng + h(li, k, l), ng, (li, k, l)))
        if ok_via[j][i]:
            for lj in range(nlay):
                if lj == li or not grids[lj][j][i]:
                    continue
                ng = g + via_cost
                there = index(lj, i, j)
                if ng < gscore[there]:
                    gscore[there] = ng
                    parent[there] = here
                    heapq.heappush(heap, (ng + h(lj, i, j), ng, (lj, i, j)))
    if not found:
        return None
    path, cur = [], gid
    while cur != sid:
        path.append(cur)
        cur = parent[cur]
    path.append(sid)
    path.reverse()
    pts = []
    for n, d in enumerate(path):
        li, rest = divmod(d, nx * ny)
        j, i = divmod(rest, nx)
        if n == 0:
            pt = (a[0], a[1], la)
        elif n == len(path) - 1:
            pt = (b[0], b[1], lb)
        else:
            pt = (x0 + i * step, y0 + j * step, li)
        if pts and pts[-1] == pt:
            continue
        pts.append(pt)
    return _finish_layers([pts], width, nc, segs, rects, rules, via_r, step,
                          nx * ny * nlay, expanded)


def _clip(segs, rects, width, rules, x0, y0, x1, y1):
    """Only copper that reaches the window can block a cell in it (a pad
    reaches as far as its own clearance asks)."""
    reach = width / 2 + (rules or {}).get('clearance', geomir.CLEAR) + 0.02
    smd = (rules or {}).get('clearance_smd', geomir.CLEAR_SMD)
    segs = [[g for g in lst if _touches(g[1], g[2], g[3], reach, x0, y0, x1, y1)]
            for lst in segs]
    rects = [[g for g in lst
              if _touches((g[1][0], g[1][1]), (g[1][0], g[1][1]),
                          math.hypot(g[1][2], g[1][3]),
                          width / 2 + geomir.pad_need(g[1], smd) + 0.02,
                          x0, y0, x1, y1)] for lst in rects]
    return segs, rects


def _finish_layers(pts, width, nc, segs, rects, rules, via_r, step, cells,
                   expanded):
    """The exact verifier over a layer-changing path: shortcut inside a
    same-layer run, keep every via, judge each piece once more."""
    pts = pts[0]
    n = len(pts)

    def run_ok(k, j):
        li = pts[k][2]
        if any(p[2] != li for p in pts[k:j + 1]):
            return False
        return _legal(pts[k][:2], pts[j][:2], width, nc, segs[li],
                     rects[li], rules)

    out = [pts[0]]
    k = 0
    while k < n - 1:
        best = k + 1
        for j in range(n - 1, k, -1):
            if j == k + 1 or run_ok(k, j):
                best = j
                break
        out.append(pts[best])
        k = best
    for p, q in zip(out, out[1:]):
        if p[2] != q[2]:
            if not geomir.via_ok(p[0], p[1], nc, segs, rects, via_r=via_r,
                                 rules=rules)[0]:
                return None
        elif (abs(p[0] - q[0]) > 1e-9 or abs(p[1] - q[1]) > 1e-9) and not \
                _legal(p[:2], q[:2], width, nc, segs[p[2]], rects[p[2]], rules):
            return None
    vias = sum(1 for p, q in zip(out, out[1:]) if p[2] != q[2])
    length = sum(math.dist(p[:2], q[:2]) for p, q in zip(out, out[1:]))
    return {'pts': out, 'vias': vias, 'step': step, 'cells': cells,
            'length_mm': round(length, 3), 'expanded': expanded}


def _finish(pts, width, nc, segs, rects, rules, step, cells, expanded):
    """Shortcut with the exact verifier: keep a vertex only when the
    path cannot skip ahead past it."""
    out = [pts[0]]
    k = 0
    while k < len(pts) - 1:
        best = k + 1
        for j in range(len(pts) - 1, k, -1):
            if j == k + 1 or _legal(pts[k], pts[j], width, nc, segs, rects, rules):
                best = j
                break
        if best > k + 1 and not _legal(pts[k], pts[best], width, nc, segs, rects, rules):
            # the adjacent step itself fails the exact test: the grid was
            # optimistic here, and nothing is trusted that is not verified
            return None
        out.append(pts[best])
        k = best
    # every segment once more, from the simplified polyline
    for p, q in zip(out, out[1:]):
        if not _legal(p, q, width, nc, segs, rects, rules):
            return None
    return {'pts': out, 'step': step, 'cells': cells, 'expanded': expanded}


def _touches(p, q, half, reach, x0, y0, x1, y1):
    """Does an item (segment p-q of half-width `half`) come within
    `reach` of the window?"""
    r = half + reach
    return not (max(p[0], q[0]) + r < x0 or min(p[0], q[0]) - r > x1
                or max(p[1], q[1]) + r < y0 or min(p[1], q[1]) - r > y1)


def _legal(p, q, width, nc, segs, rects, rules):
    ok, _ = geomir.verify_polyline([p, q], width, nc, segs, rects, rules)
    return ok
