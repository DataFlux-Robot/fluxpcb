#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""subagents — Subagent abstraction (Component 6).

Signal classification and priority ordering subagents.
Wraps existing routing_classes.py primitives into a unified interface.
"""
from .base import Subagent
from .classifier import SignalClassifierSubagent
from .prioritizer import PrioritySubagent
from .specialist import DifferentialPairSubagent, PowerNetSubagent, ClockSubagent
from .team import SubagentTeam

__all__ = [
    "Subagent",
    "SignalClassifierSubagent",
    "PrioritySubagent",
    "DifferentialPairSubagent",
    "PowerNetSubagent",
    "ClockSubagent",
    "SubagentTeam",
]
