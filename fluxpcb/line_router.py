#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""line_router — Simple line routing engine.

Draws straight lines between pads. Very fast but doesn't avoid obstacles.
Good for getting a baseline completion percentage.

Usage:
    from fluxpcb.line_router import LineRouter
    result = LineRouter().route(board_path, timeout_s=10)
"""
from __future__ import annotations

import math
import time
from typing import Any, Optional


class LineRouter:
    """Simple line routing engine."""

    def __init__(self):
        """Initialize line router."""
        self.track_width = 0.127  # mm

    def route(self, board_path: str, timeout_s: int = 10) -> dict:
        """Route a board using straight lines.

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

        # Draw lines
        routed = 0
        total = 0

        for nn, pads in net_pads.items():
            if len(pads) < 2:
                continue

            nc = pads[0][2]  # Net code

            for i in range(len(pads) - 1):
                total += 1
                x1, y1, _ = pads[i]
                x2, y2, _ = pads[i+1]

                # Draw track
                track = pcbnew.PCB_TRACK(b)
                track.SetStart(pcbnew.VECTOR2I(int(x1*1e6), int(y1*1e6)))
                track.SetEnd(pcbnew.VECTOR2I(int(x2*1e6), int(y2*1e6)))
                track.SetWidth(int(self.track_width*1e6))
                track.SetLayer(b.GetLayerID('F.Cu'))
                track.SetNet(b.FindNet(nc))
                b.Add(track)
                routed += 1

        elapsed = time.time() - t0

        # Save board
        output_path = board_path.replace('.kicad_pcb', '_line_routed.kicad_pcb')
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


def route_line(board_path: str, timeout_s: int = 10) -> dict:
    """Convenience function for line routing.

    Args:
        board_path: Path to .kicad_pcb file
        timeout_s: Timeout in seconds

    Returns:
        dict with routing results
    """
    router = LineRouter()
    return router.route(board_path, timeout_s)
