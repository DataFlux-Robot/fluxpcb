"""Grid maze escape — A* over routegrid.GridModel cells.

Candidate polylines (geomir) cover 2-3 segment shapes; when the corridor
is walled by foreign copper and every via slot near an anchor is illegal
(0054: 0/60), the remaining primitive is a cell-level search.  The grid
only proposes; exact verification and via_ok still gate emission.
"""
from __future__ import annotations

import heapq


def _grid():
    import routegrid as rg
    return rg


def nearest_cell(gm, x, y, li, nc, rmax=8):
    rg = _grid()
    gx, gy = int(x / rg.GRID), int(y / rg.GRID)
    for r in range(rmax + 1):
        for dx in range(-r, r + 1):
            for dy in range(-r, r + 1):
                if max(abs(dx), abs(dy)) != r:
                    continue
                g = (gx + dx, gy + dy)
                if gm.fixed_free(li, g[0], g[1], nc):
                    return g
    return None


def plan_maze(gm, a, b, nc, max_expand=300000, via_cost=8.0, stats=None):
    """A* between a=(x,y,lid) and b=(x,y,lid); returns [(li,gx,gy)].

    `stats`, when given, is a dict filled with {'expanded', 'limit',
    'found'} so the caller can tell "walled" from "budget exhausted".
    """
    rg = _grid()
    ali, bli = gm.li_of(a[2]), gm.li_of(b[2])
    if ali is None or bli is None:
        return None
    sa = nearest_cell(gm, a[0], a[1], ali, nc)
    gb = nearest_cell(gm, b[0], b[1], bli, nc)
    if sa is None or gb is None:
        return None
    start, goal = (ali, sa[0], sa[1]), (bli, gb[0], gb[1])
    if start == goal:
        return [start]

    def h(n):
        return abs(n[1] - goal[1]) + abs(n[2] - goal[2])

    heap = [(h(start), 0.0, start)]
    gscore = {start: 0.0}
    parent = {}
    expanded = 0
    found = None
    while heap and expanded < max_expand:
        _f, cur, node = heapq.heappop(heap)
        if node == goal:
            found = node
            break
        li, gx, gy = node
        expanded += 1
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = gx + dx, gy + dy
            if not (0 <= nx < gm.nx and 0 <= ny < gm.ny):
                continue
            if not gm.fixed_free(li, nx, ny, nc):
                continue
            nb = (li, nx, ny)
            ng = cur + 1.0
            if ng < gscore.get(nb, float("inf")):
                gscore[nb] = ng
                parent[nb] = node
                heapq.heappush(heap, (ng + h(nb), ng, nb))
        # through-via hop (one drill, any layer)
        if gm.via_cell_ok(li, gx, gy):
            for lj in range(gm.nl):
                if lj == li or not gm.fixed_free(lj, gx, gy, nc):
                    continue
                nb = (lj, gx, gy)
                ng = cur + via_cost
                if ng < gscore.get(nb, float("inf")):
                    gscore[nb] = ng
                    parent[nb] = node
                    heapq.heappush(heap, (ng + h(nb), ng, nb))
    if found is None:
        if stats is not None:
            stats.update({'expanded': expanded, 'limit': max_expand,
                          'found': False})
        return None
    if stats is not None:
        stats.update({'expanded': expanded, 'limit': max_expand, 'found': True})
    chain = []
    node = found
    while node is not None:
        chain.append(node)
        node = parent.get(node)
    chain.reverse()
    return chain


def merge_path(path):
    """Collapse collinear cells; returns [(li, x, y)] at cell centres."""
    import routegrid as rg
    if not path:
        return []
    pts = [(li, (gx + 0.5) * rg.GRID, (gy + 0.5) * rg.GRID)
           for (li, gx, gy) in path]
    out = [pts[0]]
    for i in range(1, len(pts) - 1):
        li, x, y = pts[i]
        p = out[-1]
        q = pts[i + 1]
        if li == p[0] == q[0] and (
                (abs(p[1] - x) < 1e-9 and abs(x - q[1]) < 1e-9) or
                (abs(p[2] - y) < 1e-9 and abs(y - q[2]) < 1e-9)):
            continue
        out.append(pts[i])
    if len(pts) > 1:
        out.append(pts[-1])
    return out
