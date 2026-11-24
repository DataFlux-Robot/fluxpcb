#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""devloop — Meta-dev Loop (Component 1).

HarnessPro diagnose→evolve→select loop.
Improves the design method across runs and persists reusable strategies.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional

from ..contracts import (
    RoutingCandidate,
    RoutingDiagnosis,
    RoutingResult,
    Trajectory,
)


@dataclass
class MetaRunResult:
    """Result of a meta-dev run."""
    best_candidate: Optional[RoutingCandidate] = None
    best_result: Optional[RoutingResult] = None
    candidates_evaluated: int = 0
    seconds: float = 0.0
    strategy_saved: bool = False
    status: str = 'no_data'


class MetaDevLoop:
    """Meta-dev loop — diagnose→evolve→select.

    Improves the design method across runs.
    """

    def __init__(
        self,
        task: Any = None,
        optimizer: Any = None,
        store: Any = None,
    ):
        """Initialize meta-dev loop.

        Args:
            task: PCBTask instance
            optimizer: PCBProOptimizer instance
            store: StrategyStore instance
        """
        self.task = task
        self.optimizer = optimizer
        self.store = store
        self._history: list[RoutingResult] = []

    def diagnose(self, trajectories: list[Trajectory]) -> RoutingDiagnosis:
        """Diagnose routing failures from trajectories.

        Args:
            trajectories: List of execution trajectories

        Returns:
            RoutingDiagnosis with failure modes and suggestions
        """
        diagnosis = RoutingDiagnosis()

        if not trajectories:
            return diagnosis

        # Analyze last trajectory
        last = trajectories[-1]
        for event in last.events:
            opens = event.result.get('opens', 0)
            drc = event.result.get('drc_errors', 0)

            if opens > 0:
                diagnosis.failure_modes.append('opens')
                diagnosis.suggestions.append('escape_first')

            if drc > 0:
                diagnosis.failure_modes.append('drc_errors')
                # A meta layer may not lower a gate to make a board pass.
                # Propose a topology change (re-route) instead.
                diagnosis.suggestions.append('reroute_violating_nets')

        diagnosis.completion_pct = 100 - sum(
            e.result.get('opens', 0) for e in last.events
        )
        diagnosis.drc_errors = sum(
            e.result.get('drc_errors', 0) for e in last.events
        )

        return diagnosis

    def evolve(self, diagnosis: RoutingDiagnosis) -> list[RoutingCandidate]:
        """Generate candidates based on diagnosis.

        Args:
            diagnosis: RoutingDiagnosis

        Returns:
            List of RoutingCandidates to evaluate
        """
        candidates = []

        # Baseline
        candidates.append(RoutingCandidate(
            name='baseline',
            category='baseline',
            params={},
            expected_improvement=0.0,
            risk=0.0,
        ))

        # Based on failure modes
        if 'opens' in diagnosis.failure_modes:
            candidates.append(RoutingCandidate(
                name='escape_first',
                category='escape',
                params={'escape_first': True},
                expected_improvement=0.15,
                risk=0.1,
            ))

        if 'drc_errors' in diagnosis.failure_modes:
            candidates.append(RoutingCandidate(
                name='reroute_violating_nets',
                category='topology',
                params={'reroute_nets': True},
                expected_improvement=0.1,
                risk=0.2,
            ))

        return candidates

    def select(
        self,
        candidates: list[RoutingCandidate],
        board: Any = None,
        evaluator: Any = None,
    ) -> tuple[RoutingCandidate, RoutingResult]:
        """Select best candidate by evaluating.

        Args:
            candidates: List of candidates to evaluate
            board: Board to evaluate on

        Returns:
            (best_candidate, best_result)
        """
        best_candidate = None
        best_result = None

        if evaluator is None:
            # Refusing to fabricate: a meta layer that invents its own
            # scores cannot be wrong, which is the definition of decorative.
            raise RuntimeError(
                'MetaDevLoop.select requires a real evaluator; '
                'fabricated scores are not permitted')
        for candidate in candidates:
            result = evaluator(candidate)

            self._history.append(result)

            if best_result is None or result.score > best_result.score:
                best_candidate = candidate
                best_result = result

        return best_candidate, best_result

    def run(self, budget_s: int = 300) -> MetaRunResult:
        """Run the meta-dev loop.

        Args:
            budget_s: Time budget in seconds

        Returns:
            MetaRunResult with best strategy
        """
        t0 = time.time()
        result = MetaRunResult()

        # Diagnose from real history; with no trajectories there is nothing
        # to select, and the honest result is no_data, not a placeholder.
        trajectories = [r.trajectory for r in self._history
                        if getattr(r, 'trajectory', None)]
        if not trajectories:
            result.seconds = time.time() - t0
            result.status = 'no_data'
            return result
        diagnosis = self.diagnose(trajectories)

        # Evolve candidates
        candidates = self.evolve(diagnosis)
        result.candidates_evaluated = len(candidates)

        # Select best against a real evaluator (injected by the caller)
        if self.optimizer is None:
            result.seconds = time.time() - t0
            result.status = 'no_evaluator'
            return result
        best_candidate, best_result = self.select(
            candidates, evaluator=self.optimizer.evaluate_candidate)
        result.best_candidate = best_candidate
        result.best_result = best_result
        result.status = 'ok'

        # Save strategy
        if self.store and best_candidate:
            self.store.save(best_candidate.params, {'name': best_candidate.name})
            result.strategy_saved = True

        result.seconds = time.time() - t0
        return result
