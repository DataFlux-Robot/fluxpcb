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
    """DFM compliance report.

    `unevaluated` names every rule this checker has no measurement for,
    with the reason.  A rule that was not measured is not a pass.
    """
    board_path: str = ""
    rules_checked: int = 0
    violations: list = field(default_factory=list)
    passed: bool = True
    summary: dict = field(default_factory=dict)
    unevaluated: list = field(default_factory=list)


def _min_hole_gap(holes):
    """Closest edge-to-edge gap between two holes, by a 1 mm bin index."""
    if len(holes) < 2:
        return None, 'fewer than two holes'
    bins = {}
    for i, (x, y, d) in enumerate(holes):
        bins.setdefault((int(x), int(y)), []).append(i)
    best = None
    for (bx, by), ids in bins.items():
        near = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                near.extend(bins.get((bx + dx, by + dy), ()))
        for i in ids:
            xi, yi, di = holes[i]
            for j in near:
                if j <= i:
                    continue
                xj, yj, dj = holes[j]
                gap = ((xi - xj) ** 2 + (yi - yj) ** 2) ** 0.5 - (di + dj) / 2.0
                if best is None or gap < best:
                    best = gap
    if best is None:
        return None, 'no two holes share a bin neighbourhood'
    return max(0.0, best), 'closest hole-to-hole gap'


def _min_copper_edge(props):
    """Closest approach of any track to the board edge (tracks only)."""
    edge = props.get('board_edge') or {}
    tracks = props.get('tracks') or []
    if not edge or not tracks:
        return None, 'no board edge or no tracks'
    x0, y0 = edge['x'], edge['y']
    x1, y1 = x0 + edge['width'], y0 + edge['height']
    best = None
    for t in tracks:
        for (x, y) in ((t['x1'], t['y1']), (t['x2'], t['y2'])):
            d = min(x - x0, x1 - x, y - y0, y1 - y)
            if best is None or d < best:
                best = d
    return max(0.0, best), 'track end nearest the board edge'


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
            DFMReport with violations and, for the rules this checker
            cannot measure, `unevaluated`.
        """
        report = DFMReport(board_path=str(board_path))
        report.rules_checked = len(self.rules)
        self._unevaluated = []

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
        report.unevaluated = list(getattr(self, '_unevaluated', []))
        report.summary = {
            'total_rules': report.rules_checked,
            'violations': len(report.violations),
            'errors': len([v for v in report.violations if v.severity == "error"]),
            'warnings': len([v for v in report.violations if v.severity == "warning"]),
            'unevaluated': len(report.unevaluated),
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
        """Check one DFM rule against the values the board yields.

        Only rules with a real measurement here are judged; the rest go to
        `_unevaluated` with the reason, so a caller can tell "clean" from
        "not measured".  Clearance between arbitrary items is the DRC's
        job (it has the geometry), not this text-level checker's.
        """
        name = rule.name
        measured, where = None, None

        if name == 'min_track_width':
            widths = [t['width'] for t in props.get('tracks') or []
                      if t.get('width')]
            if widths:
                measured = min(widths)
                where = 'narrowest track on the board'
        elif name == 'min_drill' or name == 'max_drill':
            drills = [h[2] for h in props.get('holes') or [] if h[2]]
            if drills:
                measured = min(drills) if name == 'min_drill' else max(drills)
                where = 'via/PTH drills'
        elif name == 'min_board_thickness':
            measured = props.get('board_thickness')
            where = 'board thickness'
        elif name == 'max_board_thickness':
            measured = props.get('board_thickness')
            where = 'board thickness'
        elif name == 'min_hole_to_hole':
            measured, where = _min_hole_gap(props.get('holes') or [])
        elif name == 'min_copper_edge':
            measured, where = _min_copper_edge(props)
        if measured is None:
            self._unevaluated.append({
                'rule': name, 'category': rule.category,
                'why': 'no measurement extracted from the board text',
                'needed_information': [
                    f'a measurement of {name} (or the DRC, which has the '
                    f'geometry)']})
            return None
        bad = False
        if name.startswith('max_'):
            bad = measured > rule.max_value
        else:
            bad = measured < rule.min_value
        if not bad:
            return None
        req = rule.min_value if not name.startswith('max_') else rule.max_value
        return DFMViolation(rule_name=name, category=rule.category,
                            actual_value=round(float(measured), 4),
                            required_value=round(float(req), 4),
                            severity=rule.severity,
                            description=(f'{name}: {measured:.4g} mm '
                                         f'{"over" if name.startswith("max_") else "under"} '
                                         f'{req:.4g} mm ({where})'))

    def apply_limits(self, limits: dict) -> list:
        """Set rule minima from a fab profile before measuring.

        The old `check_jlc` wrote the JLC numbers into the rules AFTER
        `check()` had already run, so the override never reached a
        verdict.  This applies them first and returns the names it set.
        """
        applied = []
        for rule in self.rules:
            if rule.name in limits and limits[rule.name] is not None:
                rule.min_value = float(limits[rule.name])
                applied.append(rule.name)
        return applied

    def check_jlc(self, board_path, profile=None):
        """Check against the dated JLC profile (default: the packaged one)."""
        from fluxpcb.mfg.fab_profile import active_profile
        prof = profile or active_profile()
        applied = self.apply_limits(prof.dfm_limits())
        report = self.check(board_path)
        report.summary['profile'] = prof.profile_id
        report.summary['profile_retrieved'] = prof.retrieved
        report.summary['limits_applied'] = applied
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
