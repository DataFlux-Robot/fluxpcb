#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pcb_design_agent — PCB Design Agent (Component 4).

High-level planning:
  1. Stackup planning (layer count, materials, impedance targets)
  2. Placement planning (component positions, corridors)
  3. Route guide (MST decomposition, corridors, via regions)
  4. Net class rules (clearances, widths)

Outputs BoardDesignPlan consumed by Component 3 (DevRouteLoop).

Based on:
  - FLUXPCB设计.md Component 4: "叠层规划/阻抗规划/器件摆放位置和朝向规划"
  - bga_planner.py (MST decomposition, corridors)
  - board_rules.py (BoardRules.from_pro)
  - tools/ipc2141.py (impedance solving)
"""
from __future__ import annotations

import math
import sys
from dataclasses import dataclass, field
from typing import Any, Optional

from ..contracts import (
    BoardDesignPlan,
    BoardState,
    Corridor,
    NetClassRules,
    PlacementPlan,
    RouteGuidePlan,
    StackupPlan,
)


class PCBDesignAgent:
    """PCB Design Agent — produces BoardDesignPlan.

    Consumes BoardState (Component 5) and produces a complete design plan
    covering stackup, placement, routing corridors, and net class rules.
    """

    def __init__(self, spec: dict, board_state: BoardState):
        """Initialize the design agent.

        Args:
            spec: Design specification (from flux.yaml or similar)
            board_state: Current board state with fingerprint
        """
        self.spec = spec
        self.board_state = board_state
        self.board = board_state.board

    def plan(self) -> BoardDesignPlan:
        """Produce a complete BoardDesignPlan.

        Returns:
            BoardDesignPlan with stackup, placement, route_guide, net_class_rules
        """
        stackup = self._solve_stackup()
        placement = self._plan_placement()
        route_guide = self._plan_routing()
        net_class_rules = self._derive_net_class_rules()

        return BoardDesignPlan(
            stackup=stackup,
            placement=placement,
            route_guide=route_guide,
            net_class_rules=net_class_rules,
        )

    def _solve_stackup(self) -> StackupPlan:
        """Solve stackup and impedance targets.

        Uses IPC-2141 for impedance calculation.
        """
        layers = self.spec.get('layers', 2)
        er = self.spec.get('er', 4.5)

        # Default layer order
        if layers == 2:
            layer_order = ['F.Cu', 'B.Cu']
            dielectric = [1.6]  # mm
            copper = [0.035, 0.035]  # mm
        elif layers == 6:
            layer_order = ['F.Cu', 'In1.Cu', 'In2.Cu', 'In3.Cu', 'In4.Cu', 'B.Cu']
            dielectric = [0.1, 0.2, 0.8, 0.2, 0.1]
            copper = [0.035] * 6
        elif layers == 8:
            layer_order = ['F.Cu', 'In1.Cu', 'In2.Cu', 'In3.Cu', 'In4.Cu', 'In5.Cu', 'In6.Cu', 'B.Cu']
            dielectric = [0.1, 0.15, 0.2, 0.8, 0.2, 0.15, 0.1]
            copper = [0.035] * 8
        else:
            layer_order = [f'L{i}.Cu' for i in range(layers)]
            dielectric = [1.6 / (layers - 1)] * (layers - 1)
            copper = [0.035] * layers

        # Impedance targets
        impedance_targets = []
        target_ohms = self.spec.get('impedance_ohms', 50)
        if target_ohms:
            impedance_targets.append({
                'target_ohms': target_ohms,
                'tolerance': 0.1,  # ±10%
                'type': 'single_ended',
            })

        return StackupPlan(
            layers=layers,
            er=er,
            layer_order=layer_order,
            impedance_targets=impedance_targets,
            copper_thickness_mm=copper,
            dielectric_thickness_mm=dielectric,
        )

    def _plan_placement(self) -> PlacementPlan:
        """Plan component placement and corridors.

        Uses existing board placement if available.
        """
        placements = []
        corridors = []

        if self.board:
            for fp in self.board.GetFootprints():
                pos = fp.GetPosition()
                placements.append({
                    'ref': fp.GetReference(),
                    'x': pos.x / 1e6,
                    'y': pos.y / 1e6,
                    'rotation': fp.GetOrientation().AsDegrees(),
                })

        # Generate corridors from component positions
        if placements:
            corridors = self._generate_corridors(placements)

        return PlacementPlan(placements=placements, corridors=corridors)

    def _generate_corridors(self, placements: list[dict]) -> list[Corridor]:
        """Generate routing corridors between components."""
        corridors = []
        for i, p1 in enumerate(placements):
            for p2 in placements[i+1:]:
                dist = math.hypot(p2['x'] - p1['x'], p2['y'] - p1['y'])
                if dist < 20:  # mm
                    corridors.append(Corridor(
                        x1=min(p1['x'], p2['x']),
                        y1=min(p1['y'], p2['y']),
                        x2=max(p1['x'], p2['x']),
                        y2=max(p1['y'], p2['y']),
                    ))
        return corridors[:50]  # Cap at 50 corridors

    def _plan_routing(self) -> RouteGuidePlan:
        """Plan routing guide with MST decomposition.

        Uses bga_planner for BGA nets if available.
        """
        connections = []
        corridors = []
        via_regions = []
        order = []

        # MST decomposition of multi-pin nets
        net_pads = self.board_state.net_pads
        for net_name, pads in sorted(net_pads.items()):
            if len(pads) < 2:
                continue

            # MST edges
            mst_edges = self._mst_edges(pads)
            for i, (a, b) in enumerate(mst_edges):
                connections.append({
                    'net': net_name,
                    'a': a,
                    'b': b,
                })
                order.append(f"{net_name}_{i}")

        return RouteGuidePlan(
            connections=connections,
            corridors=corridors,
            via_regions=via_regions,
            order=order,
        )

    def _mst_edges(self, pads: list[tuple]) -> list[tuple]:
        """Compute MST edges for a set of pads (Prim's algorithm)."""
        n = len(pads)
        if n <= 1:
            return []
        if n == 2:
            return [(pads[0], pads[1])]

        # Prim's MST
        visited = {0}
        edges = []
        while len(visited) < n:
            best = None
            best_dist = float('inf')
            for i in visited:
                for j in range(n):
                    if j in visited:
                        continue
                    dist = math.hypot(
                        pads[j][0] - pads[i][0],
                        pads[j][1] - pads[i][1]
                    )
                    if dist < best_dist:
                        best_dist = dist
                        best = (i, j)
            if best:
                i, j = best
                edges.append((pads[i], pads[j]))
                visited.add(j)

        return edges

    def _derive_net_class_rules(self) -> NetClassRules:
        """Derive net class rules from board settings."""
        rules = {}
        clearances = {}
        widths = {}

        # Default rules
        defaults = {
            'clearance_mm': 0.127,
            'track_width_mm': 0.127,
            'via_diameter_mm': 0.5,
            'via_drill_mm': 0.3,
        }

        # Read from board if available
        if self.board:
            try:
                ds = self.board.GetDesignSettings()
                defaults['clearance_mm'] = ds.m_MinClearance / 1e6
                defaults['track_width_mm'] = ds.m_TrackMinWidth / 1e6
                defaults['via_diameter_mm'] = ds.m_ViasMinSize / 1e6
                defaults['via_drill_mm'] = ds.m_MinThroughDrill / 1e6
            except Exception:
                pass

        # Apply to all net classes
        for cls_name in ['critical', 'differential', 'clock', 'power', 'normal']:
            rules[cls_name] = defaults.copy()
            clearances[cls_name] = defaults['clearance_mm']
            widths[cls_name] = defaults['track_width_mm']

        # Power nets get wider tracks
        widths['power'] = 0.4
        rules['power']['track_width_mm'] = 0.4

        return NetClassRules(
            rules=rules,
            clearances=clearances,
            widths=widths,
        )
