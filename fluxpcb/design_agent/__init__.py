#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""design_agent — PCB Design Agent (Component 4).

High-level planning: stackup, impedance, placement, BGA decomposition.
Outputs BoardDesignPlan consumed by Component 3 (DevRouteLoop).
"""
from .pcb_design_agent import PCBDesignAgent

__all__ = ["PCBDesignAgent"]
