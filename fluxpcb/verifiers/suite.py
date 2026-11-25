#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""suite — Verifier Suite (Component 10).

Aggregates all verifiers into a single suite. Enhanced with the
pcb-hybrid-routing-advanced repair operator library.

Usage:
    from fluxpcb.verifiers.suite import VerifierSuite
    suite = VerifierSuite([...])
    report = suite.run_all(board_state, plan)
    # or with auto-repair:
    report = suite.run_and_repair(board_state, plan)
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
    """Verifier suite — runs all verifiers and aggregates results.

    Enhanced with RepairOperators (skill package) for auto-repair.
    """

    def __init__(self, verifiers: Optional[list[Verifier]] = None, comm: Any = None):
        """Initialize verifier suite.

        Args:
            verifiers: List of verifiers to run
            comm: CommRuntime for state binding
        """
        self.verifiers = verifiers or []
        self.comm = comm

        # Component 10 enhancement: repair operator library (skill package)
        from .operators import RepairOperators
        self.repair_ops = RepairOperators()

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
                # a verifier that raised decided nothing: `unknown` with
                # the reason, never a fabricated pass or an unrelated fail
                results.append(VerificationResult(
                    name=getattr(verifier, 'name', type(verifier).__name__),
                    status="unknown",
                    severity="warning",
                    findings=[],
                    needed_information=[f'{type(e).__name__}: {e}'],
                ))

        return VerificationReport(
            results=results,
            fingerprint=state.fingerprint,
        )

    def run_and_repair(self, state: BoardState, plan: BoardDesignPlan) -> VerificationReport:
        """Run all verifiers, then auto-repair violations using skill operators.

        This is the Component 10 enhancement: verify → classify morphology →
        dispatch the right repair operator (width_cascade / policy_waiver /
        plane_bridge / topology_swap / endpoint_attach).

        Args:
            state: Board state
            plan: Design plan

        Returns:
            VerificationReport with repair ledger in evidence
        """
        # Step 1: verify
        report = self.run_all(state, plan)

        # Step 2: extract violations from report findings
        violations = self._extract_violations(report)

        # Step 3: dispatch repair operators by morphology
        if violations and state.board is not None:
            repair_results = self.repair_ops.dispatch_repair(state.board, violations)
            repair_ledger = [
                {
                    'operator': r.operator,
                    'success': r.success,
                    'repaired': r.repaired_count,
                    'failed': r.failed_count,
                    'notes': r.notes,
                }
                for r in repair_results
            ]
            # Attach repair ledger to the report
            report.repair_ledger = repair_ledger

        return report

    def _extract_violations(self, report: VerificationReport) -> list:
        """Extract Violation objects from a verification report's findings.

        Args:
            report: VerificationReport

        Returns:
            List of Violation objects
        """
        from .operators import Violation

        violations = []
        for result in report.results:
            for finding in result.findings:
                if isinstance(finding, dict):
                    v = Violation(
                        type=finding.get('type', result.name),
                        net=finding.get('net', ''),
                        net2=finding.get('net2', ''),
                        actual_mm=finding.get('actual', 0.0),
                        required_mm=finding.get('required', 0.0),
                        location=finding.get('location', (0.0, 0.0)),
                        items=finding.get('items', []),
                    )
                    violations.append(v)
        return violations

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

    @property
    def operator_names(self) -> list[str]:
        """Get the repair operator names (skill package)."""
        return [
            'width_cascade_surgery',
            'policy_waiver_probe',
            'plane_bridge',
            'topology_swap',
            'endpoint_attach',
        ]
