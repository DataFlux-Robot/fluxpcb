#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""feedback_runtime — LLM+Rule Feedback Runtime (Component 8).

Bidirectional feedback:
  1. Rule tuning: update param tables from outcomes
  2. LLM history: feed compressed trajectory to LLM
  3. Escalation: rule plateau → Component 1 method change

Consumes RouteResult (Component 9) + VerificationReport (Component 10),
produces FeedbackEvent + Trajectory.
"""
from __future__ import annotations

import time
from typing import Any, Optional

from ..contracts import (
    FeedbackEvent,
    RouteResult,
    StateFingerprint,
    Trajectory,
    VerificationReport,
)


class FeedbackRuntime:
    """Feedback runtime — bidirectional LLM↔rule feedback.

    Consumes routing results and verification reports, produces
    feedback events and trajectories.
    """

    def __init__(self, comm: Any = None):
        """Initialize feedback runtime.

        Args:
            comm: CommRuntime for state binding
        """
        self.comm = comm
        self._events: list[FeedbackEvent] = []
        self._rule_updates: list[dict] = []
        self._plateau_count: int = 0

    def observe(
        self,
        action: dict,
        result: Any,
        verification: Optional[VerificationReport] = None,
    ) -> FeedbackEvent:
        """Observe a routing result and produce feedback.

        Args:
            action: Action taken (iteration, dispatch plans, etc.)
            result: RouteResult or dict
            verification: VerificationReport from Component 10

        Returns:
            FeedbackEvent with delta and fingerprint
        """
        # Extract metrics
        if isinstance(result, RouteResult):
            opens = result.opens
            shorts = result.shorts
            drc = result.drc_errors
            fp = result.fingerprint
        elif isinstance(result, dict):
            opens = result.get('opens', 0)
            shorts = result.get('shorts', 0)
            drc = result.get('drc_errors', 0)
            fp = StateFingerprint()
        else:
            opens = shorts = drc = 0
            fp = StateFingerprint()

        # Calculate delta (improvement from previous)
        delta = self._calculate_delta(opens, shorts, drc)

        # Create feedback event
        event = FeedbackEvent(
            action=action,
            result={
                'opens': opens,
                'shorts': shorts,
                'drc_errors': drc,
            },
            delta=delta,
            fingerprint=fp,
            timestamp=time.time(),
        )

        self._events.append(event)

        # Update rules based on outcome
        self._update_rules(event)

        # Check for plateau
        if delta.get('improvement', 0) <= 0:
            self._plateau_count += 1
        else:
            self._plateau_count = 0

        return event

    def _calculate_delta(self, opens: int, shorts: int, drc: int) -> dict:
        """Calculate improvement delta from previous event."""
        if not self._events:
            return {'opens': opens, 'shorts': shorts, 'drc': drc, 'improvement': 0}

        prev = self._events[-1].result
        prev_opens = prev.get('opens', 0)
        prev_shorts = prev.get('shorts', 0)
        prev_drc = prev.get('drc_errors', 0)

        # Improvement = reduction in total errors
        prev_total = prev_opens + prev_shorts + prev_drc
        curr_total = opens + shorts + drc
        improvement = prev_total - curr_total

        return {
            'opens': opens - prev_opens,
            'shorts': shorts - prev_shorts,
            'drc': drc - prev_drc,
            'improvement': improvement,
        }

    def _update_rules(self, event: FeedbackEvent):
        """Update rules based on feedback event."""
        opens = event.result.get('opens', 0)
        drc = event.result.get('drc_errors', 0)

        # A meta/feedback layer may not lower a gate to make a board pass.
        # Propose a topology change instead; the rule model stays fixed.
        if drc > 5:
            self._rule_updates.append({
                'param': 'reroute_violating_nets',
                'action': 'enable',
                'reason': 'high_drc_errors',
            })

        # If opens are high, suggest escape-first
        if opens > 3:
            self._rule_updates.append({
                'param': 'escape_first',
                'action': 'enable',
                'value': True,
                'reason': 'high_opens',
            })

    def suggest_repairs(self, result: Any) -> list[dict]:
        """Suggest repairs based on routing result."""
        suggestions = []

        if isinstance(result, RouteResult):
            opens = result.opens
            shorts = result.shorts
        elif isinstance(result, dict):
            opens = result.get('opens', 0)
            shorts = result.get('shorts', 0)
        else:
            opens = shorts = 0

        if opens > 0:
            suggestions.append({
                'net': '*',
                'strategy': 'escape_first',
                'reason': f'{opens} opens detected',
            })

        if shorts > 0:
            suggestions.append({
                'net': '*',
                'strategy': 'increase_clearance',
                'reason': f'{shorts} shorts detected',
            })

        return suggestions

    def to_llm_history(self) -> list[dict]:
        """Get compressed trajectory for LLM history."""
        history = []
        for event in self._events[-10:]:
            history.append({
                'action': event.action,
                'result': event.result,
                'delta': event.delta,
            })
        return history

    def summarize_trajectory(self) -> Trajectory:
        """Summarize all events into a trajectory."""
        if not self._events:
            return Trajectory()

        total_improvement = sum(
            e.delta.get('improvement', 0) for e in self._events
        )

        return Trajectory(
            events=self._events.copy(),
            grade=float(total_improvement),
            board_hash=self._events[0].fingerprint.board_hash,
        )

    def escalate(self) -> Optional[dict]:
        """Check if we should escalate to Component 1 (method change).

        Returns:
            Method proposal if plateau detected, None otherwise.
        """
        if self._plateau_count >= 3:
            return {
                'type': 'method_change',
                'reason': f'plateau_detected_after_{self._plateau_count}_iterations',
                'suggestion': 'try_different_routing_strategy',
            }
        return None

    @property
    def event_count(self) -> int:
        return len(self._events)

    @property
    def plateau_count(self) -> int:
        return self._plateau_count
