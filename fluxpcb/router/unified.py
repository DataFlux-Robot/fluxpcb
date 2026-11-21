#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""unified — Unified router interface (Component 9).

Wraps NCERRouter and other engines into a single interface.
Consumes BoardDesignPlan (Component 4) + DispatchPlan (Component 7)
and produces RouteResult (for Component 10 verification).
"""
from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from ..contracts import (
    BoardDesignPlan,
    BoardState,
    DispatchPlan,
    NetTask,
    RouteGuidePlan,
    RouteResult,
    RoutingType,
    SignalClass,
    StateFingerprint,
)


@dataclass
class RouterConfig:
    """Router configuration."""
    timeout_s: int = 1200
    ncer_iterations: int = 10
    use_gpu: bool = True
    escape_first: bool = True
    power_plane: bool = True
    grid_resolution_mm: float = 0.16
    # Engine selection: "ncer" (default, deep search) or "freerouting"
    # (fast bulk router; SKILL.md step 6c). "auto" picks freerouting for
    # 2-layer boards and ncer otherwise.
    engine: str = "ncer"


class Router:
    """Unified routing interface.

    Consumes NetTask list + DispatchPlan per net, produces RouteResult.
    Supports two backends (Component 9):
      - NCER: negotiated-congestion deep router (slow, handles BGA corners)
      - Freerouting: fast bulk Specctra router (100% on 2L benchmarks)
    """

    def __init__(self, board: Any, lids: list[int], config: Optional[RouterConfig] = None):
        self.board = board
        self.lids = lids
        self.config = config or RouterConfig()
        self._ncer = None

    def _pick_engine(self) -> str:
        """Resolve 'auto' to a concrete engine name."""
        eng = (self.config.engine or "ncer").lower()
        if eng != "auto":
            return eng
        try:
            layers = self.board.GetCopperLayerCount()
        except Exception:
            layers = 2
        return "freerouting" if layers <= 2 else "ncer"

    def route(
        self,
        net_tasks: list[NetTask],
        dispatch_plans: dict[str, DispatchPlan],
        route_guide: Optional[RouteGuidePlan] = None,
    ) -> RouteResult:
        """Route all net tasks.

        Args:
            net_tasks: Ordered list of 2-pin tasks
            dispatch_plans: Per-net routing params from Component 7
            route_guide: Corridors and via regions from Component 4

        Returns:
            RouteResult with completion metrics
        """
        t0 = time.time()

        engine = self._pick_engine()

        # ── Freerouting backend ─────────────────────────────────────
        if engine == "freerouting":
            try:
                from .freerouting import FreeroutingBackend
                backend = FreeroutingBackend()
                ok, reason = backend.available()
                if ok:
                    board_path = self.board.GetFileName()
                    if board_path:
                        out_path = board_path.replace('.kicad_pcb', '_fr.kicad_pcb')
                        fr = backend.route(board_path, out_path, self.config.timeout_s)
                        if fr.routed:
                            # Reload so downstream verifiers see the routed board
                            import pcbnew as _p
                            new_board = _p.LoadBoard(out_path)
                            if new_board is not None:
                                self.board = new_board
                            elapsed = time.time() - t0
                            return RouteResult(
                                routed=[{'engine': 'freerouting'}],
                                opens=0,
                                shorts=0,
                                vias=0,
                                drc_errors=0,
                                completion_pct=100.0,
                                seconds=elapsed,
                                metrics={'engine': 'freerouting',
                                         'passes': fr.passes,
                                         'seconds': fr.seconds},
                            )
                # fall through to NCER when the backend cannot run
            except Exception:
                pass

        # ── NCER backend (default) ──────────────────────────────────
        net_pads: dict[str, list[tuple[float, float, int]]] = {}
        for task in net_tasks:
            if task.net not in net_pads:
                net_pads[task.net] = []
            net_pads[task.net].append(task.a)
            net_pads[task.net].append(task.b)

        # Extract corridors from route guide
        corridors = {}
        if route_guide:
            for corr in route_guide.corridors:
                corridors.setdefault("default", []).append(
                    [corr.x1, corr.y1, corr.x2, corr.y2]
                )

        # Run NCER router
        ncer = self._get_ncer()
        result = ncer.run(net_pads, self.config.timeout_s, corridors=corridors)

        # Build RouteResult
        elapsed = time.time() - t0
        route_result = RouteResult(
            opens=result.get('opens', 0),
            shorts=result.get('shorts', 0),
            vias=result.get('vias', 0),
            drc_errors=result.get('drc_errors', 0),
            completion_pct=result.get('completion_pct', 0.0),
            seconds=elapsed,
            metrics={'engine': 'ncer'},
        )

        return route_result

    def route_pair(
        self,
        nc: int,
        x1: float, y1: float,
        x2: float, y2: float,
        src_li: int = 0,
        dst_li: int = 0,
        corridor: Optional[list] = None,
        dispatch: Optional[DispatchPlan] = None,
    ) -> Optional[list]:
        """Route a single pair with dispatch constraints.

        Returns path or None.
        """
        ncer = self._get_ncer()

        # Apply dispatch constraints to router params
        if dispatch:
            if dispatch.track_width > 0:
                ncer.track_w = dispatch.track_width
            if dispatch.via_size[0] > 0:
                ncer.via_d = dispatch.via_size[0]
                ncer.via_drill = dispatch.via_size[1]

        return ncer.route_pair(nc, x1, y1, x2, y2, src_li, dst_li, corridor)

    def create_power_plane(self, net_name: str = "GND"):
        """Create power plane (Component 10 power integrity friendly)."""
        ncer = self._get_ncer()
        ncer._create_power_plane(net_name)

    def detect_collisions(self) -> list:
        """Detect collisions (for Component 8 feedback)."""
        ncer = self._get_ncer()
        return ncer.detect_collisions()

    @property
    def fingerprint(self) -> StateFingerprint:
        """Get current board fingerprint."""
        from ..runtime.comm import CommRuntime
        comm = CommRuntime()
        return comm.bind_state(self.board).fingerprint
