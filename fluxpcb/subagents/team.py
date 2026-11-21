#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""team — Subagent Team (Component 6).

Coordinates all subagents: classification, ordering, and per-class specialists.
"""
from __future__ import annotations

from typing import Any, Optional

from ..contracts import (
    BoardState,
    FeedbackEvent,
    NetTask,
    SignalClass,
)
from .base import Action
from .classifier import SignalClassifierSubagent
from .prioritizer import PrioritySubagent
from .specialist import ClockSubagent, DifferentialPairSubagent, PowerNetSubagent


class SubagentTeam:
    """Subagent team — coordinates all subagents.

    Dispatches classification + ordering, then applies per-class specialists.
    """

    def __init__(self):
        self.classifier = SignalClassifierSubagent()
        self.prioritizer = PrioritySubagent()
        self.specialists = {
            SignalClass.DIFFERENTIAL: DifferentialPairSubagent(),
            SignalClass.POWER: PowerNetSubagent(),
            SignalClass.CLOCK: ClockSubagent(),
        }

    def dispatch(
        self,
        board_state: BoardState,
        comm: Any = None,
    ) -> tuple[dict[str, SignalClass], list[NetTask]]:
        """Dispatch subagents to classify and order nets.

        Args:
            board_state: Board state with net_pads
            comm: CommRuntime for state binding (optional)

        Returns:
            (classification, ordered_tasks)
        """
        # Step 1: Classify all nets
        net_names = list(board_state.net_pads.keys())
        classification = self.classifier.classify(net_names)

        # Step 2: Build NetTasks from net_pads
        tasks = self._build_tasks(board_state.net_pads, classification)

        # Step 3: Order tasks by priority
        ordered_tasks = self.prioritizer.order(tasks)

        # Step 4: Apply specialist constraints
        for task in ordered_tasks:
            specialist = self.specialists.get(task.cls)
            if specialist:
                action = specialist.propose({'net': task.net})
                task._specialist_action = action  # Attach for downstream use

            # Step 5: Select repair operator by signal class (skill package)
            task._repair_operator = self.select_repair_operator(task)

        return classification, ordered_tasks

    def select_repair_operator(self, task: NetTask) -> str:
        """Select the right repair operator for a task based on its class.

        This maps the skill package's morphology recognizers to signal classes:
          - CRITICAL (BGA escape / deadlock)  → topology_swap
          - POWER (plane fragment)             → plane_bridge
          - DIFFERENTIAL / CLOCK (spacing)     → width_cascade_surgery
          - NORMAL (floating endpoint)         → endpoint_attach

        Args:
            task: NetTask to select an operator for

        Returns:
            Operator name
        """
        if task.cls == SignalClass.CRITICAL:
            return 'topology_swap'
        elif task.cls == SignalClass.POWER:
            return 'plane_bridge'
        elif task.cls in (SignalClass.DIFFERENTIAL, SignalClass.CLOCK):
            return 'width_cascade_surgery'
        else:
            return 'endpoint_attach'

    def _build_tasks(
        self,
        net_pads: dict[str, list[tuple]],
        classification: dict[str, SignalClass],
    ) -> list[NetTask]:
        """Build NetTasks from net_pads (MST decomposition)."""
        tasks = []
        for net_name, pads in net_pads.items():
            if len(pads) < 2:
                continue

            cls = classification.get(net_name, SignalClass.NORMAL)

            # MST edges for multi-pin nets
            for i in range(len(pads) - 1):
                task = NetTask(
                    net=net_name,
                    a=pads[i],
                    b=pads[i + 1],
                    cls=cls,
                    priority=float(self.prioritizer.CLASS_PRIORITY.get(cls, 5)),
                )
                tasks.append(task)

        return tasks

    def get_specialist_action(self, task: NetTask) -> Optional[Action]:
        """Get specialist action for a task."""
        return getattr(task, '_specialist_action', None)

    def get_repair_operator(self, task: NetTask) -> str:
        """Get the repair operator assigned to a task."""
        return getattr(task, '_repair_operator', 'endpoint_attach')
