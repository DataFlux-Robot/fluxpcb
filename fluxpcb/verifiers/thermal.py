#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""thermal — Thermal Verifier (Component 10).

Junction temperature from the conduction solve (`thermal`, an estimate on
declared geometry) and, when a CFD run has actually happened, `thermal_cfd`.
A CFD solver installed but not run is `unknown`, not a pass.

The verdict is the gates' verdict: `thermal` and `thermal_cfd`.  Nothing here is re-computed;
a gate that cannot decide yields `unknown` with the missing information,
not a pass.
"""
from __future__ import annotations

from ..contracts import BoardDesignPlan, BoardState, VerificationResult
from .from_gates import run_verifier
from .policy import PhysicsPolicy


class ThermalVerifier:
    """Thermal verifier"""

    name = "thermal"
    severity = "warning"

    def __init__(self, policy: PhysicsPolicy = None):
        self.policy = policy

    def run(self, state: BoardState, plan: BoardDesignPlan) -> VerificationResult:
        return run_verifier(self.name, state, plan, self.policy)
