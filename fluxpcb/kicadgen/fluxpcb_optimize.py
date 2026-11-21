#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fluxpcb_optimize — HarnessPro OptimizeStrategy for PCB routing.

Implements the diagnose→evolve→select loop:
- Diagnose: analyze routing trajectories (completion, DRC, failure modes)
- Evolve: generate candidate improvements (algorithm, power plane, grid)
- Select: Pareto selection (completion × DRC × time)

Usage:
    from fluxpcb_optimize import PCBProOptimizer
    optimizer = PCBProOptimizer()
    result = optimizer.propose_candidates(
        agent=agent, harness=harness,
        tasks_with_trajectories=[(task, [traj1, traj2])],
        harness_store=store, traj_store=traj_store,
        workdir=workdir, n_samples=5, round_ix=0,
    )
"""
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path

MM = 1e6
sys.path.insert(0, str(Path(__file__).parent))


@dataclass
class RoutingDiagnosis:
    """Diagnosis of routing failure modes."""
    task_id: str
    completion_pct: float
    drc_errors: int
    unconnected: int
    failure_modes: dict = field(default_factory=dict)
    severity: float = 1.0
    suggestions: list = field(default_factory=list)


@dataclass
class RoutingCandidate:
    """A candidate routing strategy."""
    name: str
    description: str
    category: str  # 'algorithm', 'power_plane', 'grid', 'routing_order'
    params: dict = field(default_factory=dict)
    expected_improvement: str = ""
    risk: str = "low"


class PCBProOptimizer:
    """HarnessPro OptimizeStrategy for PCB routing."""

    def diagnose(self, trajectories, net_pads_by_task):
        """Analyze routing trajectories and identify failure modes."""
        diagnoses = []
        for traj in trajectories:
            failure_modes = {}
            suggestions = []

            # Mode 1: congested pads
            if hasattr(traj, 'metadata') and traj.metadata:
                congested = traj.metadata.get('congested_pads', 0)
                if congested > 0:
                    failure_modes['congested_pads'] = congested
                    suggestions.append('Use optimistic grid (margin=0) + exact judge')

            # Mode 2: power nets
            task_id = getattr(traj, 'task_id', '')
            net_pads = net_pads_by_task.get(task_id, {})
            power_nets = {nn: c for nn, c in net_pads.items() if c > 20}
            if power_nets:
                failure_modes['power_nets'] = sum(power_nets.values())
                suggestions.append('Pour power planes (GND, VCC)')

            # Mode 3: long pairs
            long_pairs = 0
            for nn, count in net_pads.items():
                if count > 2:
                    long_pairs += count // 3  # rough estimate
            if long_pairs > 5:
                failure_modes['long_pairs'] = long_pairs
                suggestions.append('Use Exact Wavefront (no iteration limit)')

            # Mode 4: high DRC
            drc = getattr(traj, 'drc_errors', 0)
            if drc > 20:
                failure_modes['high_drc'] = drc
                suggestions.append('Strengthen commit gate verification')

            completion = getattr(traj, 'completion_pct', 0.0)
            diagnosis = RoutingDiagnosis(
                task_id=task_id,
                completion_pct=completion,
                drc_errors=drc,
                unconnected=getattr(traj, 'unconnected', 0),
                failure_modes=failure_modes,
                severity=1.0 - completion / 100.0,
                suggestions=suggestions,
            )
            diagnoses.append(diagnosis)

        return diagnoses

    def propose_candidates(self, diagnoses, n_samples=5):
        """Generate candidate routing strategies based on diagnoses."""
        candidates = []

        # Always: baseline
        candidates.append(RoutingCandidate(
            name='baseline',
            description='Current NCER router with commit gate',
            category='algorithm',
            expected_improvement='Current state',
            risk='low',
        ))

        # Aggregate failure modes across diagnoses
        all_modes = {}
        for diag in diagnoses:
            for mode, count in diag.failure_modes.items():
                all_modes[mode] = all_modes.get(mode, 0) + count

        # If congested pads: optimistic grid
        if 'congested_pads' in all_modes:
            candidates.append(RoutingCandidate(
                name='optimistic_grid',
                description='Relaxed grid (margin=0) + exact judge',
                category='grid',
                params={'pad_margin': 0.0, 'track_margin': 0.0, 'use_exact_judge': True},
                expected_improvement='Escape congested pads',
                risk='medium',
            ))

        # If power nets: pour power planes
        if 'power_nets' in all_modes:
            candidates.append(RoutingCandidate(
                name='power_plane',
                description='Pour GND/VCC planes after routing',
                category='power_plane',
                params={'pour_gnd': True, 'pour_vcc': True, 'pour_after_routing': True},
                expected_improvement='Connect power nets through plane',
                risk='low',
            ))

        # If long pairs: exact wavefront
        if 'long_pairs' in all_modes:
            candidates.append(RoutingCandidate(
                name='exact_wavefront',
                description='Replace FIM with Exact Wavefront',
                category='algorithm',
                params={'use_wavefront': True, 'fim_iters': 999999},
                expected_improvement='Eliminate FIM iteration limit',
                risk='high',
            ))

        # If high DRC: stronger verification
        if 'high_drc' in all_modes:
            candidates.append(RoutingCandidate(
                name='strong_verification',
                description='Strengthen commit gate + exact judge',
                category='algorithm',
                params={'clearance_margin': 0.2, 'use_exact_judge': True},
                expected_improvement='Reduce DRC violations',
                risk='low',
            ))

        # Combined: best of all
        combined_params = {}
        for c in candidates:
            combined_params.update(c.params)
        candidates.append(RoutingCandidate(
            name='combined',
            description='All improvements combined',
            category='algorithm',
            params=combined_params,
            expected_improvement='Best overall',
            risk='high',
        ))

        return candidates[:n_samples]

    def select_pareto(self, results):
        """Pareto selection: completion × DRC × time."""
        if not results:
            return None

        # Score = completion% - DRC penalty - time penalty
        for r in results:
            drc_penalty = r.get('drc_errors', 0) * 0.1
            time_penalty = r.get('time_s', 0) / 100.0
            r['score'] = r.get('completion_pct', 0) - drc_penalty - time_penalty

        # Select best by score
        best = max(results, key=lambda r: r.get('score', 0))

        # Also select Pareto-optimal points
        pareto = []
        for r in results:
            dominated = False
            for r2 in results:
                if (r2.get('completion_pct', 0) >= r.get('completion_pct', 0) and
                    r2.get('drc_errors', 999) <= r.get('drc_errors', 999) and
                    r2.get('time_s', 999) <= r.get('time_s', 999) and
                    (r2.get('completion_pct', 0) > r.get('completion_pct', 0) or
                     r2.get('drc_errors', 999) < r.get('drc_errors', 999))):
                    dominated = True
                    break
            if not dominated:
                pareto.append(r)

        return {'best': best, 'pareto': pareto}
