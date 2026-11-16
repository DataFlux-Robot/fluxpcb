#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""router — Rule-based routing engines (Component 9).

Unified interface for NCER, Eikonal, A*, PNS, and BGA escape routing.

Components:
  - NCERRouter: Negotiated congestion with rip-up-and-reroute
  - GPUEikonalRouter: GPU-accelerated Fast Iterative Method
  - BGAEscapeSolver: Min-cost max-flow BGA escape
  - GeometryVerifier: Exact collision verification (0.01mm)

All routing is pure-rule (no LLM). LLM decisions come from Component 7
and are applied as constraints, not direct coordinates.
"""
from .ncer import NCERRouter
from .unified import Router, RouterConfig

__all__ = ["NCERRouter", "Router", "RouterConfig"]
