#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""power_integrity — Power Integrity Verifier (Component 10).

The verdict is the gates' verdict: PDN impedance against the target
curve (`pdn_impedance_ac`), current density against the IPC limit
(`dc_current_density`) and IR drop on the real copper
(`dc_irdrop_geom`).  Nothing here is re-computed; a gate that cannot
decide yields `unknown` with the missing information, not a pass.
"""
from __future__ import annotations

from ..contracts import BoardDesignPlan, BoardState, VerificationResult
from .from_gates import run_verifier
from .policy import PhysicsPolicy


class PowerIntegrityVerifier:
    """Power integrity verifier."""

    name = "power_integrity"
    severity = "error"

    def __init__(self, policy: PhysicsPolicy = None):
        self.policy = policy

    def run(self, state: BoardState, plan: BoardDesignPlan) -> VerificationResult:
        return run_verifier(self.name, state, plan, self.policy)
