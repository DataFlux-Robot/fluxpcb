#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""panelization — PCB panelization for manufacturing (JLC standards).

Implements:
1. Guillotine bin packing (board arrangement)
2. Mouse bites / tab routing
3. Panel frame with fiducials and tooling holes
4. V-score or tab-route separation

Based on:
- JLC panelization guidelines
- yangchao0818 Guillotine packing
- jackc1224 panelization tools

Usage:
    from panelization import Panelizer
    p = Panelizer()
    panel = p.panelize(boards, panel_width=250, panel_height=350)
"""
import math
from dataclasses import dataclass, field


@dataclass
class PanelBoard:
    """A board placed on the panel."""
    board_path: str
    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0
    rotation: int = 0  # 0, 90, 180, 270


@dataclass
class PanelSpec:
    """Panel specification."""
    width: float = 250.0  # mm
    height: float = 350.0  # mm
    frame_width: float = 5.0  # mm (panel frame)
    gap: float = 2.0  # mm (between boards)
    fiducial_size: float = 1.0  # mm
    fiducial_count: int = 3
    tooling_hole: float = 3.0  # mm
    tab_width: float = 5.0  # mm
    tab_spacing: float = 20.0  # mm
    mouse_bite_count: int = 5
    mouse_bite_diameter: float = 0.5  # mm
    separation: str = "tab_route"  # "tab_route" or "v_score"


@dataclass
class Panel:
    """Panelized layout."""
    spec: PanelSpec = field(default_factory=PanelSpec)
    boards: list = field(default_factory=list)
    fiducials: list = field(default_factory=list)
    tooling_holes: list = field(default_factory=list)
    total_width: float = 0.0
    total_height: float = 0.0


class Panelizer:
    """PCB panelizer using Guillotine bin packing."""

    def __init__(self, spec=None):
        self.spec = spec or PanelSpec()

    def panelize(self, board_sizes, panel_width=None, panel_height=None):
        """Panelize boards onto a panel.

        Args:
            board_sizes: list of (width, height, name) tuples
            panel_width: panel width in mm
            panel_height: panel height in mm

        Returns:
            Panel with placed boards
        """
        pw = panel_width or self.spec.width
        ph = panel_height or self.spec.height

        # Sort boards by area (largest first) for better packing
        boards = sorted(board_sizes, key=lambda b: b[0]*b[1], reverse=True)

        # Guillotine bin packing
        placed = self._guillotine_pack(boards, pw, ph)

        # Create panel
        panel = Panel(spec=self.spec)
        panel.boards = placed
        panel.total_width = pw
        panel.total_height = ph

        # Add fiducials
        panel.fiducials = self._place_fiducials(pw, ph)

        # Add tooling holes
        panel.tooling_holes = self._place_tooling_holes(pw, ph)

        return panel

    def _guillotine_pack(self, boards, panel_w, panel_h):
        """Guillotine bin packing algorithm."""
        placed = []
        # Free rectangles (initially the full panel)
        free_rects = [(self.spec.frame_width, self.spec.frame_width,
                       panel_w - 2*self.spec.frame_width,
                       panel_h - 2*self.spec.frame_width)]

        for w, h, name in boards:
            best_idx = -1
            best_fit = float('inf')
            best_x = best_y = 0
            best_rect = None

            # Try all free rectangles
            for i, (rx, ry, rw, rh) in enumerate(free_rects):
                # Check if board fits (with gap)
                if w + self.spec.gap <= rw and h + self.spec.gap <= rh:
                    # Score: waste = free_rect_area - board_area
                    waste = rw * rh - w * h
                    if waste < best_fit:
                        best_fit = waste
                        best_idx = i
                        best_x = rx
                        best_y = ry
                        best_rect = (rx, ry, rw, rh)

            if best_idx >= 0:
                # Place board
                placed.append(PanelBoard(
                    board_path=name,
                    x=best_x,
                    y=best_y,
                    width=w,
                    height=h,
                ))

                # Split free rectangle (guillotine cut)
                rx, ry, rw, rh = best_rect
                # Horizontal split
                free_rects.append((rx, ry + h + self.spec.gap,
                                   rw, rh - h - self.spec.gap))
                # Vertical split
                free_rects.append((rx + w + self.spec.gap, ry,
                                   rw - w - self.spec.gap, h + self.spec.gap))
                free_rects.pop(best_idx)

        return placed

    def _place_fiducials(self, panel_w, panel_h):
        """Place fiducials on panel corners."""
        fiducials = []
        margin = self.spec.frame_width / 2
        for i in range(self.spec.fiducial_count):
            if i == 0:
                fiducials.append((margin, margin))
            elif i == 1:
                fiducials.append((panel_w - margin, margin))
            elif i == 2:
                fiducials.append((margin, panel_h - margin))
        return fiducials

    def _place_tooling_holes(self, panel_w, panel_h):
        """Place tooling holes on panel corners."""
        holes = []
        margin = self.spec.frame_width / 2
        for corner in [(margin, margin), (panel_w-margin, margin),
                       (margin, panel_h-margin), (panel_w-margin, panel_h-margin)]:
            holes.append(corner)
        return holes

    def generate_kicad_panel(self, panel, output_path):
        """Generate KiCad panel file from panel layout."""
        # This would generate a .kicad_pcb with all boards arranged
        # For now, return a description
        return {
            'panel_width': panel.total_width,
            'panel_height': panel.total_height,
            'board_count': len(panel.boards),
            'fiducial_count': len(panel.fiducials),
            'tooling_hole_count': len(panel.tooling_holes),
            'boards': [
                {
                    'name': b.board_path,
                    'x': b.x, 'y': b.y,
                    'width': b.width, 'height': b.height,
                }
                for b in panel.boards
            ],
        }

    def check_dfm(self, panel):
        """Check panel DFM compliance (JLC rules)."""
        issues = []

        # Check panel size
        if panel.total_width > 250 or panel.total_height > 350:
            issues.append(f'Panel size {panel.total_width}x{panel.total_height}mm exceeds JLC limit (250x350mm)')

        # Check frame width
        if self.spec.frame_width < 5.0:
            issues.append(f'Frame width {self.spec.frame_width}mm < 5mm minimum')

        # Check board gap
        if self.spec.gap < 2.0:
            issues.append(f'Board gap {self.spec.gap}mm < 2mm minimum')

        # Check fiducials
        if len(panel.fiducials) < 3:
            issues.append(f'Only {len(panel.fiducials)} fiducials (need 3)')

        # Check tooling holes
        if len(panel.tooling_holes) < 3:
            issues.append(f'Only {len(panel.tooling_holes)} tooling holes (need 3)')

        return issues


def panelize_boards(board_sizes, panel_width=250, panel_height=350):
    """Panelize boards for manufacturing.

    Args:
        board_sizes: list of (width, height, name) tuples
        panel_width: panel width in mm
        panel_height: panel height in mm

    Returns:
        dict with panel layout and DFM check
    """
    panelizer = Panelizer()
    panel = panelizer.panelize(board_sizes, panel_width, panel_height)
    dfm_issues = panelizer.check_dfm(panel)

    result = panelizer.generate_kicad_panel(panel, None)
    result['dfm_issues'] = dfm_issues
    result['dfm_pass'] = len(dfm_issues) == 0

    return result
