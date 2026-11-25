#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pipeline — Complete 12-component routing pipeline.

All 12 components connected:
  1. MetaDevLoop (diagnose→evolve→select)          — after routing
  2. MetaMetaLoop (harness self-improvement)       — after routing
  3. DevRouteLoop (route→verify→repair)            — CORE LOOP
  4. PCBDesignAgent (stackup/placement/routing)    — before routing
  5. CommRuntime (state binding)                   — spine
  6. SubagentTeam (classification + ordering)      — inside DevRouteLoop
  7. DispatchRuntime (LLM+rules)                   — inside DevRouteLoop
  8. FeedbackRuntime (feedback + repair)           — inside DevRouteLoop
  9. Router (NCER + fallbacks)                     — inside DevRouteLoop
  10. VerifierSuite (PI/SI/Thermal/SPICE/EMC)      — inside DevRouteLoop
  11. ManufacturingInspector (DFM + fab)           — after routing
  12. GroundingMap (visualization)                 — final output

Usage:
    from fluxpcb.pipeline import RoutePipeline
    result = RoutePipeline().route(board_path, timeout_s=300)
"""
from __future__ import annotations

import os
import sys
import time
from typing import Any, Optional

from .contracts import (
    BoardDesignPlan,
    BoardState,
    DispatchPlan,
    ManufacturingReport,
    NetTask,
    RouteResult,
    Trajectory,
    VerificationReport,
)


class RoutePipeline:
    """Complete 12-component routing pipeline."""

    def __init__(self, config: Optional[dict] = None):
        """Initialize the pipeline.

        Args:
            config: Optional configuration dict
        """
        self.config = config or {}
        self.comm = None
        self.subagents = None
        self.dispatch = None
        self.router = None
        self.verifiers = None
        self.feedback = None
        self.design_agent = None
        self.dev_loop = None
        self.meta_loop = None
        self.meta_meta = None
        self.mfg_inspector = None
        self.grounding = None
        self._active_components = set()
        self._initialize_components()

    def _initialize_components(self):
        """Initialize all 12 components."""
        # Component 5: Communication runtime
        from .runtime.comm import CommRuntime
        self.comm = CommRuntime()
        self._active_components.add(5)

        # Component 6: Subagents
        from .subagents.team import SubagentTeam
        self.subagents = SubagentTeam()
        self._active_components.add(6)

        # Component 7: Dispatch runtime
        from .dispatch.dispatcher import DispatchRuntime
        self.dispatch = DispatchRuntime(comm=self.comm)
        self._active_components.add(7)

        # Component 8: Feedback runtime
        from .feedback.feedback_runtime import FeedbackRuntime
        self.feedback = FeedbackRuntime(comm=self.comm)
        self._active_components.add(8)

        # Component 10: Verifiers (with repair operators)
        from .verifiers.suite import VerifierSuite
        from .verifiers.power_integrity import PowerIntegrityVerifier
        from .verifiers.signal_integrity import SignalIntegrityVerifier
        from .verifiers.thermal import ThermalVerifier
        from .verifiers.spice import SpiceVerifier
        from .verifiers.emc import EMCVerifier
        self.verifiers = VerifierSuite([
            PowerIntegrityVerifier(),
            SignalIntegrityVerifier(),
            ThermalVerifier(),
            SpiceVerifier(),
            EMCVerifier(),
        ], comm=self.comm)
        self._active_components.add(10)

    def get_component_status(self) -> dict:
        """Get the status of all 12 components.

        Returns:
            dict mapping component number to status
        """
        from .meta.devloop import MetaDevLoop
        from .meta.meta_loop import MetaMetaLoop
        from .devloop.route_loop import DevRouteLoop
        from .design_agent.pcb_design_agent import PCBDesignAgent
        from .mfg.inspector import ManufacturingInspector
        from .viz.grounding_map import GroundingMap

        return {
            1: 'connected' if self.meta_loop is not None else 'available',
            2: 'connected' if self.meta_meta is not None else 'available',
            3: 'connected' if self.dev_loop is not None else 'available',
            4: 'connected' if self.design_agent is not None else 'available',
            5: 'connected' if self.comm is not None else 'missing',
            6: 'connected' if self.subagents is not None else 'missing',
            7: 'connected' if self.dispatch is not None else 'missing',
            8: 'connected' if self.feedback is not None else 'missing',
            9: 'connected' if self.router is not None else 'available',
            10: 'connected' if self.verifiers is not None else 'missing',
            11: 'connected' if self.mfg_inspector is not None else 'available',
            12: 'connected' if self.grounding is not None else 'available',
        }

    def route(self, board_path: str, timeout_s: int = 300) -> dict:
        """Route a board using the complete 12-component pipeline.

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

        # Component 5: Bind state
        binding = self.comm.bind_state(b)
        fingerprint = binding.fingerprint

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
                pad_lid = pad.GetLayer()
                net_pads[nn].append((p.x/1e6, p.y/1e6, 0))

        # Create board state
        board_state = BoardState(
            board=b,
            fingerprint=fingerprint,
            net_pads=net_pads,
            board_path=board_path,
        )

        # Component 4: Design agent (stackup/placement/routing plan)
        from .design_agent.pcb_design_agent import PCBDesignAgent
        self.design_agent = PCBDesignAgent(self.config.get('spec', {}), board_state)
        design_plan = self.design_agent.plan()
        self._active_components.add(4)

        # Component 9: Router (unified wrapper with route(tasks, plans, guide) interface)
        import sys as _sys
        _sys.path.insert(0, 'E:/DATA/vscode/ZCODE/flux-neurotransmitter/fluxpcb/kicadgen')
        _sys.path.insert(0, 'E:/DATA/vscode/ZCODE/autorouter-bench/tools')
        import routegrid as rg
        lids = rg.signal_layer_ids(b)

        from .router.unified import Router, RouterConfig
        engine = self.config.get('engine', 'auto')
        self.router = Router(b, lids, RouterConfig(timeout_s=timeout_s, engine=engine))
        self._active_components.add(9)

        # Component 3: DevRouteLoop (CORE: route→verify→repair)
        from .devloop.route_loop import DevRouteLoop
        self.dev_loop = DevRouteLoop(
            board_state=board_state,
            design_plan=design_plan,
            comm=self.comm,
            subagents=self.subagents,
            dispatch=self.dispatch,
            router=self.router,
            verifiers=self.verifiers,
            feedback=self.feedback,
        )
        self._active_components.add(3)

        # Run the dev loop (route→verify→repair until convergence)
        dev_result = self.dev_loop.run(max_iter=5)

        # Extract routing results
        if dev_result.route_result:
            result_dict = {
                'routed_pairs': len(dev_result.route_result.routed),
                'opens': dev_result.route_result.opens,
                'vias': dev_result.route_result.vias,
                'completion_pct': dev_result.route_result.completion_pct,
            }
        else:
            # Fallback: run NCER directly if dev loop produced nothing
            from .router.ncer import NCERRouter as _NCER
            _ncer = _NCER(b, lids)
            result_dict = _ncer.run(net_pads, timeout_s)

        # Calculate completion
        routed_pairs = result_dict.get('routed_pairs', len(dev_result.route_result.routed) if dev_result.route_result else 0)
        total_expected = sum(max(0, len(pads)-1) for pads in net_pads.values() if len(pads) >= 2)
        completion_pct = 100.0 * routed_pairs / total_expected if total_expected > 0 else 0.0

        # Component 8: Feedback (already used in dev loop, summarize)
        feedback_event = self.feedback.observe(
            action={'pipeline': 'route'},
            result={'opens': total_expected - routed_pairs, 'shorts': 0, 'drc_errors': 0},
        )

        # Component 10: Verify + repair (skill-package operators)
        # run_and_repair = verify → classify morphology → dispatch the right
        # operator (copper_text_to_silk / width_cascade / policy_waiver /
        # plane_bridge / topology_swap / endpoint_attach)
        if dev_result.verification and dev_result.converged:
            verification = dev_result.verification
        else:
            verification = self.verifiers.run_and_repair(board_state, design_plan)

        # Component 11: ManufacturingInspector (DFM + fab)
        from .mfg.inspector import ManufacturingInspector
        from .mfg.gerber import GerberExporter
        self.mfg_inspector = ManufacturingInspector()
        self._active_components.add(11)

        # Save board first (inspector needs the file)
        output_path = board_path.replace('.kicad_pcb', '_routed.kicad_pcb')
        try:
            b.Save(output_path)
            board_state.board_path = output_path
        except Exception:
            pass

        mfg_report = self.mfg_inspector.inspect(board_state, design_plan)

        # Component 1: MetaDevLoop (diagnose→evolve→select)
        from .meta.devloop import MetaDevLoop
        from .meta.strategy_store import StrategyStore
        try:
            self.meta_loop = MetaDevLoop(
                store=StrategyStore(),
            )
            # Hand the loop what it can read: this board's rounds as
            # packets.  Before this it was called with packets=None and
            # returned status='no_data' on every board.
            meta_result = self.meta_loop.run(
                budget_s=min(300, int(timeout_s * 0.1)),
                packets=_packets_of(dev_result))
            self._active_components.add(1)
        except Exception as e:
            meta_result = {'error': str(e)}

        # Component 2: MetaMetaLoop (harness self-improvement)
        from .meta.meta_loop import MetaMetaLoop
        try:
            self.meta_meta = MetaMetaLoop()
            # a generation it can read; the Trajectory object it used to
            # get has no as_dict and was silently dropped
            gens = []
            if isinstance(meta_result, dict) and meta_result.get('status'):
                gens.append({'results': [meta_result],
                             'proposals': meta_result.get('proposals') or [],
                             'diagnosis': meta_result.get('diagnosis') or {}})
            harness_diff = self.meta_meta.improve(gens)
            self._active_components.add(2)
        except Exception as e:
            harness_diff = {'error': str(e)}

        # Component 12: GroundingMap (visualization)
        from .viz.grounding_map import GroundingMap
        try:
            self.grounding = GroundingMap()
            grounding_data = self.grounding.build(
                route_result=dev_result.route_result,
                verification=verification,
                mfg=mfg_report,
                trajectory=dev_result.trajectory,
            )
            grounding_path = self.grounding.render(grounding_data)
            self._active_components.add(12)
        except Exception as e:
            grounding_data = {'error': str(e)}
            grounding_path = None

        elapsed = time.time() - t0

        return {
            'completionPct': completion_pct,
            'unconnected': total_expected - routed_pairs,
            'total_pairs': total_expected,
            'routed_pairs': routed_pairs,
            'vias': result_dict.get('vias', 0),
            'seconds': elapsed,
            'timedOut': elapsed > timeout_s,
            'fingerprint': fingerprint.to_dict(),
            'verification_passed': verification.passed,
            'manufacturing_passed': mfg_report.passed,
            'components_active': sorted(self._active_components),
            'components_status': self.get_component_status(),
            'dev_loop_iterations': dev_result.iterations,
            'dev_loop_converged': dev_result.converged,
            'grounding_map': grounding_path,
        }


def _packets_of(dev_result) -> list:
    """The loop's rounds as packets the meta loop can read.

    A FailurePacket wants rounds with decision/why/operators and a board
    fingerprint; `DevRouteLoop.run` now records exactly that per round in
    its trajectory events.  Returns None when there is nothing (the meta
    loop then says no_data, which is the truth).
    """
    if dev_result is None or not getattr(dev_result, 'trajectory', None):
        return None
    out = []
    for ev in dev_result.trajectory.events:
        act = (ev.action if hasattr(ev, 'action') else
               (ev.get('action') if isinstance(ev, dict) else {})) or {}
        plans = act.get('plans') or {}
        out.append({
            'board': getattr(dev_result, 'board_path', '') or '',
            'outcome': 'clean' if getattr(dev_result, 'converged', False) else 'fail',
            'rounds': [{'stage': 'route', 'mode': 'devloop',
                        'round': act.get('iteration'),
                        'decision': 'commit' if getattr(dev_result, 'converged', False)
                        else 'rollback',
                        'why': str((act.get('verification') or {}).get('passed')),
                        'operators': sorted(plans),
                        'decider': (plans.get('*') or {}).get('decider')
                        if isinstance(plans, dict) else None,
                        'board_fp': act.get('fingerprint') or ''}],
            'operator_trace': [],
            'instrument_ok': True,
            'wall_seconds': getattr(dev_result, 'seconds', None),
        })
    return out


def route_board(board_path: str, timeout_s: int = 300) -> dict:
    """Convenience function to route a board.

    Args:
        board_path: Path to .kicad_pcb file
        timeout_s: Timeout in seconds

    Returns:
        dict with routing results
    """
    pipeline = RoutePipeline()
    return pipeline.route(board_path, timeout_s)
