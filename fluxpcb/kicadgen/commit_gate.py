# -*- coding: utf-8 -*-
"""commit_gate — world-geometry atomic commit gate for router copper.

M-round first axiom (commercial-EDA research): autorouters never REPAIR
cross-net shorts, they PREVENT them — Specctra's wire/copper two-phase,
Freerouting's "only clearance-clean paths are committed, failures are
atomic (never half a trace)". Our engines had no gate: fluxrouter's
cells_to_items ran clear_exact against FIXED copper only, so two nets'
router-placed stubs could geometrically collide while both "succeeded"
(fc iter1: 59 shorts, 85% of them 0.16mm grid-step micro-stubs).

The gate holds one SpatialHash per router layer (fixed copper loaded at
build; each accepted net's copper inserted incrementally so later nets
see it). commit(items, nc) is all-or-nothing: every item is checked
against every OTHER-net shape near it in WORLD coordinates (via barrels
span all layers); one conflict rejects the whole batch.
"""
from __future__ import annotations

import pcbnew

from spatial_hash import SpatialHash


def _item_geoms(item, layer_ids):
    """(layer_index, geometry) pairs for a track or via.

    Track -> (li, seg); via -> zero-length seg on EVERY router layer
    (through-via barrel), width = via diameter.
    """
    mm = 1000000
    try:
        if item.Type() == pcbnew.PCB_VIA_T:
            q = item.GetPosition()
            x, y = q.x / mm, q.y / mm
            w = item.GetWidth(pcbnew.F_Cu) / mm
            return [(li, ((x, y), (x, y), w / 2))
                    for li in range(len(layer_ids))]
        s, e = item.GetStart(), item.GetEnd()
        lid = item.GetLayer()
        li = layer_ids.index(lid) if lid in layer_ids else -1
        if li < 0:
            return []
        w = item.GetWidth() / mm
        return [(li, ((s.x / mm, s.y / mm), (e.x / mm, e.y / mm), w / 2))]
    except (AttributeError, ValueError, TypeError):
        return []


class CommitGate:
    def __init__(self, gm, layer_ids, clear_mm=0.17):
        from spatial_hash import build_per_layer
        self.layers = list(layer_ids)
        self.clear = clear_mm
        self.spatial = build_per_layer(gm)

    @classmethod
    def from_board(cls, board, layer_ids, clear_mm=0.17):
        """Build directly from board copper (no GridModel rasterization):
        pads -> rects, tracks -> segs, vias -> all-layer zero-length segs."""
        import routegrid as rg
        mm = 1000000
        gate = cls.__new__(cls)
        gate.layers = list(layer_ids)
        gate.clear = clear_mm
        gate.spatial = [SpatialHash() for _ in layer_ids]
        for fp in board.GetFootprints():
            for pad in fp.Pads():
                try:
                    nc = pad.GetNetCode()
                    cx, cy, hw, hh, ang = rg.pad_rect(pad)
                    rect = (cx, cy, hw, hh, ang)
                    for li, lid in enumerate(layer_ids):
                        if pad.IsOnLayer(lid):
                            gate.spatial[li].add_rect(nc, rect)
                except (AttributeError, TypeError):
                    continue
        for it in board.GetTracks():
            try:
                nc = it.GetNetCode()
                for li, geom in _item_geoms(it, layer_ids):
                    gate.spatial[li].add_seg(nc, geom[0], geom[1], geom[2])
            except (AttributeError, TypeError):
                continue
        return gate

    # -------------------------------------------------- geometry check
    def _seg_ok(self, li, a, b, half_w, nc):
        from routegrid import seg_seg_dist, pt_rect_dist
        pad = half_w + self.clear + 0.05
        segs, rects, _vias = self.spatial[li].query_rect(
            min(a[0], b[0]) - pad, min(a[1], b[1]) - pad,
            max(a[0], b[0]) + pad, max(a[1], b[1]) + pad)
        for net, s, e, extra in segs:
            if net == nc:
                continue
            if seg_seg_dist(a, b, s, e) < self.clear + extra + half_w:
                return False
        for net, rect in rects:
            if net == nc:
                continue
            import math
            n = max(2, int(math.hypot(b[0] - a[0], b[1] - a[1]) / 0.1))
            for k in range(n + 1):
                t = k / n
                if pt_rect_dist(a[0] + (b[0] - a[0]) * t,
                                a[1] + (b[1] - a[1]) * t,
                                rect) < self.clear + half_w:
                    return False
        return True

    # -------------------------------------------------- API
    def commit(self, board, items, nc) -> bool:
        """All-or-nothing: verify every item in world coordinates; on
        success insert their geometry (later nets see this copper) and
        return True. On any conflict remove NOTHING here — the caller's
        existing failure path removes the batch (atomic by contract)."""
        geoms = []
        for it in items:
            if it is None:
                continue
            geoms.extend(_item_geoms(it, self.layers))
        for li, (a, b, half_w) in geoms:
            if not self._seg_ok(li, a, b, half_w, nc):
                return False
        for li, (a, b, half_w) in geoms:
            self.spatial[li].add_seg(nc, a, b, half_w)
        return True
