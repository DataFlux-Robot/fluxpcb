"""Cheap, pre-routing board features.

Component 1 selects an arm per board.  A selector may only look at what
is known BEFORE routing — never at the routing outcome — or it is not a
selector but an oracle.  Everything here is read off the placement.
"""
from __future__ import annotations


def board_features(board) -> dict:
    bb = board.GetBoardEdgesBoundingBox()
    area = (bb.GetWidth() / 1e6) * (bb.GetHeight() / 1e6)
    nets, pads, footprints = set(), 0, 0
    for fp in board.GetFootprints():
        footprints += 1
        for pad in fp.Pads():
            pads += 1
            name = pad.GetNetname()
            if name:
                nets.add(name)
    return {
        'copper_layers': int(board.GetCopperLayerCount()),
        'area_mm2': round(area, 1),
        'footprints': footprints,
        'pads': pads,
        'nets': len(nets),
        # placement density: the usual driver of routing difficulty
        'pads_per_mm2': round(pads / area, 4) if area > 0 else None,
    }


def features_of(board_path) -> dict:
    """Features of a board file, plus what its plan can see."""
    import pcbnew

    from .planner import _short_pair_nets, board_rules, build_plan

    board = pcbnew.LoadBoard(str(board_path))
    out = board_features(board)
    rules = board_rules(board)
    out['rule_clearance_mm'] = rules.get('min_clearance')
    out['rule_track_mm'] = rules.get('min_track_width')
    try:
        plan = build_plan(str(board_path), pre_pair_mm=6.0)
        out['capacity'] = plan.capacity.verdict
        out['escape_regions'] = len(plan.escape_regions)
        out['pre_route_candidates'] = len(plan.pre_routed_nets)
    except Exception as e:      # features are evidence; never a crash
        out['plan_error'] = f'{type(e).__name__}: {e}'
    try:
        out['local_nets'] = len(_short_pair_nets(board, 6.0))
    except Exception:
        pass
    return out
