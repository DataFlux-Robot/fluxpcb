#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""classifier — Signal Classifier Subagent (Component 6).

Classifies nets into signal classes (critical, differential, clock, power, normal).
Wraps existing routing_classes.py primitives.
"""
from __future__ import annotations

from typing import Any, Optional

from ..contracts import FeedbackEvent, SignalClass
from .base import Action


class SignalClassifierSubagent:
    """Signal classifier subagent.

    Classifies nets based on naming conventions and board topology.
    """

    name = "signal_classifier"

    # Naming conventions for classification
    DIFF_PAIR_PREFIXES = [
        'USB_DP', 'USB_DM', 'CAN_H', 'CAN_L', 'CAN1_H', 'CAN1_L',
        'CAN2_H', 'CAN2_L', 'TX+', 'TX-', 'RX+', 'RX-',
        'DP', 'DM', 'D+', 'D-', 'P', 'N',
    ]

    CLOCK_PATTERNS = [
        'CLK', 'CLOCK', 'XTAL', 'OSC', 'HSE', 'LSE', 'MCO',
        'SDMMC_CK', 'SDMMC_CMD',
    ]

    POWER_PATTERNS = [
        'VCC', 'VDD', 'VSS', 'GND', '3V3', '5V', '1V8', '1V2',
        'VBAT', 'VCAP', 'VDDA', 'VDDA', 'VEE', 'AVCC',
    ]

    CRITICAL_PATTERNS = [
        'NRST', 'RESET', 'BOOT', 'SWDIO', 'SWCLK', 'SWD',
        'JTAG', 'TCK', 'TMS', 'TDI', 'TDO',
    ]

    def classify(self, nets: list[str]) -> dict[str, SignalClass]:
        """Classify nets into signal classes.

        Args:
            nets: List of net names

        Returns:
            dict mapping net name to SignalClass
        """
        result = {}
        for net in nets:
            result[net] = self._classify_single(net)
        return result

    def _classify_single(self, net: str) -> SignalClass:
        """Classify a single net."""
        net_upper = net.upper()

        # Check for differential pairs
        for prefix in self.DIFF_PAIR_PREFIXES:
            if net_upper.startswith(prefix) or net_upper.endswith(prefix):
                return SignalClass.DIFFERENTIAL

        # Check for clock signals
        for pattern in self.CLOCK_PATTERNS:
            if pattern in net_upper:
                return SignalClass.CLOCK

        # Check for power nets
        for pattern in self.POWER_PATTERNS:
            if net_upper.startswith(pattern) or net_upper.endswith(pattern):
                return SignalClass.POWER

        # Check for critical signals
        for pattern in self.CRITICAL_PATTERNS:
            if pattern in net_upper:
                return SignalClass.CRITICAL

        return SignalClass.NORMAL

    def propose(self, summary: dict) -> Action:
        """Propose classification action."""
        return Action(
            action_type="classify",
            params={'nets': summary.get('nets', [])},
            confidence=0.9,
            rationale="Signal classification based on naming conventions",
        )

    def observe(self, result: FeedbackEvent) -> None:
        """Observe feedback (no persistent state needed)."""
        pass
