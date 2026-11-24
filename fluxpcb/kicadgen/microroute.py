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
"""
from __future__ import annotations

import heapq
import math

from . import geomir
from . import routegrid as rg


def _free_test(width, nc, segs, rects, rules):
    """A point predicate: may the centre line of a `width` track of net
    `nc` pass here?  Same distances as geomir.verify_polyline."""
    own = width / 2
    clear = (rules or {}).get('clearance', geomir.CLEAR)
    smd = (rules or {}).get('clearance_smd', geomir.CLEAR_SMD)
    others = [(g[1], g[2], g[3]) for g in segs if g[0] != nc]
    pads = [g[1] for g in rects if g[0] != nc]
    ssd, ppd = rg.seg_seg_dist, geomir.pt_pad_dist

    def free(x, y):
        p = (x, y)
        for s, e, hw in others:
            if ssd(p, p, s, e) < clear + hw + own:
                return False
        for rect in pads:
            if ppd(x, y, rect) < smd + own:
                return False
        return True
    return free


def plan_local(a, b, width, nc, segs, rects, rules=None, step=None,
               margin=0.45, max_cells=250000):
    """A same-layer path from a to b at `width`, or None.

    a, b: (x, y) anchors in mm.  segs, rects: this layer's obstacle lists
    as geomir.extract_objects returns them (the copper being replaced is
    the caller's to leave out).  step: grid pitch; by default 20 µm,
    coarsened until the window fits `max_cells`.

    Returns {'pts': [(x, y), ...], 'step', 'cells', 'expanded'} with
    every segment exactly verified, or None when no path was found.
    """
    free = _free_test(width, nc, segs, rects, rules)
    reach = width / 2 + (rules or {}).get('clearance', geomir.CLEAR) + 0.02
    x0 = min(a[0], b[0]) - margin
    y0 = min(a[1], b[1]) - margin
    x1 = max(a[0], b[0]) + margin
    y1 = max(a[1], b[1]) + margin
    # only copper that reaches the window can block a cell in it
    segs = [g for g in segs if _touches(g[1], g[2], g[3], reach, x0, y0, x1, y1)]
    rects = [g for g in rects
             if _touches((g[1][0], g[1][1]), (g[1][0], g[1][1]),
                         math.hypot(g[1][2], g[1][3]), reach, x0, y0, x1, y1)]
    free = _free_test(width, nc, segs, rects, rules)
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

    grid = [[free(*point(i, j)) for i in range(nx)] for j in range(ny)]

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

    start, goal = near_free(a), near_free(b)
    if start == goal:
        return _finish([a, b], width, nc, segs, rects, rules, step, 0, 0)
    gi, gj = goal

    def h(i, j):
        dx, dy = abs(i - gi), abs(j - gj)
        return (max(dx, dy) + (math.sqrt(2) - 1) * min(dx, dy)) * step

    heap = [(h(*start), 0.0, start)]
    gscore = {start: 0.0}
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
        for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1),
                       (1, 1), (1, -1), (-1, 1), (-1, -1)):
            k, l = i + di, j + dj
            if not (0 <= k < nx and 0 <= l < ny) or not grid[l][k]:
                continue
            if di and dj and not (grid[j][k] and grid[l][i]):
                continue                  # no corner cutting
            ng = g + (math.sqrt(2) if di and dj else 1.0) * step
            if ng < gscore.get((k, l), math.inf):
                gscore[(k, l)] = ng
                parent[(k, l)] = (i, j)
                heapq.heappush(heap, (ng + h(k, l), ng, (k, l)))
    if not found:
        return None
    path = [goal]
    while path[-1] != start:
        path.append(parent[path[-1]])
    path.reverse()
    pts = [a] + [point(i, j) for i, j in path[1:-1]] + [b]
    return _finish(pts, width, nc, segs, rects, rules, step, nx * ny, expanded)


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
