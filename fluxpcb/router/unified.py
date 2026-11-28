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
    # Engine selection: "ncer" (default, deep search), "pathfinder", or "freerouting"
    engine: str = "ncer"
    allow_vippo: bool = False
    pf_mode: str = "keep"
    pf_max_iters: int = 24
    rules: Optional[dict] = None


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
        if layers >= 4:
            return "pathfinder"
        try:
            from .freerouting import FreeroutingBackend
            ok, _ = FreeroutingBackend().available()
            if ok:
                return "freerouting"
        except Exception:
            pass
        return "pathfinder"

    def _lower_dispatch_to_pf_cfg(
        self,
        dispatch_plans: dict[str, DispatchPlan],
        route_guide: Optional[RouteGuidePlan] = None,
    ) -> dict:
        """Lower Component 7 DispatchPlans into PathFinder domain cfg."""
        from pathlib import Path
        net_widths_mm: dict[str, float] = {}
        diff_pairs: list[dict] = []
        seen_pairs = set()
        for net, dp in (dispatch_plans or {}).items():
            if dp.routing_type == RoutingType.DIFF_PAIR and dp.diff_partner:
                pair_key = tuple(sorted((net, dp.diff_partner)))
                if pair_key not in seen_pairs:
                    seen_pairs.add(pair_key)
                    diff_pairs.append({
                        'name': f'{net}/{dp.diff_partner}',
                        'p': net,
                        'n': dp.diff_partner,
                        'w_mm': float(dp.track_width or 0.16),
                        's_mm': float(dp.diff_gap_mm or 0.18),
                        'max_skew_mm': float(dp.max_skew_mm or 0.5),
                        'z_target': float(dp.target_z_ohm or 90.0),
                        'prefer_layers': list(dp.layer_pref or [0]),
                    })
            elif dp.track_width and dp.track_width >= 0.24:
                # Only widen nets explicitly planned wider than standard signal traces
                # (e.g. power/ground trunks from Component 4/7 lowering).
                net_widths_mm[net] = float(dp.track_width)

        cfg: dict[str, Any] = {
            'mode': self.config.pf_mode or 'keep',
            'time_budget_s': min(240, max(30, int(self.config.timeout_s * 0.7))),
            'max_iters': int(self.config.pf_max_iters or 24),
            'allow_vippo': bool(self.config.allow_vippo),
            'diff_pairs': diff_pairs,
            'net_widths_mm': net_widths_mm,
        }
        if self.config.rules:
            cfg['rules'] = dict(self.config.rules)
        else:
            # Let SixLayerPathFinder read .kicad_pro directly when present; only fall
            # back to DesignSettings if there is no project file beside the board.
            bp = Path(self.board.GetFileName() or "")
            if not (bp.name and bp.with_suffix(".kicad_pro").exists()):
                try:
                    ds = self.board.GetDesignSettings()
                    if ds.m_MinClearance > 0 and ds.m_TrackMinWidth > 0:
                        cfg['rules'] = {
                            'clearance': int(ds.m_MinClearance),
                            'track_min': int(ds.m_TrackMinWidth),
                            'annular': int(ds.m_ViasMinAnnularWidth),
                            'drill_min': int(ds.m_MinThroughDrill),
                            'edge': int(ds.m_CopperEdgeClearance),
                            'via_min': int(ds.m_ViasMinSize),
                        }
                except Exception:
                    pass
        return cfg

    def route(
        self,
        net_tasks: list[NetTask],
        dispatch_plans: dict[str, DispatchPlan],
        route_guide: Optional[RouteGuidePlan] = None,
    ) -> RouteResult:
        """Route all net tasks, honoring Component 7 DispatchPlans."""
        t0 = time.time()

        engine = self._pick_engine()

        # ── PathFinder domain-aware backend (2L..6L+ BGA + diff-pair + power width) ──
        if engine == "pathfinder":
            from pathlib import Path
            kg = str(Path(__file__).resolve().parent.parent / "kicadgen")
            if kg not in sys.path:
                sys.path.insert(0, kg)
            import route_pathfinder as _rp
            board_path = Path(self.board.GetFileName() or "board.kicad_pcb")
            cfg = self._lower_dispatch_to_pf_cfg(dispatch_plans, route_guide)
            pf = _rp.SixLayerPathFinder(self.board, board_path, cfg)
            res = pf.solve()
            elapsed = time.time() - t0
            ob = int(res.get('open_before', 0))
            oa = int(res.get('open_after', 0))
            comp = 100.0 if ob == 0 else max(0.0, 100.0 * (ob - oa) / ob)
            return RouteResult(
                routed=[{'engine': 'pathfinder', 'committed_nets': res.get('committed_nets', 0)}],
                opens=oa,
                shorts=0,
                vias=int(res.get('vias_added', 0)),
                drc_errors=0,
                completion_pct=round(comp, 2),
                seconds=elapsed,
                fingerprint=self.fingerprint,
                metrics={'engine': 'pathfinder', **res},
            )

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
