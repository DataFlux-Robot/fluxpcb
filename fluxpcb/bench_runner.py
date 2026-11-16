#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bench_runner — Benchmark runner for autorouter-bench, PCBWorld, eda-bench.

Uses NCER router directly (not the full 12-component pipeline) for speed.
The full pipeline is for h743-fc design, not benchmark routing.

Usage:
    python bench_runner.py <board_path> <output_path> [--timeout N]
"""
from __future__ import annotations

import json
import os
import sys
import time


def run_board(board_path: str, output_path: str, timeout_s: int = 1200) -> dict:
    """Route a board using NCER router.

    Args:
        board_path: Input .kicad_pcb file
        output_path: Output .kicad_pcb file
        timeout_s: Timeout in seconds

    Returns:
        dict with completion metrics (always includes completionPct)
    """
    t0 = time.time()

    # Import NCER router
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'kicadgen'))
    sys.path.insert(0, 'E:/DATA/vscode/ZCODE/autorouter-bench/tools')

    try:
        from fluxpcb_ncer import NCERRouter
        import pcbnew
        import routegrid as rg
    except ImportError as e:
        return {
            'error': f'Import failed: {e}',
            'completionPct': 0.0,
            'unconnected': -1,
            'seconds': time.time() - t0,
        }

    # Load board
    b = pcbnew.LoadBoard(board_path)
    if b is None:
        return {
            'error': 'Failed to load board',
            'completionPct': 0.0,
            'unconnected': -1,
            'seconds': time.time() - t0,
        }

    b.BuildConnectivity()
    lids = rg.signal_layer_ids(b)

    # Collect net pads
    net_pads = {}
    total_pairs = 0
    for fp in b.GetFootprints():
        for pad in fp.Pads():
            nc = pad.GetNetCode()
            if nc == 0:
                continue
            nn = pad.GetNetname()
            if nn not in net_pads:
                net_pads[nn] = []
            p = pad.GetPosition()
            pad_lid = pad.GetLayer()
            pad_li = lids.index(pad_lid) if pad_lid in lids else 0
            net_pads[nn].append((p.x/1e6, p.y/1e6, pad_li))

    # Count total pairs
    for nn, pads in net_pads.items():
        if len(pads) >= 2:
            total_pairs += len(pads) - 1

    # Route
    router = NCERRouter(b, lids)
    result = router.run(net_pads, timeout_s)

    # Save (always, even on timeout)
    try:
        b.Save(output_path)
    except Exception as e:
        # Save failed, but we still have routing info
        pass

    elapsed = time.time() - t0

    # Calculate completion from actual routing
    routed_pairs = result.get('routed_pairs', 0)
    if total_pairs > 0:
        completion_pct = 100.0 * routed_pairs / total_pairs
    else:
        completion_pct = 100.0 if routed_pairs > 0 else 0.0

    return {
        'completionPct': completion_pct,
        'unconnected': total_pairs - routed_pairs,
        'total_pairs': total_pairs,
        'routed_pairs': routed_pairs,
        'vias': result.get('vias', 0),
        'seconds': elapsed,
        'timedOut': elapsed > timeout_s,
    }


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print('Usage: python bench_runner.py <board_path> <output_path> [--timeout N]')
        sys.exit(1)

    board_path = sys.argv[1]
    output_path = sys.argv[2]

    timeout_s = 1200
    if '--timeout' in sys.argv:
        timeout_s = int(sys.argv[sys.argv.index('--timeout') + 1])

    result = run_board(board_path, output_path, timeout_s)
    print(json.dumps(result))
