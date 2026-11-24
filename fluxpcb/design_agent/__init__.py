#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""design_agent — PCB Design Agent (Component 4).

High-level planning: stackup, impedance, placement, BGA decomposition.
Outputs BoardDesignPlan consumed by Component 3 (DevRouteLoop).
"""
from .capacity import capacity_claim, channel_capacity
from .pcb_design_agent import PCBDesignAgent
from .plan import CapacityClaim, RoutingPlan
from .planner import build_plan, plan_for_feasibility

__all__ = ["PCBDesignAgent", "RoutingPlan", "CapacityClaim",
           "build_plan", "plan_for_feasibility", "capacity_claim",
           "channel_capacity"]
