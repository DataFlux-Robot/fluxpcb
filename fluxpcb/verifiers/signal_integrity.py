#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""signal_integrity — Signal Integrity Verifier (Component 10).

IPC-2141 closed-form impedance of the declared stackup against the plan's targets
(`impedance_si`), and length matching where netclasses.json carries measured
lengths.  It is an estimate of the geometry, not a measurement of the routed
copper — and the finding says so.

The verdict is the gates' verdict: `impedance_si`.  Nothing here is re-computed;
a gate that cannot decide yields `unknown` with the missing information,
not a pass.
"""
from __future__ import annotations

from ..contracts import BoardDesignPlan, BoardState, VerificationResult
from .from_gates import run_verifier
from .policy import PhysicsPolicy


class SignalIntegrityVerifier:
    """Signal integrity verifier"""

    name = "signal_integrity"
    severity = "warning"

    def __init__(self, policy: PhysicsPolicy = None):
        self.policy = policy

    def run(self, state: BoardState, plan: BoardDesignPlan) -> VerificationResult:
        return run_verifier(self.name, state, plan, self.policy)
