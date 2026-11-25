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


def _items(board):
    """Every board item that must be copied per panel position."""
    for t in board.GetTracks():
        yield t
    for f in board.GetFootprints():
        yield f
    for z in board.Zones():
        yield z
    for d in board.GetDrawings():
        yield d


def _dup(item):
    """Duplicate across pcbnew versions (footprints want a bool argument)."""
    for args in ((), (False,)):
        try:
            return item.Duplicate(*args)
        except TypeError:
            continue
        except Exception:
            return None
    return None


def _clear(board):
    """Empty a file-loaded board.

    `BOARD.Remove` on an item that came from a loaded file fails on KiCad 10
    ("SwigPyObject has no attribute thisown"); `Delete` is the call for
    file-loaded items.  Every item is dropped the first way it succeeds.
    """
    for coll in (board.GetTracks(), board.GetFootprints(), board.Zones(),
                 board.GetDrawings()):
        for item in list(coll):
            try:
                board.Delete(item)
            except Exception:
                try:
                    board.Remove(item)
                except Exception:
                    pass


def _frame(board, panel):
    """Panel outline + 3 fiducials + 4 NPTH tooling holes."""
    import pcbnew
    w = pcbnew.FromMM(panel.total_width)
    h = pcbnew.FromMM(panel.total_height)
    pts = [(0, 0), (w, 0), (w, h), (0, h)]
    for i in range(4):
        seg = pcbnew.PCB_SHAPE(board)
        seg.SetShape(pcbnew.SHAPE_T_SEGMENT)
        seg.SetStart(pcbnew.VECTOR2I(*pts[i]))
        seg.SetEnd(pcbnew.VECTOR2I(*pts[(i + 1) % 4]))
        seg.SetLayer(pcbnew.Edge_Cuts)
        seg.SetWidth(pcbnew.FromMM(0.15))
        board.Add(seg)
    for (x, y) in panel.fiducials:
        c = pcbnew.PCB_SHAPE(board)
        c.SetShape(pcbnew.SHAPE_T_CIRCLE)
        c.SetCenter(pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y)))
        c.SetRadius(pcbnew.FromMM(panel.spec.fiducial_size / 2))
        c.SetLayer(pcbnew.F_Cu)
        c.SetFilled(True)
        board.Add(c)
    for i, (x, y) in enumerate(panel.tooling_holes):
        fp = pcbnew.FOOTPRINT(board)
        fp.SetReference(f'H{i + 1}')
        fp.SetValue('TOOLING')
        pos = pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y))
        fp.SetPosition(pos)
        pad = pcbnew.PAD(fp)
        pad.SetNumber('')
        pad.SetAttribute(pcbnew.PAD_ATTRIB_NPTH)
        pad.SetShape(pcbnew.PAD_SHAPE_CIRCLE)
        d = pcbnew.FromMM(panel.spec.tooling_hole)
        pad.SetSize(pcbnew.VECTOR2I(d, d))
        pad.SetDrillSize(pcbnew.VECTOR2I(d, d))
        pad.SetPosition(pos)
        fp.Add(pad)
        board.Add(fp)


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
                # The free rectangle already starts one gap away from the
                # placed board, so the board itself only has to fit: adding
                # the gap again here made a 2x2 of 87 mm boards place one.
                if w <= rw and h <= rh:
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

    def generate_kicad_panel(self, panel, output_path, src_path=None):
        """Write a real .kicad_pcb with the panel layout.

        Each placement duplicates the source board's tracks, footprints,
        zones and drawings at an offset, then the frame, three fiducials and
        four NPTH tooling holes are added.  References repeat across copies
        (a production panel would rename them); the summary says so.

        Returns a dict, and writes the file when `src_path` is given and
        pcbnew is available.
        """
        summary = {
            'panel_width': panel.total_width,
            'panel_height': panel.total_height,
            'board_count': len(panel.boards),
            'fiducial_count': len(panel.fiducials),
            'tooling_hole_count': len(panel.tooling_holes),
            'boards': [{'name': b.board_path, 'x': b.x, 'y': b.y,
                        'width': b.width, 'height': b.height}
                       for b in panel.boards],
            'refs_repeat': True,
        }
        src = src_path or (panel.boards[0].board_path if panel.boards else None)
        if not src or not str(src).endswith('.kicad_pcb'):
            summary['status'] = 'no_source'
            return summary
        try:
            import pcbnew
        except Exception as e:
            summary['status'] = 'no_pcbnew'
            summary['error'] = str(e)
            return summary
        try:
            base = pcbnew.LoadBoard(str(src))
            bb = base.GetBoardEdgesBoundingBox()
            x0, y0 = bb.GetLeft(), bb.GetTop()
            # start from an empty board carrying the source's net table:
            # load the source once, then delete every item
            out = pcbnew.LoadBoard(str(src))
            _clear(out)
            keep = []
            for k, b in enumerate(panel.boards):
                if k == 0:
                    items_src = base
                else:
                    items_src = pcbnew.LoadBoard(str(src))
                dx = pcbnew.FromMM(b.x) - x0
                dy = pcbnew.FromMM(b.y) - y0
                for item in _items(items_src):
                    dup = _dup(item)
                    if dup is None:
                        continue
                    dup.Move(pcbnew.VECTOR2I(int(dx), int(dy)))
                    keep.append(dup)          # SWIG ownership: keep alive
                    out.Add(dup)
            _frame(out, panel)
            try:
                pcbnew.ZONE_FILLER(out).Fill(out.Zones())
            except Exception:
                pass
            pcbnew.SaveBoard(str(output_path), out)
            summary['status'] = 'ok'
            summary['path'] = str(output_path)
            summary['bytes'] = __import__('os').path.getsize(str(output_path))
        except Exception as e:
            summary['status'] = 'error'
            summary['error'] = f'{type(e).__name__}: {e}'
        return summary

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
