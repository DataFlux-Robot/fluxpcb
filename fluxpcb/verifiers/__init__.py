#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verifiers — Verification Suite (Component 10).

    VerifierSuite            the five verifiers, each backed by its gates
    PowerIntegrityVerifier   PDN impedance, current density, IR drop on copper
    SignalIntegrityVerifier  impedance of the declared stackup vs targets
    ThermalVerifier          junction temperature (conduction; CFD when run)
    SpiceVerifier            ngspice Monte-Carlo on the stage's testbench
    EMCVerifier              return path, cavity modes, susceptibility, field
    PhysicsPolicy            off / estimate / execute — what may run per run

The modules are imported lazily (PEP 562): the repair path — what the
benchmark and every repair operator import — reaches `operators`,
`conflict_reroute` and `txn` without loading the physics suite, and a
test pins that (`test_the_repair_path_imports_none_of_the_new_physics`).
"""
from __future__ import annotations

import importlib

_LAZY = {
    'Verifier': '.base',
    'PhysicsPolicy': '.policy',
    'VerifierSuite': '.suite',
    'PowerIntegrityVerifier': '.power_integrity',
    'SignalIntegrityVerifier': '.signal_integrity',
    'ThermalVerifier': '.thermal',
    'SpiceVerifier': '.spice',
    'EMCVerifier': '.emc',
}

__all__ = sorted(_LAZY)


def __getattr__(name):
    where = _LAZY.get(name)
    if where is None:
        raise AttributeError(f'module {__name__!r} has no attribute {name!r}')
    mod = importlib.import_module(where, __name__)
    return getattr(mod, name)


def __dir__():
    return sorted(set(globals()) | set(_LAZY))
