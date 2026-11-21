#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""route_loop — Dev Route Loop (Component 3).

Per-board route→verify→repair convergence loop.

Orchestrates:
  1. Component 6 (SubagentTeam) — classify + order
  2. Component 7 (DispatchRuntime) — per-net params
  3. Component 9 (Router) — route
  4. Component 10 (VerifierSuite) — verify
  5. Component 8 (FeedbackRuntime) — feedback + repair

Converges when opens==0 and shorts==0 and drc==0.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional

from ..contracts import (
    BoardDesignPlan,
    BoardState,
    DispatchPlan,
    FeedbackEvent,
    NetTask,
    RouteResult,
    Trajectory,
    VerificationReport,
)


@dataclass
class DevLoopResult:
    """Result of the dev loop."""
    route_result: Optional[RouteResult] = None
    verification: Optional[VerificationReport] = None
    trajectory: Optional[Trajectory] = None
    iterations: int = 0
    converged: bool = False
    seconds: float = 0.0


class DevRouteLoop:
    """Dev Route Loop — per-board convergence loop.

    Orchestrates route→verify→repair until convergence.
    """

    def __init__(
        self,
        board_state: BoardState,
        design_plan: BoardDesignPlan,
        comm: Any = None,
        subagents: Any = None,
        dispatch: Any = None,
        router: Any = None,
        verifiers: Any = None,
        feedback: Any = None,
    ):
        """Initialize the dev loop.

        Args:
            board_state: Board state (Component 5)
            design_plan: Design plan (Component 4)
            comm: CommRuntime (Component 5)
            subagents: SubagentTeam (Component 6)
            dispatch: DispatchRuntime (Component 7)
            router: Router (Component 9)
            verifiers: VerifierSuite (Component 10)
            feedback: FeedbackRuntime (Component 8)
        """
        self.board_state = board_state
        self.design_plan = design_plan
        self.comm = comm
        self.subagents = subagents
        self.dispatch = dispatch
        self.router = router
        self.verifiers = verifiers
        self.feedback = feedback

    def run(self, max_iter: int = 5) -> DevLoopResult:
        """Run the dev loop until convergence.

        Args:
            max_iter: Maximum iterations

        Returns:
            DevLoopResult with final state
        """
        t0 = time.time()
        result = DevLoopResult()
        trajectory = Trajectory(board_hash=self.board_state.fingerprint.board_hash)

        for i in range(max_iter):
            result.iterations = i + 1

            # Step 1: Classify + order (Component 6)
            classification, tasks = self.subagents.dispatch(self.board_state, self.comm)

            # Step 2: Dispatch per-net params (Component 7)
            dispatch_plans = {}
            for task in tasks:
                plan = self.dispatch.dispatch(task)
                dispatch_plans[task.net] = plan

            # Step 3: Route (Component 9)
            route_result = self.router.route(
                tasks,
                dispatch_plans,
                self.design_plan.route_guide,
            )

            # Step 4: Verify (Component 10)
            verification = self.verifiers.run_all(self.board_state, self.design_plan)

            # Step 5: Feedback (Component 8)
            feedback_event = self.feedback.observe(
                action={'iteration': i, 'route': route_result},
                result=route_result,
            )
            trajectory.events.append(feedback_event)

            # Check convergence
            if (route_result.opens == 0 and
                    route_result.shorts == 0 and
                    route_result.drc_errors == 0):
                result.converged = True
                result.route_result = route_result
                result.verification = verification
                break

            # Repair: rip-up and reroute failed nets
            if self.feedback and route_result.opens > 0:
                self._repair(route_result, tasks, dispatch_plans)

        result.trajectory = trajectory
        result.seconds = time.time() - t0

        return result

    def _repair(
        self,
        route_result: RouteResult,
        tasks: list[NetTask],
        dispatch_plans: dict[str, DispatchPlan],
    ):
        """Repair failed nets by ripping up and rerouting."""
        # Simple repair: reroute nets with opens
        if self.feedback:
            suggestions = self.feedback.suggest_repairs(route_result)
            for suggestion in suggestions:
                # Apply suggestion to dispatch plans
                net = suggestion.get('net')
                if net in dispatch_plans:
                    dispatch_plans[net].escape_strategy = suggestion.get('strategy', '')
