#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""spice — SPICE Functional Verifier (Component 10).

ngspice Monte-Carlo on the testbench the acceptance stage wrote, read back
from sim/spice_mc.json (`spice_functional`).  The finding names the assumption
that the testbench represents the load.

The verdict is the gates' verdict: `spice_functional`.  Nothing here is re-computed;
a gate that cannot decide yields `unknown` with the missing information,
not a pass.
"""
from __future__ import annotations

from ..contracts import BoardDesignPlan, BoardState, VerificationResult
from .from_gates import run_verifier
from .policy import PhysicsPolicy


class SpiceVerifier:
    """SPICE functional verifier"""

    name = "spice_functional"
    severity = "error"

    def __init__(self, policy: PhysicsPolicy = None):
        self.policy = policy

    def run(self, state: BoardState, plan: BoardDesignPlan) -> VerificationResult:
        return run_verifier(self.name, state, plan, self.policy)
