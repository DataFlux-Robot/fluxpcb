#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""board_rules — Board-level parameter adaptation.

Reads .kicad_pro to derive ALL routing parameters.
No hardcoded values — everything comes from the board.

Usage:
    from board_rules import BoardRules
    rules = BoardRules.from_pro('board.kicad_pro')
    rules.pad_margin   # derived from clearance + track_w/2 + diagonal slack
    rules.fim_iters    # derived from grid size
    rules.power_nets   # derived from pad count + netclass patterns
"""
import json
import math
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class NetClassRules:
    """Per-netclass rules."""
    name: str
    clearance: float = 0.127
    track_width: float = 0.15
    via_diameter: float = 0.6
    via_drill: float = 0.35
    diff_pair_width: float = 0.0
    diff_pair_gap: float = 0.0


@dataclass
class BoardRules:
    """All board-derived routing parameters."""
    # Board identity
    board_path: str = ""
    pro_path: str = ""

    # Basic rules (from .kicad_pro board.design_settings.rules)
    min_clearance: float = 0.127
    min_track_width: float = 0.15
    min_hole_clearance: float = 0.25
    min_hole_to_hole: float = 0.25
    min_copper_edge_clearance: float = 0.3
    min_via_diameter: float = 0.4
    min_through_hole_diameter: float = 0.2

    # Netclass rules (from .kicad_pro net_settings.classes)
    net_classes: dict = field(default_factory=dict)  # name -> NetClassRules

    # Derived raster margins (computed, not hardcoded)
    pad_margin: float = 0.27       # clearance + track_w/2 + diagonal slack
    track_margin: float = 0.22     # clearance + track_w/2
    via_block_r: float = 0.35      # via_dia/2 + clearance + track_w/2
    hole_keep: float = 0.38        # drill/2 + min_hole_clearance + track_w/2
    edge_keep: float = 0.55        # min_copper_edge_clearance + track_w/2

    # Stackup
    copper_layers: int = 2
    signal_layers: list = field(default_factory=list)  # pcbnew layer IDs

    # Network roles
    power_nets: dict = field(default_factory=dict)  # net_name -> pad_count
    diff_pairs: list = field(default_factory=list)

    # Algorithm parameters (scaled by board size)
    grid_mm: float = 0.16
    fim_iters: int = 500
    via_cost: float = 8.0
    astar_cap: int = 500000

    @classmethod
    def from_pro(cls, pro_path, board=None):
        """Load rules from .kicad_pro file."""
        rules = cls()
        rules.pro_path = str(pro_path)

        pro = {}
        if Path(pro_path).exists():
            pro = json.loads(Path(pro_path).read_text())

        # Basic rules
        ds = pro.get('board', {}).get('design_settings', {}).get('rules', {})
        rules.min_clearance = ds.get('min_clearance', 0.127)
        rules.min_track_width = ds.get('min_track_width', 0.15)
        rules.min_hole_clearance = ds.get('min_hole_clearance', 0.25)
        rules.min_hole_to_hole = ds.get('min_hole_to_hole', 0.25)
        rules.min_copper_edge_clearance = ds.get('min_copper_edge_clearance', 0.3)
        rules.min_via_diameter = ds.get('min_via_diameter', 0.4)
        rules.min_through_hole_diameter = ds.get('min_through_hole_diameter', 0.2)

        # Netclass rules
        for cls_data in pro.get('net_settings', {}).get('classes', []):
            ncr = NetClassRules(
                name=cls_data.get('name', 'Default'),
                clearance=cls_data.get('clearance', rules.min_clearance),
                track_width=cls_data.get('track_width', rules.min_track_width),
                via_diameter=cls_data.get('via_diameter', 0.6),
                via_drill=cls_data.get('via_drill', 0.35),
                diff_pair_width=cls_data.get('diff_pair_width', 0.0),
                diff_pair_gap=cls_data.get('diff_pair_gap', 0.0),
            )
            rules.net_classes[ncr.name] = ncr

        # Default netclass (fallback)
        default = rules.net_classes.get('Default', NetClassRules())
        tw = default.track_width

        # Derived raster margins
        G = rules.grid_mm
        diag_slack = 0.5 * G * math.sqrt(2)  # worst-case cell-center offset
        rules.pad_margin = rules.min_clearance + tw / 2 + diag_slack
        rules.track_margin = rules.min_clearance + tw / 2
        rules.via_block_r = default.via_diameter / 2 + rules.min_clearance + tw / 2
        rules.hole_keep = default.via_drill / 2 + rules.min_hole_clearance + tw / 2
        rules.edge_keep = rules.min_copper_edge_clearance + tw / 2

        # Board-specific analysis (if board provided)
        if board is not None:
            rules._analyze_board(board)

        return rules

    def _analyze_board(self, board):
        """Analyze board for stackup and network roles."""
        import pcbnew

        # Stackup
        self.copper_layers = board.GetCopperLayerCount()

        # Network roles
        net_pads = {}
        for fp in board.GetFootprints():
            for pad in fp.Pads():
                nc = pad.GetNetCode()
                if nc == 0:
                    continue
                nn = pad.GetNetname()
                net_pads[nn] = net_pads.get(nn, 0) + 1

        # Power nets: large pad count OR netclass pattern
        for nn, count in net_pads.items():
            if count > 20:
                self.power_nets[nn] = count
            elif any(kw in nn.upper() for kw in ['GND', 'VCC', 'VDD', 'VSS', 'VBUS', '+3V', '+5V', '+1V']):
                self.power_nets[nn] = count

        # Diff pairs: netclass with diff_pair_width > 0
        for name, ncr in self.net_classes.items():
            if ncr.diff_pair_width > 0:
                self.diff_pairs.append(name)

    def scale_algo_params(self, nx, ny):
        """Scale algorithm parameters by board grid size."""
        # FIM iterations: need enough for wavefront to reach corners
        self.fim_iters = max(500, nx + ny)
        # A* cap: scale with grid area
        self.astar_cap = max(500000, (nx * ny) // 4)

    def get_netclass_rules(self, net_name):
        """Get rules for a specific net (with netclass pattern matching)."""
        # Direct match
        if net_name in self.net_classes:
            return self.net_classes[net_name]
        # Pattern match (simplified)
        for name, ncr in self.net_classes.items():
            if name != 'Default' and name in net_name:
                return ncr
        # Default
        return self.net_classes.get('Default', NetClassRules())
