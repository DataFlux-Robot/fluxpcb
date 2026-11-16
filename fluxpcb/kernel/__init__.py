# -*- coding: utf-8 -*-
"""Board State Kernel — the board stops being a shared mutable file.

Five facets, one kernel (each kills a failure CLASS, see module docs):
  state   — single truth interface, fingerprint-bound measurements
  txn     — engine invocations as atomic transactions
  invariants — enforced lessons (I1-I4, add new ones here)
  causal  — failure taxonomy + upstream regeneration edges
  truthnet — fabrication-grade netlist audit (IPC-D-356 vs schematic)
  lint    — knowhow entries must generalize into invariants
"""
from . import state, txn, invariants, causal, truthnet, lint  # noqa
