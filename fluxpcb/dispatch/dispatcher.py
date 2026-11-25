#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dispatcher — LLM+Rule Dispatch Runtime (Component 7).

Per-net routing params via LLM+rules.

Decision points:
  - bga_escape: BGA fanout strategy (dogbone vs via-in-pad vs staggered)
  - stackup: Layer assignment for multi-layer routing
  - stall_arbitration: When router is stuck, which nets to rip up
  - fanout_rebalance: Rebalance BGA fanout when channels are congested

Three-mode chain (consistent with kernel discipline):
  1. LLM → structured output (validated against schema)
  2. Rule fallback → deterministic (strategy_selector)
  3. Baseline → defaults

The Decision.planner field records which mode fired.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from ..contracts import (
    BGAFanoutStyle,
    DispatchPlan,
    NetTask,
    Planner,
    RoutingType,
    SignalClass,
    ViaType,
)


class DispatchRuntime:
    """LLM+Rule dispatch runtime.

    Produces DispatchPlan for each NetTask using LLM+rules.
    """

    def __init__(
        self,
        llm: Any = None,
        rules: Any = None,
        comm: Any = None,
    ):
        """Initialize dispatch runtime.

        Args:
            llm: LLMAdapter instance (optional, for online mode)
            rules: NetClassRules (from Component 4)
            comm: CommRuntime for state binding
        """
        self.llm = llm
        self.rules = rules
        self.comm = comm
        self._history: list[dict] = []

    def dispatch(self, task: NetTask, summary: dict = None) -> DispatchPlan:
        """Dispatch routing params for a NetTask.

        Three-mode chain: LLM → rules → baseline.

        Args:
            task: NetTask to dispatch
            summary: State summary from CommRuntime

        Returns:
            DispatchPlan with routing params
        """
        # Mode 1: Try LLM
        if self.llm:
            plan = self._try_llm(task, summary or {})
            if plan:
                return plan

        # Mode 2: Rule-based fallback
        plan = self._rule_dispatch(task)
        if plan:
            return plan

        # Mode 3: Baseline
        return self._baseline_dispatch(task)

    def _try_llm(self, task: NetTask, summary: dict) -> Optional[DispatchPlan]:
        """Try LLM decision (validated against rules)."""
        try:
            context = {
                'net': task.net,
                'class': task.cls.value,
                'a': task.a,
                'b': task.b,
                'summary': summary,
            }

            prompt = f"""Given this PCB routing task, decide the optimal routing parameters.
Net: {task.net}
Class: {task.cls.value}
Source: {task.a}
Dest: {task.b}

Return JSON with fields:
- via_type: "through"|"blind"|"buried"|"micro"
- via_size: [diameter_mm, drill_mm]
- track_width: mm
- routing_type: "normal"|"diff_pair"|"equal_length"|"power"
- bga_fanout_style: "dogbone"|"inline"|"via_in_pad"|"staggered"
"""

            # complete(system, prompt): the call used to pass one
            # argument, raise TypeError, and be swallowed — the LLM mode
            # had never run once
            response = self.llm.complete(
                "You are a PCB routing parameter planner. Answer with JSON "
                "only, and state nothing you cannot justify from the task.",
                prompt)
            if response and response.get('ok'):
                plan = self._parse_llm_response(task, response.get('text') or '')
                if self._validate_plan(plan):
                    plan.planner = Planner.LLM
                    plan.rationale = "LLM decision"
                    plan.decider = {
                        'kind': 'llm',
                        'id': 'dispatch',
                        'model': response.get('model') or '',
                        'provider': response.get('provider') or '',
                        'prompt_digest': response.get('prompt_digest') or '',
                    }
                    return plan
        except Exception:
            pass

        return None

    def _parse_llm_response(self, task: NetTask, response: str) -> DispatchPlan:
        """Parse LLM response into DispatchPlan."""
        try:
            data = json.loads(response)
        except (json.JSONDecodeError, TypeError):
            data = {}

        return DispatchPlan(
            net=task.net,
            via_type=ViaType(data.get('via_type', 'through')),
            via_size=tuple(data.get('via_size', [0.5, 0.3])),
            track_width=data.get('track_width', 0.127),
            routing_type=RoutingType(data.get('routing_type', 'normal')),
            bga_fanout_style=BGAFanoutStyle(data.get('bga_fanout_style', 'dogbone')),
            planner=Planner.LLM,
        )

    def _validate_plan(self, plan: DispatchPlan) -> bool:
        """Validate a plan against rules."""
        # Basic validation
        if plan.track_width < 0.09:  # JLC minimum
            return False
        if plan.via_size[0] < 0.4:  # JLC minimum via diameter
            return False
        if plan.via_size[1] < 0.2:  # JLC minimum drill
            return False
        return True

    def _rule_dispatch(self, task: NetTask) -> Optional[DispatchPlan]:
        """Rule-based dispatch (deterministic)."""
        plan = DispatchPlan(net=task.net, planner=Planner.RULE)

        # Apply class-specific rules
        if task.cls == SignalClass.DIFFERENTIAL:
            plan.routing_type = RoutingType.DIFF_PAIR
            plan.track_width = 0.127
            plan.rationale = "Differential pair rules"
        elif task.cls == SignalClass.POWER:
            plan.routing_type = RoutingType.POWER
            plan.track_width = 0.4
            plan.rationale = "Power net rules"
        elif task.cls == SignalClass.CLOCK:
            plan.routing_type = RoutingType.EQUAL_LENGTH
            plan.track_width = 0.127
            plan.rationale = "Clock signal rules"
        elif task.cls == SignalClass.CRITICAL:
            plan.track_width = 0.127
            plan.rationale = "Critical signal rules"
        else:
            plan.track_width = 0.127
            plan.rationale = "Normal signal rules"

        # BGA fanout style based on pitch
        if self._is_bga_net(task):
            plan.bga_fanout_style = BGAFanoutStyle.DOGBONE
            plan.escape_strategy = "dogbone_diagonal"

        plan.decider = {'kind': 'rule', 'id': 'dispatch_rules',
                        'because': plan.rationale}
        return plan

    def _baseline_dispatch(self, task: NetTask) -> DispatchPlan:
        """Baseline dispatch (defaults)."""
        return DispatchPlan(
            net=task.net,
            via_type=ViaType.THROUGH,
            via_size=(0.5, 0.3),
            track_width=0.127,
            routing_type=RoutingType.NORMAL,
            bga_fanout_style=BGAFanoutStyle.DOGBONE,
            planner=Planner.BASELINE,
            rationale="Baseline defaults",
            decider={'kind': 'rule', 'id': 'dispatch_baseline'},
        )

    def _is_bga_net(self, task: NetTask) -> bool:
        """Check if this net is on a BGA component."""
        # Simple heuristic: if pad coordinates are close together (<1mm), it's BGA
        dist = ((task.a[0] - task.b[0]) ** 2 + (task.a[1] - task.b[1]) ** 2) ** 0.5
        return dist < 1.0

    def feedback(self, result: dict, diagnosis: dict) -> list[dict]:
        """Process feedback and generate new candidates.

        Args:
            result: RouteResult dict
            diagnosis: RoutingDiagnosis dict

        Returns:
            List of new RoutingCandidate dicts
        """
        candidates = []

        # Gate lowering is structurally forbidden: propose a topology change.
        if result.get('drc_errors', 0) > 10:
            candidates.append({
                'name': 'reroute_violating_nets',
                'category': 'topology',
                'params': {'reroute_nets': True},
                'expected_improvement': 0.1,
                'risk': 0.2,
            })

        # If opens are high, suggest escape-first routing
        if result.get('opens', 0) > 5:
            candidates.append({
                'name': 'escape_first',
                'category': 'escape',
                'params': {'escape_first': True},
                'expected_improvement': 0.15,
                'risk': 0.1,
            })

        return candidates

    def record_outcome(self, plan: DispatchPlan, result: dict):
        """Record outcome for LLM history."""
        self._history.append({
            'net': plan.net,
            'planner': plan.planner.value,
            'params': {
                'via_type': plan.via_type.value,
                'track_width': plan.track_width,
                'routing_type': plan.routing_type.value,
            },
            'result': {
                'opens': result.get('opens', 0),
                'drc_errors': result.get('drc_errors', 0),
            },
        })

    def to_llm_history(self) -> list[dict]:
        """Get LLM history for next decision."""
        return self._history[-10:]  # Last 10 outcomes
