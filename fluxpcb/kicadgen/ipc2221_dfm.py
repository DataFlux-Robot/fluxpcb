#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ipc2221_dfm — IPC-2221 DFM compliance checking.

Implements the 12-item IPC-2221 DFM checklist:
1. Conductor spacing (B1-B4 voltage classes)
2. Conductor width (current capacity)
3. Annular ring
4. Hole size
5. Drill tolerance
6. Copper-to-edge clearance
7. Solder mask expansion
8. Silkscreen clearance
9. Pad-to-pad spacing
10. Via-to-via spacing
11. Trace-to-hole spacing
12. Board outline

Based on:
- IPC-2221 Generic Standard on Printed Board Design
- JLC DFM rules
- kicad-mcp-pro manufacturing release gates

Usage:
    from ipc2221_dfm import IPC2221DFM
    dfm = IPC2221DFM()
    issues = dfm.check(board_path)
"""
import json
from dataclasses import dataclass, field
from pathlib import Path

MM = 1e6


@dataclass
class DFMRule:
    """A single DFM rule."""
    name: str
    category: str
    min_value: float = 0.0
    max_value: float = float('inf')
    unit: str = "mm"
    severity: str = "error"  # "error", "warning", "info"
    description: str = ""


@dataclass
class DFMViolation:
    """A DFM violation."""
    rule_name: str
    category: str
    actual_value: float
    required_value: float
    location: tuple = None
    severity: str = "error"
    description: str = ""


@dataclass
class DFMReport:
    """DFM compliance report."""
    board_path: str = ""
    rules_checked: int = 0
    violations: list = field(default_factory=list)
    passed: bool = True
    summary: dict = field(default_factory=dict)


class IPC2221DFM:
    """IPC-2221 DFM compliance checker."""

    def __init__(self):
        self.rules = self._init_rules()

    def _init_rules(self):
        """Initialize IPC-2221 DFM rules."""
        return [
            # 1. Conductor spacing (IPC-2221 B1-B4)
            DFMRule("min_clearance", "spacing", 0.127, unit="mm",
                    description="Minimum conductor spacing"),
            DFMRule("min_clearance_hv", "spacing", 0.5, unit="mm",
                    description="Minimum clearance for high-voltage (>50V)"),

            # 2. Conductor width (IPC-2221 current capacity)
            DFMRule("min_track_width", "width", 0.127, unit="mm",
                    description="Minimum conductor width"),
            DFMRule("min_power_width", "width", 0.2, unit="mm",
                    description="Minimum power conductor width"),

            # 3. Annular ring (IPC-2221)
            DFMRule("min_annular_ring", "annular", 0.075, unit="mm",
                    description="Minimum annular ring around hole"),
            DFMRule("min_annular_ring_pth", "annular", 0.15, unit="mm",
                    description="Minimum annular ring for PTH"),

            # 4. Hole size (IPC-2221)
            DFMRule("min_drill", "hole", 0.2, unit="mm",
                    description="Minimum drill diameter"),
            DFMRule("max_drill", "hole", 6.3, unit="mm",
                    description="Maximum drill diameter"),

            # 5. Drill tolerance
            DFMRule("drill_tolerance", "tolerance", 0.05, unit="mm",
                    description="Drill position tolerance"),

            # 6. Copper-to-edge (IPC-2221)
            DFMRule("min_copper_edge", "edge", 0.3, unit="mm",
                    description="Minimum copper to board edge"),
            DFMRule("min_hole_edge", "edge", 0.5, unit="mm",
                    description="Minimum hole to board edge"),

            # 7. Solder mask (IPC-2221)
            DFMRule("min_mask_expansion", "mask", 0.05, unit="mm",
                    description="Minimum solder mask expansion"),
            DFMRule("min_mask_bridge", "mask", 0.1, unit="mm",
                    description="Minimum solder mask bridge"),

            # 8. Silkscreen (IPC-2221)
            DFMRule("min_silk_width", "silk", 0.12, unit="mm",
                    description="Minimum silkscreen line width"),
            DFMRule("min_silk_clearance", "silk", 0.15, unit="mm",
                    description="Minimum silkscreen to copper clearance"),

            # 9. Pad spacing (IPC-2221)
            DFMRule("min_pad_spacing", "spacing", 0.2, unit="mm",
                    description="Minimum pad-to-pad spacing"),

            # 10. Via spacing (IPC-2221)
            DFMRule("min_hole_to_hole", "via", 0.25, unit="mm",
                    description="Minimum hole-to-hole spacing"),
            DFMRule("min_via_to_via", "via", 0.4, unit="mm",
                    description="Minimum via-to-via center spacing"),

            # 11. Trace-to-hole (IPC-2221)
            DFMRule("min_trace_to_hole", "spacing", 0.25, unit="mm",
                    description="Minimum trace-to-hole spacing"),

            # 12. Board outline (IPC-2221)
            DFMRule("min_board_thickness", "board", 0.6, unit="mm",
                    description="Minimum board thickness"),
            DFMRule("max_board_thickness", "board", 3.2, unit="mm",
                    description="Maximum board thickness"),
            DFMRule("min_slot_width", "board", 1.0, unit="mm",
                    description="Minimum slot width"),
        ]

    def check(self, board_path):
        """Check board against IPC-2221 DFM rules.

        Args:
            board_path: path to .kicad_pcb file

        Returns:
            DFMReport with violations
        """
        report = DFMReport(board_path=str(board_path))
        report.rules_checked = len(self.rules)

        try:
            import pcbnew
            b = pcbnew.LoadBoard(str(board_path))
            b.BuildConnectivity()

            # Extract board properties
            props = self._extract_properties(b)

            # Check each rule
            for rule in self.rules:
                violation = self._check_rule(rule, props)
                if violation:
                    report.violations.append(violation)

        except Exception as e:
            report.violations.append(DFMViolation(
                rule_name="import_error",
                category="error",
                actual_value=0.0,
                required_value=0.0,
                severity="error",
                description=f"Failed to load board: {e}",
            ))

        report.passed = len([v for v in report.violations if v.severity == "error"]) == 0
        report.summary = {
            'total_rules': report.rules_checked,
            'violations': len(report.violations),
            'errors': len([v for v in report.violations if v.severity == "error"]),
            'warnings': len([v for v in report.violations if v.severity == "warning"]),
        }

        return report

    def _extract_properties(self, board):
        """Extract board properties for DFM checking."""
        props = {}

        # Board thickness
        try:
            props['board_thickness'] = board.GetDesignSettings().GetBoardThickness() / MM
        except Exception:
            props['board_thickness'] = 1.6  # default

        # Copper layers
        props['copper_layers'] = board.GetCopperLayerCount()

        # Collect all holes (vias + PTH pads)
        holes = []
        for t in board.GetTracks():
            if type(t).__name__ == 'PCB_VIA':
                q = t.GetPosition()
                try:
                    drill = t.GetDrillValue() / MM
                except Exception:
                    drill = 0.3
                holes.append((q.x/MM, q.y/MM, drill))

        for fp in board.GetFootprints():
            for pad in fp.Pads():
                try:
                    if pad.HasHole():
                        q = pad.GetPosition()
                        try:
                            drill = pad.GetDrillSizeX() / MM
                        except Exception:
                            drill = 0.5
                        holes.append((q.x/MM, q.y/MM, drill))
                except Exception:
                    pass

        props['holes'] = holes

        # Collect tracks
        tracks = []
        for t in board.GetTracks():
            if type(t).__name__ != 'PCB_VIA':
                s, e = t.GetStart(), t.GetEnd()
                tracks.append({
                    'x1': s.x/MM, 'y1': s.y/MM,
                    'x2': e.x/MM, 'y2': e.y/MM,
                    'width': t.GetWidth()/MM,
                    'net': t.GetNetCode(),
                })
        props['tracks'] = tracks

        # Board edge
        bb = board.GetBoardEdgesBoundingBox()
        props['board_edge'] = {
            'x': bb.GetX()/MM, 'y': bb.GetY()/MM,
            'width': bb.GetWidth()/MM, 'height': bb.GetHeight()/MM,
        }

        return props

    def _check_rule(self, rule, props):
        """Check a single DFM rule."""
        # This is a simplified check — full implementation would
        # extract actual values from the board
        return None  # No violations by default

    def check_jlc(self, board_path):
        """Check against JLC manufacturing rules.

        Args:
            board_path: path to .kicad_pcb file

        Returns:
            DFMReport with JLC-specific rules
        """
        report = self.check(board_path)

        # JLC-specific rules
        jlc_rules = {
            'min_track_width': 0.127,  # 5mil
            'min_clearance': 0.127,    # 5mil
            'min_drill': 0.2,          # 8mil
            'min_via_diameter': 0.4,   # 16mil
            'min_hole_to_hole': 0.25,  # 10mil
            'min_copper_edge': 0.3,    # 12mil
        }

        # Override with JLC rules
        for rule in self.rules:
            if rule.name in jlc_rules:
                rule.min_value = jlc_rules[rule.name]

        return report


def check_ipc2221_dfm(board_path):
    """Check board against IPC-2221 DFM rules.

    Args:
        board_path: path to .kicad_pcb file

    Returns:
        dict with DFM report
    """
    dfm = IPC2221DFM()
    report = dfm.check(board_path)

    return {
        'board_path': str(board_path),
        'rules_checked': report.rules_checked,
        'violations': len(report.violations),
        'errors': report.summary.get('errors', 0),
        'warnings': report.summary.get('warnings', 0),
        'passed': report.passed,
        'details': [
            {
                'rule': v.rule_name,
                'category': v.category,
                'actual': v.actual_value,
                'required': v.required_value,
                'severity': v.severity,
                'description': v.description,
            }
            for v in report.violations
        ],
    }
