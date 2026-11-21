#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""element_actions — Discrete routing element action space.

FIRST-CLASS routing elements (no existing work has these):
- Straight segment (0°/90°)
- 45° diagonal segment
- Through via
- Blind via / Buried via / Microvia
- Differential pair segment (coupled, with polarity)

Based on:
- Whole-Route (arXiv 2610.35601): pair-as-lane with via crossover
- CADET (arXiv 2611.16913): make_line / make_via engine actions
- PCBWorld (arXiv 2607.05915): segment-level actions

Usage:
    from element_actions import ElementActionSpace, RouteElement
    space = ElementActionSpace(board, layer_ids)
    elements = space.generate_path(src, dst, constraints)
"""
import math
from dataclasses import dataclass, field
from enum import Enum, auto


class ElementType(Enum):
    """Routing element types."""
    STRAIGHT = auto()      # 0° or 90° segment
    DIAGONAL_45 = auto()   # 45° diagonal segment
    VIA_THROUGH = auto()   # through-hole via
    VIA_BLIND = auto()     # blind via (top→inner or inner→bottom)
    VIA_BURIED = auto()    # buried via (inner→inner)
    VIA_MICRO = auto()     # microvia (HDI)
    DIFF_PAIR = auto()     # coupled differential pair segment


@dataclass
class RouteElement:
    """A single routing element."""
    element_type: ElementType
    x1: float
    y1: float
    x2: float
    y2: float
    layer: int = 0
    target_layer: int = 0  # for vias
    width: float = 0.15
    gap: float = 0.0       # for diff pairs
    polarity: int = 0      # 0=positive, 1=negative (for diff pairs)
    net: str = ""
    net_code: int = 0


@dataclass
class ActionCandidate:
    """A candidate action (element + score)."""
    element: RouteElement
    score: float = 0.0
    valid: bool = True
    violation: str = ""


class ElementActionSpace:
    """Discrete routing element action space."""

    def __init__(self, board, layer_ids, grid_mm=0.16):
        self.b = board
        self.lids = layer_ids
        self.nl = len(layer_ids)
        self.G = grid_mm

    def generate_candidates(self, src_pos, dst_pos, src_layer, dst_layer,
                            net, nc, constraints=None):
        """Generate candidate routing elements for a connection.

        Args:
            src_pos: (x, y) source position
            dst_pos: (x, y) destination position
            src_layer: source layer index
            dst_layer: destination layer index
            net: net name
            nc: net code
            constraints: dict of constraints (diff_pair, width, etc.)

        Returns:
            list of ActionCandidate sorted by score
        """
        candidates = []

        x1, y1 = src_pos
        x2, y2 = dst_pos

        # Generate straight segments (0°/90°)
        candidates.extend(self._gen_straight(x1, y1, x2, y2, src_layer, net, nc))

        # Generate 45° diagonal segments
        candidates.extend(self._gen_diagonal(x1, y1, x2, y2, src_layer, net, nc))

        # Generate vias if layer change needed
        if src_layer != dst_layer:
            candidates.extend(self._gen_vias(x1, y1, src_layer, dst_layer, net, nc))

        # Generate diff pair segments if constraint specified
        if constraints and constraints.get('diff_pair'):
            candidates.extend(self._gen_diff_pair(
                x1, y1, x2, y2, src_layer, net, nc,
                constraints.get('diff_gap', 0.15)))

        # Score and sort
        for c in candidates:
            c.score = self._score_element(c.element, src_pos, dst_pos, constraints)

        candidates.sort(key=lambda c: -c.score)
        return candidates

    def _gen_straight(self, x1, y1, x2, y2, layer, net, nc):
        """Generate straight (0°/90°) segments."""
        candidates = []

        # Horizontal
        candidates.append(ActionCandidate(
            element=RouteElement(
                element_type=ElementType.STRAIGHT,
                x1=x1, y1=y1, x2=x2, y2=y1,
                layer=layer, net=net, net_code=nc,
            )
        ))

        # Vertical
        candidates.append(ActionCandidate(
            element=RouteElement(
                element_type=ElementType.STRAIGHT,
                x1=x1, y1=y1, x2=x1, y2=y2,
                layer=layer, net=net, net_code=nc,
            )
        ))

        # L-shape (horizontal then vertical)
        candidates.append(ActionCandidate(
            element=RouteElement(
                element_type=ElementType.STRAIGHT,
                x1=x1, y1=y1, x2=x2, y2=y1,
                layer=layer, net=net, net_code=nc,
            )
        ))
        candidates.append(ActionCandidate(
            element=RouteElement(
                element_type=ElementType.STRAIGHT,
                x1=x2, y1=y1, x2=x2, y2=y2,
                layer=layer, net=net, net_code=nc,
            )
        ))

        # L-shape (vertical then horizontal)
        candidates.append(ActionCandidate(
            element=RouteElement(
                element_type=ElementType.STRAIGHT,
                x1=x1, y1=y1, x2=x1, y2=y2,
                layer=layer, net=net, net_code=nc,
            )
        ))
        candidates.append(ActionCandidate(
            element=RouteElement(
                element_type=ElementType.STRAIGHT,
                x1=x1, y1=y2, x2=x2, y2=y2,
                layer=layer, net=net, net_code=nc,
            )
        ))

        return candidates

    def _gen_diagonal(self, x1, y1, x2, y2, layer, net, nc):
        """Generate 45° diagonal segments."""
        candidates = []

        # Direct 45° diagonal (if endpoints align)
        dx = x2 - x1
        dy = y2 - y1
        if abs(abs(dx) - abs(dy)) < 0.01:
            # Perfect 45°
            candidates.append(ActionCandidate(
                element=RouteElement(
                    element_type=ElementType.DIAGONAL_45,
                    x1=x1, y1=y1, x2=x2, y2=y2,
                    layer=layer, net=net, net_code=nc,
                )
            ))

        # Staircase (45° + straight)
        if abs(dx) > abs(dy):
            # Horizontal dominant
            mid_x = x1 + math.copysign(abs(dy), dx)
            candidates.append(ActionCandidate(
                element=RouteElement(
                    element_type=ElementType.DIAGONAL_45,
                    x1=x1, y1=y1, x2=mid_x, y2=y2,
                    layer=layer, net=net, net_code=nc,
                )
            ))
            candidates.append(ActionCandidate(
                element=RouteElement(
                    element_type=ElementType.STRAIGHT,
                    x1=mid_x, y1=y2, x2=x2, y2=y2,
                    layer=layer, net=net, net_code=nc,
                )
            ))
        else:
            # Vertical dominant
            mid_y = y1 + math.copysign(abs(dx), dy)
            candidates.append(ActionCandidate(
                element=RouteElement(
                    element_type=ElementType.DIAGONAL_45,
                    x1=x1, y1=y1, x2=x2, y2=mid_y,
                    layer=layer, net=net, net_code=nc,
                )
            ))
            candidates.append(ActionCandidate(
                element=RouteElement(
                    element_type=ElementType.STRAIGHT,
                    x1=x2, y1=mid_y, x2=x2, y2=y2,
                    layer=layer, net=net, net_code=nc,
                )
            ))

        return candidates

    def _gen_vias(self, x, y, from_layer, to_layer, net, nc):
        """Generate via candidates."""
        candidates = []

        # Through via (if going through all layers)
        if abs(to_layer - from_layer) == self.nl - 1:
            candidates.append(ActionCandidate(
                element=RouteElement(
                    element_type=ElementType.VIA_THROUGH,
                    x1=x, y1=y, x2=x, y2=y,
                    layer=from_layer, target_layer=to_layer,
                    net=net, net_code=nc,
                )
            ))

        # Blind via (top to inner or inner to bottom)
        if (from_layer == 0 and to_layer < self.nl - 1) or \
           (to_layer == self.nl - 1 and from_layer > 0):
            candidates.append(ActionCandidate(
                element=RouteElement(
                    element_type=ElementType.VIA_BLIND,
                    x1=x, y1=y, x2=x, y2=y,
                    layer=from_layer, target_layer=to_layer,
                    net=net, net_code=nc,
                )
            ))

        # Buried via (inner to inner)
        if from_layer > 0 and to_layer < self.nl - 1:
            candidates.append(ActionCandidate(
                element=RouteElement(
                    element_type=ElementType.VIA_BURIED,
                    x1=x, y1=y, x2=x, y2=y,
                    layer=from_layer, target_layer=to_layer,
                    net=net, net_code=nc,
                )
            ))

        # Microvia (HDI, adjacent layers only)
        if abs(to_layer - from_layer) == 1:
            candidates.append(ActionCandidate(
                element=RouteElement(
                    element_type=ElementType.VIA_MICRO,
                    x1=x, y1=y, x2=x, y2=y,
                    layer=from_layer, target_layer=to_layer,
                    net=net, net_code=nc,
                )
            ))

        return candidates

    def _gen_diff_pair(self, x1, y1, x2, y2, layer, net, nc, gap):
        """Generate differential pair segments (coupled)."""
        candidates = []

        # Two parallel segments with gap
        # Positive polarity
        candidates.append(ActionCandidate(
            element=RouteElement(
                element_type=ElementType.DIFF_PAIR,
                x1=x1, y1=y1, x2=x2, y2=y2,
                layer=layer, gap=gap, polarity=0,
                net=net, net_code=nc,
            )
        ))

        # Negative polarity (offset by gap)
        dx = x2 - x1
        dy = y2 - y1
        length = math.hypot(dx, dy)
        if length > 0:
            # Perpendicular offset
            px, py = -dy/length * gap, dx/length * gap
            candidates.append(ActionCandidate(
                element=RouteElement(
                    element_type=ElementType.DIFF_PAIR,
                    x1=x1+px, y1=y1+py, x2=x2+px, y2=y2+py,
                    layer=layer, gap=gap, polarity=1,
                    net=net, net_code=nc,
                )
            ))

        return candidates

    def _score_element(self, element, src_pos, dst_pos, constraints):
        """Score a routing element (higher = better)."""
        score = 0.0

        # Wirelength penalty (shorter = better)
        dist = math.hypot(element.x2 - element.x1, element.y2 - element.y1)
        score -= dist * 1.0

        # Via penalty (fewer vias = better)
        if element.element_type in (ElementType.VIA_THROUGH, ElementType.VIA_BLIND,
                                     ElementType.VIA_BURIED, ElementType.VIA_MICRO):
            score -= 10.0

        # 45° bonus (preferred for manufacturing)
        if element.element_type == ElementType.DIAGONAL_45:
            score += 2.0

        # Diff pair bonus (if required)
        if element.element_type == ElementType.DIFF_PAIR and constraints.get('diff_pair'):
            score += 5.0

        # Microvia penalty (expensive)
        if element.element_type == ElementType.VIA_MICRO:
            score -= 20.0

        return score


def generate_route_elements(src_pos, dst_pos, src_layer, dst_layer,
                            net, nc, constraints=None):
    """Generate routing elements for a connection.

    Args:
        src_pos: (x, y) source position
        dst_pos: (x, y) destination position
        src_layer: source layer index
        dst_layer: destination layer index
        net: net name
        nc: net code
        constraints: dict of constraints

    Returns:
        list of RouteElement
    """
    space = ElementActionSpace(None, [0, 1])  # dummy board for generation
    candidates = space.generate_candidates(
        src_pos, dst_pos, src_layer, dst_layer, net, nc, constraints)
    return [c.element for c in candidates if c.valid]
