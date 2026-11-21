#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""prioritizer — Priority Subagent (Component 6).

Orders routing tasks by priority: critical → differential → clock → power → normal.
Within each class, BGA escape tasks first, then hardest-first.
"""
from __future__ import annotations

from typing import Any

from ..contracts import FeedbackEvent, NetTask, SignalClass
from .base import Action


class PrioritySubagent:
    """Priority ordering subagent.

    Orders NetTasks by signal class and difficulty.
    """

    name = "priority_orderer"

    # Priority ranking (lower = higher priority)
    CLASS_PRIORITY = {
        SignalClass.CRITICAL: 0,
        SignalClass.DIFFERENTIAL: 1,
        SignalClass.CLOCK: 2,
        SignalClass.POWER: 3,
        SignalClass.NORMAL: 4,
    }

    def order(self, tasks: list[NetTask]) -> list[NetTask]:
        """Order tasks by priority.

        Args:
            tasks: List of NetTasks to order

        Returns:
            Ordered list of NetTasks
        """
        # Sort by class priority, then by distance (shortest first)
        def sort_key(task: NetTask) -> tuple:
            class_rank = self.CLASS_PRIORITY.get(task.cls, 5)
            distance = self._distance(task.a, task.b)
            return (class_rank, distance)

        return sorted(tasks, key=sort_key)

    def _distance(self, a: tuple, b: tuple) -> float:
        """Calculate Euclidean distance between two points."""
        return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5

    def propose(self, summary: dict) -> Action:
        """Propose ordering action."""
        return Action(
            action_type="order",
            params={'tasks': summary.get('tasks', [])},
            confidence=0.85,
            rationale="Priority ordering by signal class and distance",
        )

    def observe(self, result: FeedbackEvent) -> None:
        """Observe feedback (no persistent state needed)."""
        pass
