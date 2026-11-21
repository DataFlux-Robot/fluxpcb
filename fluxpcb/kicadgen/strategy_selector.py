#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""strategy_selector — Adaptive routing strategy selection.

Automatically selects the best routing strategy based on board diagnosis.

Usage:
    from strategy_selector import select_strategy, apply_strategy
    strategy = select_strategy(diagnosis)
    apply_strategy(router, strategy)
"""
import sys
from dataclasses import dataclass, field
from pathlib import Path

MM = 1e6
sys.path.insert(0, str(Path(__file__).parent))
import routegrid as rg


@dataclass
class RoutingStrategy:
    """A complete routing strategy."""
    name: str
    description: str

    # Grid parameters
    pad_margin: float = 0.15
    track_margin: float = 0.15
    use_optimistic_grid: bool = False

    # Algorithm parameters
    use_exact_wavefront: bool = False
    fim_iters: int = 500
    use_astar_fallback: bool = True
    astar_cap: int = 500000

    # Power plane
    pour_power_nets: bool = False
    power_net_names: list = field(default_factory=lambda: ['GND'])

    # Verification
    clearance_margin: float = 0.15
    use_exact_judge: bool = True

    # Routing order
    mst_ordering: bool = True
    hardest_first: bool = False

    # PNS
    use_pns_nudge: bool = True
    nudge_max_shift: float = 0.32


def select_strategy(diagnosis):
    """Select routing strategy based on diagnosis.

    Args:
        diagnosis: dict with failure_modes, board params, etc.

    Returns:
        RoutingStrategy: the selected strategy
    """
    failure_modes = diagnosis.get('failure_modes', {})
    board_params = diagnosis.get('board_params', {})

    # Start with baseline
    strategy = RoutingStrategy(name='baseline', description='Current NCER')

    # Adapt to board parameters
    min_clearance = board_params.get('min_clearance', 0.127)
    min_track_width = board_params.get('min_track_width', 0.15)

    # Derive margins from board rules
    G = 0.16  # grid resolution
    diag_slack = 0.5 * G * 1.414
    strategy.pad_margin = min_clearance + min_track_width / 2 + diag_slack
    strategy.track_margin = min_clearance + min_track_width / 2
    strategy.clearance_margin = min_clearance

    # Adapt to failure modes
    if 'congested_pads' in failure_modes:
        strategy.name = 'optimistic_grid'
        strategy.description = 'Relaxed grid for congested pads'
        strategy.use_optimistic_grid = True
        strategy.pad_margin = min_clearance  # tighter margin
        strategy.track_margin = min_clearance

    if 'power_nets' in failure_modes:
        strategy.pour_power_nets = True
        power_nets = diagnosis.get('power_net_names', ['GND'])
        strategy.power_net_names = power_nets
        if strategy.name == 'baseline':
            strategy.name = 'power_plane'
            strategy.description = 'With power plane pour'

    if 'long_pairs' in failure_modes:
        strategy.use_exact_wavefront = True
        strategy.fim_iters = 999999
        strategy.astar_cap = 1000000
        if strategy.name == 'baseline':
            strategy.name = 'exact_wavefront'
            strategy.description = 'With exact wavefront solver'

    if 'high_drc' in failure_modes:
        strategy.clearance_margin = min_clearance * 1.5
        strategy.use_exact_judge = True

    # Combined strategy if multiple failure modes
    if len(failure_modes) > 2:
        strategy.name = 'combined'
        strategy.description = 'All adaptations combined'

    return strategy


def apply_strategy(router, strategy):
    """Apply routing strategy to a router instance.

    Args:
        router: NCERRouter instance
        strategy: RoutingStrategy to apply
    """
    # Apply grid parameters
    import routegrid as rg
    rg.PAD_MARGIN = strategy.pad_margin
    rg.TRACK_MARGIN = strategy.track_margin

    # Apply algorithm parameters
    router.use_exact_wavefront = strategy.use_exact_wavefront
    router.fim_iters = strategy.fim_iters
    router.astar_cap = strategy.astar_cap

    # Apply verification
    router.clearance_margin = strategy.clearance_margin
    router.use_exact_judge = strategy.use_exact_judge

    # Apply PNS
    router.use_pns_nudge = strategy.use_pns_nudge
    router.nudge_max_shift = strategy.nudge_max_shift

    # Invalidate caches
    if hasattr(router, 'router') and hasattr(router.router, 'invalidate_cache'):
        router.router.invalidate_cache()

    return router


def diagnose_board(board_path):
    """Diagnose board to select strategy.

    Returns:
        dict: diagnosis with failure_modes and board_params
    """
    import pcbnew
    import json

    b = pcbnew.LoadBoard(str(board_path))
    b.BuildConnectivity()
    lids = rg.signal_layer_ids(b)

    # Read board rules
    pro_path = str(board_path).replace('.kicad_pcb', '.kicad_pro')
    board_params = {}
    if Path(pro_path).exists():
        pro = json.loads(Path(pro_path).read_text())
        rules = pro.get('board', {}).get('design_settings', {}).get('rules', {})
        board_params = {
            'min_clearance': rules.get('min_clearance', 0.127),
            'min_track_width': rules.get('min_track_width', 0.15),
            'min_hole_clearance': rules.get('min_hole_clearance', 0.25),
            'min_hole_to_hole': rules.get('min_hole_to_hole', 0.25),
            'min_copper_edge_clearance': rules.get('min_copper_edge_clearance', 0.3),
        }

    # Analyze nets
    net_pads = {}
    for fp in b.GetFootprints():
        for pad in fp.Pads():
            nc = pad.GetNetCode()
            if nc == 0:
                continue
            nn = pad.GetNetname()
            net_pads[nn] = net_pads.get(nn, 0) + 1

    # Detect failure modes
    failure_modes = {}
    power_net_names = []

    # Power nets
    for nn, count in net_pads.items():
        if count > 20 or any(kw in nn.upper() for kw in ['GND', 'VCC', 'VDD', 'VSS', 'VBUS']):
            power_net_names.append(nn)
    if power_net_names:
        failure_modes['power_nets'] = sum(net_pads.get(nn, 0) for nn in power_net_names)

    # Congested pads (check grid)
    gm = rg.GridModel(b, lids)
    congested = 0
    for fp in b.GetFootprints():
        for pad in fp.Pads():
            nc = pad.GetNetCode()
            if nc == 0:
                continue
            p = pad.GetPosition()
            gx, gy = int(p.x/MM / rg.GRID), int(p.y/MM / rg.GRID)
            if not (0 <= gx < gm.nx and 0 <= gy < gm.ny):
                continue
            free_count = 0
            for dx in range(-3, 4):
                for dy in range(-3, 4):
                    if 0 <= gx+dx < gm.nx and 0 <= gy+dy < gm.ny:
                        if gm.fixed_free(0, gx+dx, gy+dy, nc):
                            free_count += 1
            if free_count < 10:
                congested += 1
    if congested > 5:
        failure_modes['congested_pads'] = congested

    # Long pairs
    total_pairs = sum(max(0, c-1) for c in net_pads.values())
    if total_pairs > 50:
        failure_modes['long_pairs'] = total_pairs // 3

    return {
        'board_path': str(board_path),
        'board_params': board_params,
        'net_pads': net_pads,
        'power_net_names': power_net_names,
        'failure_modes': failure_modes,
    }
