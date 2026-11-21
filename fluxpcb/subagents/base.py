#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""base — Subagent protocol (Component 6).

Defines the base protocol for all subagents.
Each subagent implements propose() and observe().
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, runtime_checkable

from ..contracts import FeedbackEvent, NetTask, SignalClass


@dataclass
class Action:
    """Subagent action proposal."""
    action_type: str = ""
    params: dict = field(default_factory=dict)
    confidence: float = 0.0
    rationale: str = ""


@runtime_checkable
class Subagent(Protocol):
    """Base protocol for all subagents."""

    name: str

    def propose(self, summary: dict) -> Action:
        """Propose an action based on the summary.

        Args:
            summary: Bounded state summary from CommRuntime

        Returns:
            Action proposal
        """
        ...

    def observe(self, result: FeedbackEvent) -> None:
        """Observe a feedback event and update internal state.

        Args:
            result: Feedback event from Component 8
        """
        ...
