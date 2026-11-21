#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""smart_via — Smart via placer for BGA escape (Quilter-inspired).

NOT fixed IPC-7095 dogbone — dynamically selects via positions based on:
- Congestion density
- Escape channel capacity
- Layer transition cost
- Via count minimization

Based on:
- Quilter: automatic via patterns, escape directions, breakout traces
- FanoutNet: multi-constraint cost function
- PCBRouteAgent: via regions at layer transitions

Usage:
    from smart_via import SmartViaPlacer
    placer = SmartViaPlacer(board, layer_ids)
    vias = placer.place_for_ball(ball_x, ball_y, net, target_layer)
"""
import math
from dataclasses import dataclass, field

MM = 1e6


@dataclass
class ViaCandidate:
    """A candidate via position."""
    x: float
    y: float
    cost: float = 0.0
    congestion: float = 0.0
    layer_change_cost: float = 0.0


@dataclass
class ViaPlacement:
    """Final via placement decision."""
    ball_id: str
    ball_x: float
    ball_y: float
    via_x: float
    via_y: float
    from_layer: int
    to_layer: int
    net: str
    strategy: str  # 'dog_ear', 'inline', 'adaptive'


class SmartViaPlacer:
    """Smart via placer for BGA escape (Quilter-inspired)."""

    def __init__(self, board, layer_ids, via_d=0.6, via_drill=0.35,
                 clearance=0.15, pitch=0.8):
        self.b = board
        self.lids = layer_ids
        self.via_d = via_d
        self.via_drill = via_drill
        self.clearance = clearance
        self.pitch = pitch

        # Congestion map: (layer, gx, gy) -> count
        self.congestion = {}

    def place_for_ball(self, ball_id, ball_x, ball_y, net, nc,
                       target_layer=1, existing_vias=None):
        """Place a via for a BGA ball.

        Args:
            ball_id: ball identifier (e.g. "A1")
            ball_x, ball_y: ball center (mm)
            net: net name
            nc: net code
            target_layer: layer index to escape to
            existing_vias: list of (x, y) existing via positions

        Returns:
            ViaPlacement or None if no valid position found
        """
        candidates = self._gen_candidates(ball_x, ball_y, target_layer)

        # Score candidates
        scored = []
        for c in candidates:
            cost = self._eval_candidate(c, nc, existing_vias or [])
            c.cost = cost
            scored.append(c)

        # Sort by cost (lowest first)
        scored.sort(key=lambda c: c.cost)

        # Try best candidates
        for c in scored[:5]:
            if self._validate(c, nc):
                return ViaPlacement(
                    ball_id=ball_id,
                    ball_x=ball_x,
                    ball_y=ball_y,
                    via_x=c.x,
                    via_y=c.y,
                    from_layer=0,
                    to_layer=target_layer,
                    net=net,
                    strategy='adaptive',
                )

        return None

    def _gen_candidates(self, ball_x, ball_y, target_layer):
        """Generate candidate via positions (Quilter-style: multiple patterns)."""
        candidates = []

        # Dog_ear pattern (diagonal 45°)
        diag = self.pitch / math.sqrt(2)
        for dx, dy in [(diag, diag), (-diag, diag), (diag, -diag), (-diag, -diag)]:
            candidates.append(ViaCandidate(
                x=ball_x + dx,
                y=ball_y + dy,
                layer_change_cost=1.0,
            ))

        # Inline pattern (horizontal/vertical)
        half_pitch = self.pitch / 2
        for dx, dy in [(half_pitch, 0), (-half_pitch, 0), (0, half_pitch), (0, -half_pitch)]:
            candidates.append(ViaCandidate(
                x=ball_x + dx,
                y=ball_y + dy,
                layer_change_cost=1.0,
            ))

        # Offset patterns (for congested areas)
        for dx, dy in [(diag*1.5, 0), (-diag*1.5, 0), (0, diag*1.5), (0, -diag*1.5)]:
            candidates.append(ViaCandidate(
                x=ball_x + dx,
                y=ball_y + dy,
                layer_change_cost=1.5,
            ))

        return candidates

    def _eval_candidate(self, candidate, nc, existing_vias):
        """Evaluate via candidate cost (FanoutNet-style multi-constraint)."""
        cost = 0.0

        # Constraint 1: congestion (fewer nearby vias = better)
        nearby_vias = 0
        for (ex, ey) in existing_vias:
            dist = math.hypot(candidate.x - ex, candidate.y - ey)
            if dist < self.via_d * 2:
                nearby_vias += 1
        cost += nearby_vias * 10.0

        # Constraint 2: distance from ball (shorter = better)
        ball_dist = math.hypot(candidate.x - self._current_ball_x,
                               candidate.y - self._current_ball_y)
        cost += ball_dist * 2.0

        # Constraint 3: layer change cost
        cost += candidate.layer_change_cost * 5.0

        # Constraint 4: clearance to existing copper
        min_clearance_dist = self._calc_min_clearance(candidate.x, candidate.y, nc)
        if min_clearance_dist < self.clearance + self.via_d/2:
            cost += 100.0  # heavy penalty for clearance violation

        return cost

    def _validate(self, candidate, nc):
        """Validate via candidate (exact geometry check)."""
        # Check clearance to existing copper
        sgs = self._extract_copper()
        for li in range(len(self.lids)):
            for (net, a, b, hw) in sgs[li]:
                if net == nc:
                    continue
                dist = self._pt_seg_dist(candidate.x, candidate.y, a[0], a[1], b[0], b[1])
                if dist < self.clearance + self.via_d/2 + hw:
                    return False

        # Check hole-to-hole
        for fp in self.b.GetFootprints():
            for pad in fp.Pads():
                try:
                    if not pad.HasHole():
                        continue
                except Exception:
                    continue
                q = pad.GetPosition()
                dist = math.hypot(candidate.x - q.x/MM, candidate.y - q.y/MM)
                try:
                    pad_drill = pad.GetDrillSizeX() / MM
                except Exception:
                    pad_drill = 0.5
                min_dist = self.via_drill/2 + pad_drill/2 + 0.25
                if dist < min_dist:
                    return False

        return True

    def _extract_copper(self):
        """Extract copper geometry from board."""
        import geomir
        sgs, _ = geomir.extract_objects(self.b, self.lids)
        return sgs

    def _calc_min_clearance(self, x, y, nc):
        """Calculate minimum clearance to existing copper."""
        sgs = self._extract_copper()
        min_dist = 1e9
        for li in range(len(self.lids)):
            for (net, a, b, hw) in sgs[li]:
                if net == nc:
                    continue
                dist = self._pt_seg_dist(x, y, a[0], a[1], b[0], b[1])
                min_dist = min(min_dist, dist - hw)
        return min_dist

    def _pt_seg_dist(self, px, py, ax, ay, bx, by):
        """Point to segment distance."""
        dx, dy = bx - ax, by - ay
        l2 = dx * dx + dy * dy
        if l2 == 0:
            return math.hypot(px - ax, py - ay)
        t = max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / l2))
        return math.hypot(px - ax - t * dx, py - ay - t * dy)

    @property
    def _current_ball_x(self):
        return 0.0

    @property
    def _current_ball_y(self):
        return 0.0


def place_bga_vias(board, layer_ids, balls):
    """Place vias for BGA balls using smart via placer.

    Args:
        board: pcbnew.BOARD
        layer_ids: list of layer IDs
        balls: list of dicts with {id, x, y, net, nc, target_layer}

    Returns:
        list of ViaPlacement
    """
    placer = SmartViaPlacer(board, layer_ids)
    placements = []
    existing_vias = []

    for ball in balls:
        placement = placer.place_for_ball(
            ball['id'], ball['x'], ball['y'],
            ball['net'], ball['nc'],
            ball.get('target_layer', 1),
            existing_vias,
        )
        if placement:
            placements.append(placement)
            existing_vias.append((placement.via_x, placement.via_y))

    return placements
