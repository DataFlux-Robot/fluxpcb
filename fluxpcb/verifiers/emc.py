#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""emc — EMC Pre-compliance Verifier (Component 10).

Four gates answer it: return-current concentration (`return_path`, an analytic
estimate), plane-cavity resonance against the band limit
(`plane_cavity_resonance`), the per-net susceptibility index
(`em_susceptibility`, a labelled proxy) and, when physics execution is
confirmed, the openEMS field solve (`em_field_analysis`).  The field solve is
`unknown` until it runs.

The verdict is the gates' verdict: `return_path`, `plane_cavity_resonance`, `em_susceptibility`, `em_field_analysis`.  Nothing here is re-computed;
a gate that cannot decide yields `unknown` with the missing information,
not a pass.
"""
from __future__ import annotations

from ..contracts import BoardDesignPlan, BoardState, VerificationResult
from .from_gates import run_verifier
from .policy import PhysicsPolicy


class EMCVerifier:
    """EMC pre-compliance verifier"""

    name = "emc_precompliance"
    severity = "warning"

    def __init__(self, policy: PhysicsPolicy = None):
        self.policy = policy

    def run(self, state: BoardState, plan: BoardDesignPlan) -> VerificationResult:
        return run_verifier(self.name, state, plan, self.policy)
