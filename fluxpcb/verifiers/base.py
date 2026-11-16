#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""base — Verifier protocol (Component 10).

Defines the base protocol for all verifiers.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from ..contracts import BoardDesignPlan, BoardState, VerificationResult


@runtime_checkable
class Verifier(Protocol):
    """Base protocol for all verifiers."""

    name: str
    severity: str  # "error", "warning", "info"

    def run(self, state: BoardState, plan: BoardDesignPlan) -> VerificationResult:
        """Run verification.

        Args:
            state: Board state (Component 5)
            plan: Design plan (Component 4)

        Returns:
            VerificationResult with findings
        """
        ...
