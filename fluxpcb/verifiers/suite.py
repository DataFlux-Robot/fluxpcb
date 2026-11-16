#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""suite — Verifier Suite (Component 10).

Aggregates all verifiers into a single suite.
"""
from __future__ import annotations

from typing import Any, Optional

from ..contracts import (
    BoardDesignPlan,
    BoardState,
    VerificationReport,
    VerificationResult,
)
from .base import Verifier


class VerifierSuite:
    """Verifier suite — runs all verifiers and aggregates results."""

    def __init__(self, verifiers: Optional[list[Verifier]] = None, comm: Any = None):
        """Initialize verifier suite.

        Args:
            verifiers: List of verifiers to run
            comm: CommRuntime for state binding
        """
        self.verifiers = verifiers or []
        self.comm = comm

    def run_all(self, state: BoardState, plan: BoardDesignPlan) -> VerificationReport:
        """Run all verifiers and aggregate results.

        Args:
            state: Board state
            plan: Design plan

        Returns:
            VerificationReport with all results
        """
        results = []
        for verifier in self.verifiers:
            try:
                result = verifier.run(state, plan)
                results.append(result)
            except Exception as e:
                results.append(VerificationResult(
                    name=verifier.name,
                    status="fail",
                    severity="error",
                    findings=[{'error': str(e)}],
                ))

        return VerificationReport(
            results=results,
            fingerprint=state.fingerprint,
        )

    def run_single(self, name: str, state: BoardState, plan: BoardDesignPlan) -> Optional[VerificationResult]:
        """Run a single verifier by name."""
        for verifier in self.verifiers:
            if verifier.name == name:
                return verifier.run(state, plan)
        return None

    def add(self, verifier: Verifier):
        """Add a verifier to the suite."""
        self.verifiers.append(verifier)

    @property
    def verifier_names(self) -> list[str]:
        return [v.name for v in self.verifiers]
