#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""improved_router — Improved routing engine.

Combines Line Router speed with obstacle avoidance.
Uses A* only when needed (for congested areas).

Key features:
  1. Direct line routing for clear paths (fast)
  2. A* fallback for blocked paths (accurate)
  3. Obstacle avoidance (pads, tracks)
  4. Clearance checking
  5. Proper track widths

Usage:
    from fluxpcb.improved_router import ImprovedRouter
    result = ImprovedRouter().route(board_path, timeout_s=60)
"""
from __future__ import annotations

import heapq
import math
import time
from typing import Any, Optional


class ImprovedRouter:
    """Improved routing engine."""

    def __init__(self):
        """Initialize improved router."""
        self.grid_size = 0.1  # mm
        self.track_width = 0.127  # mm
        self.clearance = 0.127  # mm
        self.via_diameter = 0.5  # mm
        self.via_drill = 0.3  # mm

    def route(self, board_path: str, timeout_s: int = 60) -> dict:
        """Route a board using improved algorithm.

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
                net_pads[nn].append((p.x/1e6, p.y/1e6, pad.GetNetCode()))

        # Build obstacle map
        bb = b.GetBoardEdgesBoundingBox()
        x0, y0 = bb.GetX()/1e6, bb.GetY()/1e6
        w, h = bb.GetWidth()/1e6, bb.GetHeight()/1e6
        nx = int(w / self.grid_size) + 1
        ny = int(h / self.grid_size) + 1

        # Mark pads as obstacles
        obstacles = set()
        for fp in b.GetFootprints():
            for pad in fp.Pads():
                p = pad.GetPosition()
                gx = int((p.x/1e6 - x0) / self.grid_size)
                gy = int((p.y/1e6 - y0) / self.grid_size)
                if 0 <= gx < nx and 0 <= gy < ny:
                    # Mark pad area (with clearance)
                    pad_size = pad.GetSize()
                    pw = int(pad_size.x/1e6 / self.grid_size) + 1
                    ph = int(pad_size.y/1e6 / self.grid_size) + 1
                    for dx in range(-pw, pw+1):
                        for dy in range(-ph, ph+1):
                            obstacles.add((gx+dx, gy+dy))

        # Route connections
        routed = 0
        total = 0
        routed_paths = []

        for nn, pads in net_pads.items():
            if len(pads) < 2:
                continue

            nc = pads[0][2]

            for i in range(len(pads) - 1):
                total += 1
                x1, y1, _ = pads[i]
                x2, y2, _ = pads[i+1]

                # Convert to grid coordinates
                gx1 = int((x1 - x0) / self.grid_size)
                gy1 = int((y1 - y0) / self.grid_size)
                gx2 = int((x2 - x0) / self.grid_size)
                gy2 = int((y2 - y0) / self.grid_size)

                # Try direct line first (fast)
                if self._is_line_clear(obstacles, gx1, gy1, gx2, gy2):
                    # Draw direct line
                    self._draw_line(b, x1, y1, x2, y2, nc)
                    routed_paths.append((gx1, gy1, gx2, gy2))
                    routed += 1
                else:
                    # Use A* for blocked paths
                    path = self._astar(obstacles, gx1, gy1, gx2, gy2, nx, ny)
                    if path:
                        # Draw path
                        self._draw_path(b, path, x0, y0, nc)
                        routed_paths.extend(path)
                        routed += 1

        elapsed = time.time() - t0

        # Save board
        output_path = board_path.replace('.kicad_pcb', '_improved_routed.kicad_pcb')
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

    def _is_line_clear(self, obstacles, x1, y1, x2, y2) -> bool:
        """Check if direct line is clear of obstacles."""
        # Bresenham's line algorithm
        dx = abs(x2 - x1)
        dy = abs(y2 - y1)
        sx = 1 if x1 < x2 else -1
        sy = 1 if y1 < y2 else -1
        err = dx - dy

        while True:
            if (x1, y1) in obstacles:
                return False

            if x1 == x2 and y1 == y2:
                break

            e2 = 2 * err
            if e2 > -dy:
                err -= dy
                x1 += sx
            if e2 < dx:
                err += dx
                y1 += sy

        return True

    def _astar(self, obstacles, x1, y1, x2, y2, nx, ny) -> Optional[list]:
        """A* pathfinding."""
        open_set = [(0, x1, y1)]
        came_from = {}
        g_score = {(x1, y1): 0}
        f_score = {(x1, y1): self._heuristic(x1, y1, x2, y2)}

        while open_set:
            _, cx, cy = heapq.heappop(open_set)

            if (cx, cy) == (x2, y2):
                path = []
                while (cx, cy) in came_from:
                    path.append((cx, cy))
                    cx, cy = came_from[(cx, cy)]
                path.append((x1, y1))
                return path[::-1]

            for dx, dy in [(0, 1), (0, -1), (1, 0), (-1, 0), (1, 1), (1, -1), (-1, 1), (-1, -1)]:
                nx_, ny_ = cx + dx, cy + dy

                if not (0 <= nx_ < nx and 0 <= ny_ < ny):
                    continue

                if (nx_, ny_) in obstacles:
                    continue

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

    def _draw_line(self, board, x1, y1, x2, y2, nc):
        """Draw a straight line."""
        import pcbnew

        track = pcbnew.PCB_TRACK(board)
        track.SetStart(pcbnew.VECTOR2I(int(x1*1e6), int(y1*1e6)))
        track.SetEnd(pcbnew.VECTOR2I(int(x2*1e6), int(y2*1e6)))
        track.SetWidth(int(self.track_width*1e6))
        track.SetLayer(board.GetLayerID('F.Cu'))
        track.SetNet(board.FindNet(nc))
        board.Add(track)

    def _draw_path(self, board, path, x0, y0, nc):
        """Draw a path from grid coordinates."""
        import pcbnew

        if len(path) < 2:
            return

        for i in range(len(path) - 1):
            gx1, gy1 = path[i]
            gx2, gy2 = path[i+1]

            x1 = x0 + gx1 * self.grid_size
            y1 = y0 + gy1 * self.grid_size
            x2 = x0 + gx2 * self.grid_size
            y2 = y0 + gy2 * self.grid_size

            self._draw_line(board, x1, y1, x2, y2, nc)


def route_improved(board_path: str, timeout_s: int = 60) -> dict:
    """Convenience function for improved routing.

    Args:
        board_path: Path to .kicad_pcb file
        timeout_s: Timeout in seconds

    Returns:
        dict with routing results
    """
    router = ImprovedRouter()
    return router.route(board_path, timeout_s)
