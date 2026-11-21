#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bench_runner — Benchmark runner for autorouter-bench / PCBWorld.

Two engines:
  --engine freerouting : Freerouting backend + skill-package repair operators
                         (fast; 100% on 2-layer benchmarks)
  --engine ncer        : NCER deep router (slow; complex boards)

After routing the runner ALWAYS:
  1. refills zones (SKILL.md: any autorouter output must be zone-refilled)
  2. applies repair operators via repair_drc (copper text, waivers, endpoints)
  3. re-runs DRC for verification

Usage:
    python bench_runner.py <board.kicad_pcb> <out.kicad_pcb> [--timeout N] [--engine freerouting|ncer]
"""
from __future__ import annotations

import json
import os
import sys
import time


def _ensure_fluxpcb_importable():
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    if root not in sys.path:
        sys.path.insert(0, root)
    return root


def run_board(board_path: str, output_path: str, timeout_s: int = 1200,
              engine: str = "freerouting") -> dict:
    """Route a board and repair DRC.

    Args:
        board_path: Input .kicad_pcb file
        output_path: Output .kicad_pcb file
        timeout_s: Routing timeout in seconds
        engine: 'freerouting' or 'ncer'

    Returns:
        dict with completion + DRC metrics
    """
    t0 = time.time()
    _ensure_fluxpcb_importable()

    work_dir = os.path.dirname(os.path.abspath(output_path)) or '.'
    os.makedirs(work_dir, exist_ok=True)

    # ── Step 1: route ──────────────────────────────────────────────
    if engine == "freerouting":
        from fluxpcb.router.freerouting import FreeroutingBackend
        backend = FreeroutingBackend()
        ok, reason = backend.available()
        if not ok:
            return {'error': f'freerouting unavailable: {reason}',
                    'completionPct': 0.0, 'seconds': time.time() - t0}
        # FLUXPCB_USE_BOARD_RULES=1 routes at the board's own design rules
        # (the DSN export otherwise falls back to KiCad's default 0.2mm).
        use_board_rules = os.environ.get('FLUXPCB_USE_BOARD_RULES', '0') == '1'
        fr = backend.route(board_path, output_path, timeout_s=timeout_s,
                           use_board_rules=use_board_rules)
        if not fr.routed:
            return {'error': f'refresh routing failed: {fr.error}',
                    'completionPct': 0.0, 'seconds': fr.seconds}
        route_seconds = fr.seconds
        engine_label = f'freerouting(passes={fr.passes},board_rules={use_board_rules})'
    else:
        # NCER backend (direct, same as before)
        import pcbnew
        import routegrid as rg
        sys.path.insert(0, os.path.join('E:/DATA/vscode/ZCODE',
                                        'autorouter-bench', 'tools'))
        from fluxpcb_ncer import NCERRouter

        b = pcbnew.LoadBoard(board_path)
        if b is None:
            return {'error': 'Failed to load board', 'completionPct': 0.0}
        b.BuildConnectivity()
        lids = rg.signal_layer_ids(b)

        net_pads = {}
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

        router = NCERRouter(b, lids)
        result = router.run(net_pads, timeout_s)
        try:
            b.Save(output_path)
        except Exception:
            pass
        route_seconds = result.get('seconds', 0)
        engine_label = 'ncer'

    # ── Step 2: refill zones + repair operators + DRC ─────────────
    repair_summary = {}
    try:
        import pcbnew
        from fluxpcb.verifiers.operators import RepairOperators, Violation

        # 2a. refill zones (SKILL.md rule)
        b = pcbnew.LoadBoard(output_path)
        if b is not None:
            filler = pcbnew.ZONE_FILLER(b)
            filler.Fill(b.Zones())
            b.Save(output_path)

        # 2b. repair operators (copper text, waivers, endpoints, widen)
        #     Loop: widening can expose marginal clearance shortfalls that a
        #     follow-up waiver pass clears, so iterate while improving.
        from fluxpcb.repair_drc import run_drc, repair_with_operators
        drc_json = output_path + '.drc_before.json'
        prev_errs = None
        for _round in range(3):
            run_drc(output_path, drc_json)
            try:
                with open(drc_json) as _f:
                    _d = json.load(_f)
                n_errs = len([v for v in _d.get('violations', [])
                              if v.get('severity') == 'error'])
            except Exception:
                n_errs = 0
            if prev_errs is not None and n_errs >= prev_errs:
                break  # no further improvement
            prev_errs = n_errs
            if n_errs == 0:
                break
            repair_summary = repair_with_operators(output_path, drc_json)

        # 2c. refill again + final measurement
        b = pcbnew.LoadBoard(output_path)
        if b is not None:
            filler = pcbnew.ZONE_FILLER(b)
            filler.Fill(b.Zones())
            b.Save(output_path)

        b = pcbnew.LoadBoard(output_path)
        b.BuildConnectivity()
        try:
            unconnected = b.GetConnectivity().GetUnconnectedCount(True)
        except TypeError:
            unconnected = b.GetConnectivity().GetUnconnectedCount()
        vias = sum(1 for t in b.GetTracks()
                   if type(t).__name__ == 'PCB_VIA')
    except Exception as e:
        repair_summary = {'error': str(e)}
        unconnected = -1
        vias = 0

    elapsed = time.time() - t0

    # Completion vs ratsnest baseline
    try:
        import pcbnew
        b0 = pcbnew.LoadBoard(board_path)
        b0.BuildConnectivity()
        rats = b0.GetConnectivity().GetUnconnectedCount(True)
    except Exception:
        rats = 0

    completion = 100.0 * (1 - unconnected / rats) if rats > 0 and unconnected >= 0 else 0.0

    return {
        'completionPct': round(completion, 1),
        'unconnected': unconnected,
        'ratsnest': rats,
        'vias': vias,
        'seconds': round(elapsed, 1),
        'routeSeconds': route_seconds,
        'engine': engine_label,
        'timedOut': route_seconds > timeout_s,
        'repair': {
            'operators': repair_summary.get('operators_run', []),
            'repaired': repair_summary.get('repaired', 0),
            'failed': repair_summary.get('failed', 0),
        },
    }


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print('Usage: python bench_runner.py <board> <out> [--timeout N] [--engine e]')
        sys.exit(1)

    board_path = sys.argv[1]
    output_path = sys.argv[2]
    timeout_s = 1200
    engine = 'freerouting'
    if '--timeout' in sys.argv:
        timeout_s = int(sys.argv[sys.argv.index('--timeout') + 1])
    if '--engine' in sys.argv:
        engine = sys.argv[sys.argv.index('--engine') + 1]

    result = run_board(board_path, output_path, timeout_s, engine)
    print(json.dumps(result))
