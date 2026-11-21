#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""repair_drc — DRC repair using skill-package operators.

Post-processing script: takes a routed board, runs DRC, applies the right
repair operator per violation morphology, then re-verifies.

This bridges the skill-package operator library into the benchmark pipeline
(which runs fluxpcb_ncer.py directly, not the full 12-component pipeline).

Usage:
    python repair_drc.py <board.kicad_pcb> [--output <fixed.kicad_pcb>]
"""
from __future__ import annotations

import json
import os
import subprocess
import sys


def run_drc(board_path: str, out_json: str) -> dict:
    """Run kicad-cli DRC and return parsed JSON."""
    subprocess.run([
        'kicad-cli', 'pcb', 'drc', '--format', 'json',
        '--output', out_json,
        '--severity-all',
        board_path,
    ], capture_output=True, text=True, timeout=300)

    if not os.path.exists(out_json):
        return {'violations': [], 'unconnected_items': []}

    with open(out_json) as f:
        return json.load(f)


def refill_zones(board_path: str):
    """Refill zones (SKILL.md: 'any autorouter output must be zone-refilled')."""
    import pcbnew
    b = pcbnew.LoadBoard(board_path)
    filler = pcbnew.ZONE_FILLER(b)
    filler.Fill(b.Zones())
    b.Save(board_path)


def repair_with_operators(board_path: str, drc_json: str) -> dict:
    """Apply skill-package repair operators to DRC violations.

    Returns a summary of what was repaired.
    """
    # Ensure fluxpcb is importable regardless of how the script is invoked
    _here = os.path.dirname(os.path.abspath(__file__))
    _root = os.path.dirname(_here)
    if _root not in sys.path:
        sys.path.insert(0, _root)

    from fluxpcb.verifiers.operators import RepairOperators, Violation

    with open(drc_json) as f:
        drc = json.load(f)

    raw_violations = drc.get('violations', [])

    # Convert DRC JSON to Violation objects
    violations = []
    import re
    for v in raw_violations:
        vtype = v.get('type', '')
        items = v.get('items', [])
        desc = v.get('description', '')

        # Parse actual/required from description:
        # "间距违规 ( 间距 0.2000 mm; 实际 0.1769 mm)"
        # or "clearance violation (clearance 0.2 mm; actual 0.1769 mm)"
        required = 0.0
        actual = 0.0
        m_req = re.search(r'间距\s*([0-9.]+)\s*mm', desc)
        if m_req:
            required = float(m_req.group(1))
        m_act = re.search(r'实际\s*([0-9.]+)\s*mm', desc)
        if m_act:
            actual = float(m_act.group(1))

        # Extract net names + uuid + position from items
        net = ''
        net2 = ''
        uuid = ''
        pos = (0.0, 0.0)
        for item in items:
            idesc = item.get('description', '')
            m = re.search(r'\[([^\]]+)\]', idesc)
            if m:
                if not net:
                    net = m.group(1)
                elif not net2:
                    net2 = m.group(1)
            if vtype == 'track_dangling' and not uuid:
                uuid = item.get('uuid', '')
                p = item.get('pos', {})
                pos = (p.get('x', 0.0), p.get('y', 0.0))

        violations.append(Violation(
            type=vtype, net=net, net2=net2,
            actual_mm=actual, required_mm=required,
            location=pos, track_uuid=uuid,
        ))

    ops = RepairOperators()
    import pcbnew
    board = pcbnew.LoadBoard(board_path)

    results = ops.dispatch_repair(board, violations)

    # Save repaired board
    board.Save(board_path)

    return {
        'operators_run': [r.operator for r in results],
        'repaired': sum(r.repaired_count for r in results),
        'failed': sum(r.failed_count for r in results),
        'ledger': ops.ledger,
    }


def main():
    if len(sys.argv) < 2:
        print('Usage: python repair_drc.py <board.kicad_pcb> [--output <fixed>]')
        sys.exit(1)

    board_path = sys.argv[1]
    output_path = board_path
    if '--output' in sys.argv:
        output_path = sys.argv[sys.argv.index('--output') + 1]

    print(f'=== DRC Repair Pipeline ===')
    print(f'Board: {board_path}')
    print()

    # Step 1: Refill zones BEFORE DRC (SKILL.md rule)
    print('Step 1: Refill zones...')
    refill_zones(board_path)
    print('  Done')

    # Step 2: Run DRC to get baseline
    print('Step 2: Baseline DRC...')
    drc_before = run_drc(board_path, board_path + '.drc_before.json')
    n_before = len(drc_before.get('violations', []))
    u_before = len(drc_before.get('unconnected_items', []))
    print(f'  Violations: {n_before}, Unconnected: {u_before}')

    # Step 3: Apply repair operators
    print('Step 3: Apply repair operators...')
    try:
        summary = repair_with_operators(board_path, board_path + '.drc_before.json')
        print(f'  Operators: {summary["operators_run"]}')
        print(f'  Repaired: {summary["repaired"]}, Failed: {summary["failed"]}')
    except Exception as e:
        print(f'  Repair error: {e}')
        summary = {'error': str(e)}

    # Step 4: Refill zones again + final DRC
    print('Step 4: Final DRC (after refill)...')
    refill_zones(board_path)
    drc_after = run_drc(board_path, board_path + '.drc_after.json')
    n_after = len(drc_after.get('violations', []))
    u_after = len(drc_after.get('unconnected_items', []))
    print(f'  Violations: {n_after}, Unconnected: {u_after}')

    # Report
    print()
    print('=== Result ===')
    print(f'Violations: {n_before} -> {n_after} ({n_after - n_before:+d})')
    print(f'Unconnected: {u_before} -> {u_after} ({u_after - u_before:+d})')

    # Type breakdown
    def count_types(drc):
        types = {}
        for v in drc.get('violations', []):
            t = v.get('type', 'unknown')
            types[t] = types.get(t, 0) + 1
        return types

    tb = count_types(drc_before)
    ta = count_types(drc_after)
    print()
    print('By type:')
    all_types = set(tb.keys()) | set(ta.keys())
    for t in sorted(all_types, key=lambda x: -max(tb.get(x, 0), ta.get(x, 0))):
        print(f'  {t}: {tb.get(t, 0)} -> {ta.get(t, 0)}')

    if output_path != board_path:
        import shutil
        shutil.copy2(board_path, output_path)


if __name__ == '__main__':
    main()
