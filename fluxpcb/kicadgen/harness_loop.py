#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""harness_loop — HarnessPro-driven adaptive PCB routing.

Core: diagnose→evolve→select loop that automatically adapts to ANY board.

Diagnose: Run router, analyze failures (completion, DRC, failure modes)
Evolve:   Generate candidates (algorithm changes, power planes, routing order)
Select:   Evaluate candidates on target board, pick the best

Usage:
    python harness_loop.py BOARD.kicad_pcb [--budget S] [--output OUT.kicad_pcb]
"""
import json
import math
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

MM = 1e6
sys.path.insert(0, str(Path(__file__).parent))
import routegrid as rg
import geomir


# ── HarnessPro Diagnosis schema ──────────────────────────
@dataclass
class RoutingDiagnosis:
    """Diagnosis of a routing run."""
    board_name: str
    completion_pct: float
    drc_errors: int
    unconnected: int
    total_pairs: int
    routed_pairs: int
    failure_modes: dict = field(default_factory=dict)  # mode -> count
    suggestions: list = field(default_factory=list)  # improvement directions
    severity: float = 1.0


@dataclass
class RoutingCandidate:
    """A candidate improvement strategy."""
    name: str
    description: str
    category: str  # 'algorithm', 'power_plane', 'routing_order', 'grid'
    params: dict = field(default_factory=dict)
    expected_improvement: str = ""
    risk: str = "low"  # 'low', 'medium', 'high'


@dataclass
class RoutingResult:
    """Result of a routing run."""
    candidate_name: str
    completion_pct: float
    drc_errors: int
    unconnected: int
    vias: int
    time_s: float
    success: bool
    score: float = 0.0


# ── Diagnosis: analyze routing failures ──────────────────
def diagnose_routing(board_path, routed_path, net_pads):
    """Analyze routing results and identify failure modes."""
    import pcbnew
    b = pcbnew.LoadBoard(str(routed_path))
    b.BuildConnectivity()

    # Count unconnected items
    conn = b.GetConnectivity()
    unconnected = conn.GetUnconnectedCount(True)

    # Count total pairs
    total_pairs = 0
    for nn, pads in net_pads.items():
        if len(pads) >= 2:
            total_pairs += len(pads) - 1

    # Analyze failure modes
    failure_modes = {}
    suggestions = []

    # Mode 1: pads surrounded by obstacles (src_free low)
    lids = rg.signal_layer_ids(b)
    gm = rg.GridModel(b, lids)
    congested_pads = 0
    for nn, pads in net_pads.items():
        if len(pads) < 2:
            continue
        net = b.GetNetInfo().GetNetItem(nn)
        if net is None:
            continue
        nc = net.GetNetCode()
        for (x, y, li) in pads[:2]:  # check first 2 pads
            gx, gy = int(x / rg.GRID), int(y / rg.GRID)
            if not (0 <= gx < gm.nx and 0 <= gy < gm.ny):
                continue
            free_count = 0
            for dx in range(-3, 4):
                for dy in range(-3, 4):
                    if 0 <= gx+dx < gm.nx and 0 <= gy+dy < gm.ny:
                        if gm.fixed_free(li, gx+dx, gy+dy, nc):
                            free_count += 1
            if free_count < 10:
                congested_pads += 1

    if congested_pads > 0:
        failure_modes['congested_pads'] = congested_pads
        suggestions.append('Use optimistic grid (margin=0) + exact judge')

    # Mode 2: large power nets (GND, VCC)
    power_nets = {}
    for nn, pads in net_pads.items():
        if len(pads) > 20:
            power_nets[nn] = len(pads)
    if power_nets:
        failure_modes['power_nets'] = sum(power_nets.values())
        suggestions.append('Pour power planes (GND, VCC)')

    # Mode 3: long-distance pairs
    long_pairs = 0
    for nn, pads in net_pads.items():
        if len(pads) < 2:
            continue
        for i in range(len(pads)-1):
            d = math.hypot(pads[i][0]-pads[i+1][0], pads[i][1]-pads[i+1][1])
            if d > 20:
                long_pairs += 1
    if long_pairs > 0:
        failure_modes['long_pairs'] = long_pairs
        suggestions.append('Use Exact Wavefront (no iteration limit)')

    # Calculate completion
    routed = max(0, total_pairs - unconnected)
    completion_pct = 100.0 * routed / max(total_pairs, 1)

    return RoutingDiagnosis(
        board_name=str(board_path),
        completion_pct=completion_pct,
        drc_errors=0,  # filled by caller
        unconnected=unconnected,
        total_pairs=total_pairs,
        routed_pairs=routed,
        failure_modes=failure_modes,
        suggestions=suggestions,
        severity=1.0 - completion_pct / 100.0,
    )


# ── Evolve: generate candidate improvements ─────────────
def generate_candidates(diagnosis):
    """Generate candidate improvements based on diagnosis."""
    candidates = []

    # Always: baseline (current approach)
    candidates.append(RoutingCandidate(
        name='baseline',
        description='Current NCER router with commit gate',
        category='algorithm',
        expected_improvement='Current state',
        risk='low',
    ))

    # If congested pads: optimistic grid
    if 'congested_pads' in diagnosis.failure_modes:
        candidates.append(RoutingCandidate(
            name='optimistic_grid',
            description='Relaxed grid (margin=0) + exact judge (chaosRouter)',
            category='grid',
            params={'pad_margin': 0.0, 'track_margin': 0.0, 'use_exact_judge': True},
            expected_improvement='Escape congested pads (src_free=4)',
            risk='medium',
        ))

    # If power nets: pour power planes
    if 'power_nets' in diagnosis.failure_modes:
        candidates.append(RoutingCandidate(
            name='power_plane',
            description='Pour GND/VCC planes after routing (Koppar/Zap! approach)',
            category='power_plane',
            params={'pour_gnd': True, 'pour_vcc': True, 'pour_after_routing': True},
            expected_improvement='Connect GND 84 pads through plane',
            risk='low',
        ))

    # If long pairs: exact wavefront
    if 'long_pairs' in diagnosis.failure_modes:
        candidates.append(RoutingCandidate(
            name='exact_wavefront',
            description='Replace FIM with Exact Wavefront (RA-L 2024)',
            category='algorithm',
            params={'use_wavefront': True, 'max_iterations': 999999},
            expected_improvement='Eliminate FIM 500-iteration limit',
            risk='high',
        ))

    # Always: combined approach
    candidates.append(RoutingCandidate(
        name='combined',
        description='Power plane + optimistic grid + exact wavefront',
        category='algorithm',
        params={'pour_gnd': True, 'pad_margin': 0.0, 'use_exact_judge': True, 'use_wavefront': True},
        expected_improvement='Best of all approaches',
        risk='high',
    ))

    return candidates


# ── Select: evaluate candidates ─────────────────────────
def evaluate_candidate(board_path, candidate, net_pads, timeout_s=300):
    """Evaluate a routing candidate on the target board."""
    import pcbnew
    sys.path.insert(0, 'E:/DATA/vscode/ZCODE/autorouter-bench/tools')
    from fluxpcb_ncer import NCERRouter

    t0 = time.time()
    b = pcbnew.LoadBoard(str(board_path))
    b.BuildConnectivity()
    lids = rg.signal_layer_ids(b)

    # Apply candidate parameters
    router = NCERRouter(b, lids)

    # Apply grid relaxation if specified
    if candidate.params.get('pad_margin') is not None:
        rg.PAD_MARGIN = candidate.params['pad_margin']
        rg.TRACK_MARGIN = candidate.params.get('track_margin', 0.0)

    # Run routing
    try:
        result = router.run(net_pads, timeout_s)
        elapsed = time.time() - t0

        # Measure completion
        b2 = router.b
        b2.BuildConnectivity()
        unconnected = b2.GetConnectivity().GetUnconnectedCount(True)
        total_pairs = sum(max(0, len(p)-1) for p in net_pads.values() if len(p) >= 2)
        routed = max(0, total_pairs - unconnected)
        completion_pct = 100.0 * routed / max(total_pairs, 1)

        # Apply power plane if specified (AFTER routing)
        if candidate.params.get('pour_gnd'):
            router._create_power_plane('GND')
            b2.BuildConnectivity()
            unconnected = b2.GetConnectivity().GetUnconnectedCount(True)
            routed = max(0, total_pairs - unconnected)
            completion_pct = 100.0 * routed / max(total_pairs, 1)

        # Measure DRC (run kicad-cli)
        drc_errors = 0
        try:
            import subprocess
            tmp_board = str(board_path).replace('.kicad_pcb', '_eval.kicad_pcb')
            b2.Save(tmp_board)
            drc_json = tmp_board.replace('.kicad_pcb', '.drc.json')
            subprocess.run(
                ['kicad-cli', 'pcb', 'drc', '--format', 'json',
                 '--severity-error', '-o', drc_json, tmp_board],
                capture_output=True, timeout=60,
            )
            if Path(drc_json).exists():
                drc_data = json.loads(Path(drc_json).read_text())
                drc_errors = len(drc_data.get('violations', []))
        except Exception:
            pass

        return RoutingResult(
            candidate_name=candidate.name,
            completion_pct=completion_pct,
            drc_errors=drc_errors,
            unconnected=unconnected,
            vias=result.get('total_pairs', 0),
            time_s=elapsed,
            success=True,
        )
    except Exception as e:
        return RoutingResult(
            candidate_name=candidate.name,
            completion_pct=0.0,
            drc_errors=999,
            unconnected=999,
            vias=0,
            time_s=time.time() - t0,
            success=False,
        )


# ── Main: HarnessPro loop ──────────────────────────────
def harness_loop(board_path, output_path, budget_s=600):
    """Main HarnessPro loop: diagnose→evolve→select."""
    t0 = time.time()
    import pcbnew

    print(f'=== HarnessPro Adaptive Routing ===', flush=True)
    print(f'Board: {board_path}', flush=True)
    print(f'Budget: {budget_s}s', flush=True)

    # Load board and collect net pads
    b = pcbnew.LoadBoard(str(board_path))
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
            net_pads[nn].append((p.x/MM, p.y/MM, pad_li))

    # Phase 1: Diagnose board (static analysis, no routing needed)
    print(f'\n--- Phase 1: Diagnose ---', flush=True)
    from strategy_selector import diagnose_board, select_strategy, apply_strategy
    diagnosis = diagnose_board(board_path)
    print(f'  Failure modes: {diagnosis["failure_modes"]}', flush=True)
    print(f'  Power nets: {diagnosis["power_net_names"]}', flush=True)

    # Phase 2: Select strategy based on diagnosis
    print(f'\n--- Phase 2: Select Strategy ---', flush=True)
    strategy = select_strategy(diagnosis)
    print(f'  Strategy: {strategy.name} — {strategy.description}', flush=True)
    print(f'  Pad margin: {strategy.pad_margin:.3f}, Clearance: {strategy.clearance_margin:.3f}', flush=True)
    print(f'  Pour power: {strategy.pour_power_nets} {strategy.power_net_names}', flush=True)

    # Phase 3: Route with selected strategy
    print(f'\n--- Phase 3: Route ---', flush=True)
    candidates = [RoutingCandidate(
        name=strategy.name,
        description=strategy.description,
        category='algorithm',
        params={
            'pad_margin': strategy.pad_margin,
            'track_margin': strategy.track_margin,
            'pour_gnd': strategy.pour_power_nets,
            'clearance_margin': strategy.clearance_margin,
        },
    )]
    remaining_time = budget_s - (time.time() - t0)
    eval_timeout = max(120, remaining_time)
    results = []

    for candidate in candidates:
        if remaining_time < 60:
            print(f'  Skipping {candidate.name} (time budget exhausted)', flush=True)
            break

        print(f'  Evaluating {candidate.name} (timeout={eval_timeout}s)...', flush=True)
        result = evaluate_candidate(board_path, candidate, net_pads, eval_timeout)
        results.append(result)
        remaining_time = budget_s - (time.time() - t0)

        print(f'    Completion: {result.completion_pct:.1f}%, '
              f'Unconnected: {result.unconnected}, '
              f'Time: {result.time_s:.0f}s', flush=True)

    # Select best: completion - DRC penalty (not just completion)
    if results:
        # Score = completion% - DRC*0.1 (DRC errors reduce score)
        for r in results:
            r.score = r.completion_pct - r.drc_errors * 0.1
        best = max(results, key=lambda r: r.score)
        print(f'\n--- Best: {best.candidate_name} (score={best.score:.1f}) ---', flush=True)
        print(f'  Completion: {best.completion_pct:.1f}%', flush=True)
        print(f'  DRC errors: {best.drc_errors}', flush=True)
        print(f'  Unconnected: {best.unconnected}', flush=True)

        return {
            'best_candidate': best.candidate_name,
            'completion': best.completion_pct,
            'drc_errors': best.drc_errors,
            'unconnected': best.unconnected,
            'diagnosis': diagnosis.get('failure_modes', {}) if isinstance(diagnosis, dict) else getattr(diagnosis, 'failure_modes', {}),
            'candidates_evaluated': len(results),
        }

    return {'error': 'No candidates evaluated'}


if __name__ == '__main__':
    board_path = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 else board_path.replace('.kicad_pcb', '_routed.kicad_pcb')
    budget_s = int(sys.argv[3]) if len(sys.argv) > 3 else 600

    result = harness_loop(board_path, output_path, budget_s)
    print(json.dumps(result))
