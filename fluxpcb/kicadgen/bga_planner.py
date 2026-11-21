#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bga_planner — BGA global routing planner (PCBRouteAgent-inspired).

Implements the LLM global planning phase:
- MST topology: multi-pin nets → MST decomposition to 2-pin tasks
- Corridors: axis-aligned bounding box per layer (expanded 1.5mm)
- Via regions: layer transition points with expanded bounding boxes
- Routing order: MST total length, shortest first
- Output: RouteGuidePlan JSON (connections + corridors + via_regions + order)

Based on:
- PCBRouteAgent (arXiv 2611.08175v2): LLM global planning + PNS local execution
- FanoutNet (AAAI 2023): DRL fanout + multi-constraint router
- Quilter: automatic fanout + via pattern selection

Usage:
    from bga_planner import BGAGlobalPlanner
    planner = BGAGlobalPlanner(board)
    plan = planner.plan()
"""
import json
import math
from dataclasses import dataclass, field
from pathlib import Path

MM = 1e6
CORRIDOR_MARGIN = 1.5  # mm, PCBRouteAgent uses δ=1.5mm


@dataclass
class ConnectionTask:
    """A single 2-pin connection task."""
    net_name: str
    src_x: float
    src_y: float
    src_layer: int
    dst_x: float
    dst_y: float
    dst_layer: int
    distance: float = 0.0
    priority: int = 0  # 0=highest (shortest first)


@dataclass
class Corridor:
    """Axis-aligned bounding box corridor per layer."""
    layer: int
    x_min: float
    y_min: float
    x_max: float
    y_max: float
    margin: float = CORRIDOR_MARGIN


@dataclass
class ViaRegion:
    """Via placement region at layer transition."""
    x: float
    y: float
    radius: float = 1.0  # mm, expanded bounding box
    from_layer: int = 0
    to_layer: int = 1


@dataclass
class RouteGuidePlan:
    """Complete routing guide plan."""
    connections: list = field(default_factory=list)
    corridors: list = field(default_factory=list)
    via_regions: list = field(default_factory=list)
    routing_order: list = field(default_factory=list)  # list of connection indices


class BGAGlobalPlanner:
    """BGA global routing planner (PCBRouteAgent-inspired)."""

    def __init__(self, board):
        self.board = board
        self.b = board  # pcbnew.BOARD

    def plan(self):
        """Generate RouteGuidePlan for all nets."""
        # Step 1: Extract nets and pads
        net_pads = self._extract_net_pads()

        # Step 2: MST decomposition for multi-pin nets
        tasks = self._mst_decompose(net_pads)

        # Step 3: Generate corridors for each task
        corridors = self._gen_corridors(tasks)

        # Step 4: Generate via regions for layer transitions
        via_regions = self._gen_via_regions(tasks)

        # Step 5: Sort by distance (shortest first)
        tasks.sort(key=lambda t: t.distance)
        for i, t in enumerate(tasks):
            t.priority = i

        plan = RouteGuidePlan(
            connections=tasks,
            corridors=corridors,
            via_regions=via_regions,
            routing_order=list(range(len(tasks))),
        )

        return plan

    def _extract_net_pads(self):
        """Extract net-to-pads mapping from board."""
        net_pads = {}
        for fp in self.b.GetFootprints():
            for pad in fp.Pads():
                nc = pad.GetNetCode()
                if nc == 0:
                    continue
                nn = pad.GetNetname()
                if nn not in net_pads:
                    net_pads[nn] = []
                p = pad.GetPosition()
                pad_lid = pad.GetLayer()
                # Get layer index
                try:
                    lid = self.b.GetLayerName(pad_lid)
                except Exception:
                    lid = 'F.Cu'
                net_pads[nn].append({
                    'x': p.x / MM,
                    'y': p.y / MM,
                    'layer': lid,
                    'ref': fp.GetReference(),
                })
        return net_pads

    def _mst_decompose(self, net_pads):
        """Decompose multi-pin nets into 2-pin tasks using MST."""
        tasks = []
        for nn, pads in net_pads.items():
            if len(pads) < 2:
                continue

            if len(pads) == 2:
                # Direct connection
                p1, p2 = pads
                dist = math.hypot(p2['x'] - p1['x'], p2['y'] - p1['y'])
                tasks.append(ConnectionTask(
                    net_name=nn,
                    src_x=p1['x'], src_y=p1['y'], src_layer=0,
                    dst_x=p2['x'], dst_y=p2['y'], dst_layer=0,
                    distance=dist,
                ))
            else:
                # MST decomposition
                n = len(pads)
                visited = {0}
                edges = []
                while len(visited) < n:
                    best = None
                    for i in visited:
                        for j in range(n):
                            if j in visited:
                                continue
                            d = math.hypot(
                                pads[i]['x'] - pads[j]['x'],
                                pads[i]['y'] - pads[j]['y'])
                            if best is None or d < best[0]:
                                best = (d, i, j)
                    if best is None:
                        break
                    _, i, j = best
                    edges.append((i, j))
                    visited.add(j)

                for i, j in edges:
                    p1, p2 = pads[i], pads[j]
                    dist = math.hypot(p2['x'] - p1['x'], p2['y'] - p1['y'])
                    tasks.append(ConnectionTask(
                        net_name=nn,
                        src_x=p1['x'], src_y=p1['y'], src_layer=0,
                        dst_x=p2['x'], dst_y=p2['y'], dst_layer=0,
                        distance=dist,
                    ))

        return tasks

    def _gen_corridors(self, tasks):
        """Generate corridors (axis-aligned bounding boxes) per task."""
        corridors = []
        for task in tasks:
            # Bounding box with margin
            x_min = min(task.src_x, task.dst_x) - CORRIDOR_MARGIN
            y_min = min(task.src_y, task.dst_y) - CORRIDOR_MARGIN
            x_max = max(task.src_x, task.dst_x) + CORRIDOR_MARGIN
            y_max = max(task.src_y, task.dst_y) + CORRIDOR_MARGIN

            corridor = Corridor(
                layer=0,  # default layer
                x_min=x_min,
                y_min=y_min,
                x_max=x_max,
                y_max=y_max,
            )
            corridors.append(corridor)

        return corridors

    def _gen_via_regions(self, tasks):
        """Generate via regions at layer transitions."""
        via_regions = []
        for task in tasks:
            # If source and destination are on different layers
            if task.src_layer != task.dst_layer:
                # Via at midpoint
                mid_x = (task.src_x + task.dst_x) / 2
                mid_y = (task.src_y + task.dst_y) / 2
                via_region = ViaRegion(
                    x=mid_x,
                    y=mid_y,
                    radius=CORRIDOR_MARGIN,
                    from_layer=task.src_layer,
                    to_layer=task.dst_layer,
                )
                via_regions.append(via_region)

        return via_regions

    def to_json(self):
        """Export plan as JSON."""
        plan = self.plan()
        return {
            'connections': [
                {
                    'net': t.net_name,
                    'src': [round(t.src_x, 3), round(t.src_y, 3), t.src_layer],
                    'dst': [round(t.dst_x, 3), round(t.dst_y, 3), t.dst_layer],
                    'distance': round(t.distance, 3),
                    'priority': t.priority,
                }
                for t in plan.connections
            ],
            'corridors': [
                {
                    'layer': c.layer,
                    'bbox': [round(c.x_min, 3), round(c.y_min, 3),
                             round(c.x_max, 3), round(c.y_max, 3)],
                    'margin': c.margin,
                }
                for c in plan.corridors
            ],
            'via_regions': [
                {
                    'x': round(v.x, 3),
                    'y': round(v.y, 3),
                    'radius': v.radius,
                    'from_layer': v.from_layer,
                    'to_layer': v.to_layer,
                }
                for v in plan.via_regions
            ],
            'routing_order': plan.routing_order,
            'total_connections': len(plan.connections),
            'total_via_regions': len(plan.via_regions),
        }


def plan_bga_routing(board_path):
    """Plan BGA routing for a board file.

    Args:
        board_path: path to .kicad_pcb file

    Returns:
        dict: RouteGuidePlan JSON
    """
    import pcbnew
    b = pcbnew.LoadBoard(str(board_path))
    b.BuildConnectivity()
    planner = BGAGlobalPlanner(b)
    return planner.to_json()


if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print("Usage: python bga_planner.py BOARD.kicad_pcb")
        sys.exit(1)
    result = plan_bga_routing(sys.argv[1])
    print(json.dumps(result, indent=2))
