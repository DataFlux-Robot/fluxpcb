#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""greedy_router — Greedy routing engine.

Routes connections in order of difficulty (shortest first).
Uses optimized A* with bounding box optimization.

Key features:
  1. Sort connections by distance (shortest first)
  2. Bounding box optimization (limit search space)
  3. Early termination on timeout
  4. Progress tracking

Usage:
    from fluxpcb.greedy_router import GreedyRouter
    result = GreedyRouter().route(board_path, timeout_s=60)
"""
from __future__ import annotations

import heapq
import math
import time
from typing import Any, Optional


class GreedyRouter:
    """Greedy routing engine."""

    def __init__(self):
        """Initialize greedy router."""
        self.grid_size = 0.15  # mm
        self.track_width = 0.127  # mm
        self.clearance = 0.127  # mm
        self.max_search_radius = 100  # cells

    def route(self, board_path: str, timeout_s: int = 60) -> dict:
        """Route a board using greedy algorithm.

        Args:
            board_path: Path to .kicad_pcb file
            timeout_s: Timeout in seconds

        Returns:
            dict with routing results
        """
        t0 = time.time()

        # Load board
        import pcbnew
        b = pcbnew.LoadBoard(board_path)
        if b is None:
            return {'error': 'Failed to load board', 'completionPct': 0.0}

        b.BuildConnectivity()

        # Collect net pads
        net_pads = {}
        for fp in b.GetFootprints():
            for pad in fp.Pads():
                nc = pad.GetNetCode()
                if nc == 0:
                    continue
                nn = pad.GetNetname()
                if nn not in net_pads:
                    net_pads[nn] = []
                p = pad.GetPosition()
                net_pads[nn].append((p.x/1e6, p.y/1e6, 0))

        # Build connections list (sorted by distance)
        connections = []
        for nn, pads in net_pads.items():
            if len(pads) < 2:
                continue
            for i in range(len(pads) - 1):
                x1, y1, _ = pads[i]
                x2, y2, _ = pads[i+1]
                dist = math.hypot(x2-x1, y2-y1)
                connections.append((dist, nn, x1, y1, x2, y2))

        # Sort by distance (shortest first)
        connections.sort(key=lambda x: x[0])

        # Build grid
        bb = b.GetBoardEdgesBoundingBox()
        x0, y0 = bb.GetX()/1e6, bb.GetY()/1e6
        w, h = bb.GetWidth()/1e6, bb.GetHeight()/1e6
        nx = int(w / self.grid_size) + 1
        ny = int(h / self.grid_size) + 1

        # Build occupancy grid
        occupancy = [[[False]*ny for _ in range(nx)] for _ in range(2)]

        # Mark pads as obstacles
        for fp in b.GetFootprints():
            for pad in fp.Pads():
                p = pad.GetPosition()
                gx = int((p.x/1e6 - x0) / self.grid_size)
                gy = int((p.y/1e6 - y0) / self.grid_size)
                if 0 <= gx < nx and 0 <= gy < ny:
                    occupancy[0][gx][gy] = True
                    occupancy[1][gx][gy] = True

        # Route connections
        routed = 0
        total = len(connections)

        for dist, nn, x1, y1, x2, y2 in connections:
            # Check timeout
            if time.time() - t0 > timeout_s:
                break

            # Convert to grid coordinates
            gx1 = int((x1 - x0) / self.grid_size)
            gy1 = int((y1 - y0) / self.grid_size)
            gx2 = int((x2 - x0) / self.grid_size)
            gy2 = int((y2 - y0) / self.grid_size)

            # Route using A* with bounding box
            path = self._astar_bounded(
                occupancy, gx1, gy1, gx2, gy2, nx, ny,
                bounding_box=True
            )

            if path:
                # Mark path as occupied
                for gx, gy in path:
                    occupancy[0][gx][gy] = True
                routed += 1

        elapsed = time.time() - t0

        # Save board
        output_path = board_path.replace('.kicad_pcb', '_greedy_routed.kicad_pcb')
        try:
            b.Save(output_path)
        except Exception:
            pass

        completion_pct = 100.0 * routed / total if total > 0 else 0.0

        return {
            'completionPct': completion_pct,
            'unconnected': total - routed,
            'total_pairs': total,
            'routed_pairs': routed,
            'vias': 0,
            'seconds': elapsed,
            'timedOut': elapsed > timeout_s,
        }

    def _astar_bounded(self, occupancy, x1, y1, x2, y2, nx, ny,
                       bounding_box=True) -> Optional[list]:
        """A* with bounding box optimization.

        Limits search to a bounding box around start and end points.
        """
        if bounding_box:
            # Calculate bounding box with margin
            margin = self.max_search_radius
            min_x = max(0, min(x1, x2) - margin)
            max_x = min(nx-1, max(x1, x2) + margin)
            min_y = max(0, min(y1, y2) - margin)
            max_y = min(ny-1, max(y1, y2) + margin)
        else:
            min_x, max_x = 0, nx-1
            min_y, max_y = 0, ny-1

        # A* algorithm
        open_set = [(0, x1, y1)]
        came_from = {}
        g_score = {(x1, y1): 0}
        f_score = {(x1, y1): self._heuristic(x1, y1, x2, y2)}

        while open_set:
            _, cx, cy = heapq.heappop(open_set)

            if (cx, cy) == (x2, y2):
                # Reconstruct path
                path = []
                while (cx, cy) in came_from:
                    path.append((cx, cy))
                    cx, cy = came_from[(cx, cy)]
                path.append((x1, y1))
                return path[::-1]

            # Explore neighbors
            for dx, dy in [(0, 1), (0, -1), (1, 0), (-1, 0), (1, 1), (1, -1), (-1, 1), (-1, -1)]:
                nx_, ny_ = cx + dx, cy + dy

                # Check bounds (including bounding box)
                if not (min_x <= nx_ <= max_x and min_y <= ny_ <= max_y):
                    continue

                if not (0 <= nx_ < nx and 0 <= ny_ < ny):
                    continue

                if occupancy[0][nx_][ny_]:
                    continue

                # Cost
                move_cost = 1.414 if dx != 0 and dy != 0 else 1.0
                tentative_g = g_score[(cx, cy)] + move_cost

                if (nx_, ny_) not in g_score or tentative_g < g_score[(nx_, ny_)]:
                    came_from[(nx_, ny_)] = (cx, cy)
                    g_score[(nx_, ny_)] = tentative_g
                    f_score[(nx_, ny_)] = tentative_g + self._heuristic(nx_, ny_, x2, y2)
                    heapq.heappush(open_set, (f_score[(nx_, ny_)], nx_, ny_))

        return None

    def _heuristic(self, x1, y1, x2, y2) -> float:
        """Manhattan distance heuristic."""
        return abs(x1 - x2) + abs(y1 - y2)


def route_greedy(board_path: str, timeout_s: int = 60) -> dict:
    """Convenience function for greedy routing.

    Args:
        board_path: Path to .kicad_pcb file
        timeout_s: Timeout in seconds

    Returns:
        dict with routing results
    """
    router = GreedyRouter()
    return router.route(board_path, timeout_s)
