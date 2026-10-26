# -*- coding: utf-8 -*-
"""spatial_hash — uniform-bucket spatial index for the gridless router
(N-track, N1).

0.5mm buckets map to copper primitives (segments / pad rects / via
circles). add()/query_rect() are O(1) per bucket — the exact-clearance
edge validation in route_explore queries only the few nearby buckets
instead of scanning every shape (the O(n) scan is what made
GridModel.clear_exact unusable as a per-expansion check at wire count
>5k).

No third-party deps: buckets are dicts of lists; shapes are plain
tuples the GridModel already collects.
"""
from __future__ import annotations

BUCKET = 0.5          # mm


class SpatialHash:
    def __init__(self, bucket_mm: float = BUCKET):
        self.bucket = bucket_mm
        self.segs = {}      # (bx, by) -> [(net, (x1,y1), (x2,y2), half_w)]
        self.rects = {}     # (bx, by) -> [(net, (cx, cy, hw, hh, ang))]
        self.vias = {}      # (bx, by) -> [(net, x, y, r)]
        self.n = 0

    # -------------------------------------------------- helpers
    def _range(self, x0, y0, x1, y1):
        b = self.bucket
        return range(int(x0 / b), int(x1 / b) + 1), \
            range(int(y0 / b), int(y1 / b) + 1)

    def _insert(self, table, x0, y0, x1, y1, item):
        bxr, byr = self._range(x0, y0, x1, y1)
        for bx in bxr:
            for by in byr:
                table.setdefault((bx, by), []).append(item)
        self.n += 1

    # -------------------------------------------------- builders
    def add_seg(self, net, a, b, half_w):
        (x1, y1), (x2, y2) = a, b
        pad = half_w + self.bucket
        self._insert(self.segs, min(x1, x2) - pad, min(y1, y2) - pad,
                     max(x1, x2) + pad, max(y1, y2) + pad,
                     (net, a, b, half_w))

    def add_rect(self, net, rect, margin=0.0):
        cx, cy, hw, hh, _ang = rect
        m = max(hw, hh) + margin + self.bucket
        self._insert(self.rects, cx - m, cy - m, cx + m, cy + m,
                     (net, rect))

    def add_via(self, net, x, y, r):
        m = r + self.bucket
        self._insert(self.vias, x - m, y - m, x + m, y + m,
                     (net, x, y, r))

    def load_gridmodel_layer(self, gm, li):
        """Load one copper layer's fixed primitives from a GridModel
        (segs/rects are the exact un-rasterized geometry; vias arrive as
        zero-length segs on every layer they cross)."""
        for net, a, b, hw in gm.segs[li]:
            self.add_seg(net, a, b, hw)
        for net, rect in gm.rects[li]:
            self.add_rect(net, rect)
        return self.n

    # -------------------------------------------------- queries
    def query_rect(self, x0, y0, x1, y1):
        """All shapes whose buckets intersect the rect (conservative —
        callers run the exact geometry check on the candidates)."""
        bxr, byr = self._range(x0, y0, x1, y1)
        segs, rects, vias = set(), set(), set()
        for bx in bxr:
            for by in byr:
                for it in self.segs.get((bx, by), ()):
                    segs.add(it)
                for it in self.rects.get((bx, by), ()):
                    rects.add(it)
                for it in self.vias.get((bx, by), ()):
                    vias.add(it)
        return segs, rects, vias


def build_per_layer(gm) -> list:
    """One SpatialHash per router layer of a GridModel."""
    out = []
    for li in range(gm.nl):
        sh = SpatialHash()
        sh.load_gridmodel_layer(gm, li)
        out.append(sh)
    return out
