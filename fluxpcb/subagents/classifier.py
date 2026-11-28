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

    # Unambiguous differential-pair tokens (single-letter 'P'/'N' removed:
    # previously caused VCAP, NRST, PWM_CH1, OSC_IN to be misclassified as diff!)
    DIFF_PAIR_EXACT = (
        'USB_DP', 'USB_DM', 'USB_D+', 'USB_D-', 'CAN_H', 'CAN_L',
        'CAN1_H', 'CAN1_L', 'CAN2_H', 'CAN2_L',
    )
    DIFF_PAIR_SUFFIXES = (
        '_DP', '_DM', '_D+', '_D-', '_TX+', '_TX-', '_RX+', '_RX-',
        'TX+', 'TX-', 'RX+', 'RX-', '_H', '_L', '+', '-',
    )
    _PAIR_MATES = (
        ('_DP', '_DM'), ('_D+', '_D-'), ('_P', '_N'),
        ('_H', '_L'), ('+', '-'), ('DP', 'DM'),
    )

    CLOCK_PATTERNS = [
        'CLK', 'CLOCK', 'XTAL', 'OSC', 'HSE', 'LSE', 'MCO',
        'SDMMC_CK', 'SDMMC1_CK', 'SDMMC_CMD', 'SPI1_SCK', 'SPI2_SCK',
    ]

    POWER_PATTERNS = [
        'VCC', 'VDD', 'VSS', 'GND', '3V3', '5V', '1V8', '1V2', '12V',
        'VBAT', 'VCAP', 'VDDA', 'VEE', 'AVCC', 'VBUS', '+3V3', '+5V',
    ]

    CRITICAL_PATTERNS = [
        'NRST', 'RESET', 'BOOT', 'SWDIO', 'SWCLK', 'SWD',
        'JTAG', 'TCK', 'TMS', 'TDI', 'TDO',
    ]

    def find_diff_pairs(self, nets: list[str]) -> list[tuple[str, str]]:
        """Return confirmed (P, N) net pairs where BOTH mates exist in `nets`."""
        by_upper = {n.upper(): n for n in nets if n}
        pairs, used = [], set()
        for p_suf, n_suf in self._PAIR_MATES:
            for nu, orig in sorted(by_upper.items()):
                if orig in used or not nu.endswith(p_suf):
                    continue
                stem = nu[:-len(p_suf)]
                if not stem:
                    continue
                mate_u = stem + n_suf
                if mate_u in by_upper and by_upper[mate_u] != orig and by_upper[mate_u] not in used:
                    # Guard against generic power nets like +5V / -5V or V+ / V-
                    if any(pk in stem for pk in ('GND', 'VCC', 'VDD', 'VSS')):
                        continue
                    pairs.append((orig, by_upper[mate_u]))
                    used.add(orig)
                    used.add(by_upper[mate_u])
        return pairs

    def classify(self, nets: list[str]) -> dict[str, SignalClass]:
        """Classify nets into signal classes (pair-aware for differentials)."""
        paired = set()
        for p, n in self.find_diff_pairs(nets):
            paired.add(p)
            paired.add(n)
        result = {}
        for net in nets:
            if net in paired:
                result[net] = SignalClass.DIFFERENTIAL
            else:
                result[net] = self._classify_single(net)
        return result

    def _classify_single(self, net: str) -> SignalClass:
        """Classify a single net."""
        net_upper = net.upper()

        # Check for power nets first so +5V / +3V3 are never mistaken for diff
        for pattern in self.POWER_PATTERNS:
            if net_upper == pattern or net_upper.startswith(pattern + '_') or net_upper.endswith('_' + pattern) or net_upper.startswith('+' + pattern):
                return SignalClass.POWER

        # Check for unambiguous differential pair tokens
        if net_upper in self.DIFF_PAIR_EXACT:
            return SignalClass.DIFFERENTIAL
        for suf in self.DIFF_PAIR_SUFFIXES:
            if len(net_upper) > len(suf) and net_upper.endswith(suf):
                return SignalClass.DIFFERENTIAL

        # Check for clock signals
        for pattern in self.CLOCK_PATTERNS:
            if pattern in net_upper:
                return SignalClass.CLOCK

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
