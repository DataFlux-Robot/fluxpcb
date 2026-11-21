#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verifiers — Verification Suite (Component 10).

Unified verifier suite over existing physics tools:
  - PowerIntegrityVerifier: IR drop, PDN impedance
  - SignalIntegrityVerifier: crosstalk, length match, eye
  - ThermalVerifier: temperature, heat dissipation
  - SpiceVerifier: functional simulation
  - EMCVerifier: return path, emissions

Wraps existing tools: dcsolve.py, fdm.py, pdn_ac.py, spice.py, returnpath.py
"""
from .base import Verifier
from .suite import VerifierSuite
from .power_integrity import PowerIntegrityVerifier
from .signal_integrity import SignalIntegrityVerifier
from .thermal import ThermalVerifier
from .spice import SpiceVerifier
from .emc import EMCVerifier

__all__ = [
    "Verifier",
    "VerifierSuite",
    "PowerIntegrityVerifier",
    "SignalIntegrityVerifier",
    "ThermalVerifier",
    "SpiceVerifier",
    "EMCVerifier",
]
