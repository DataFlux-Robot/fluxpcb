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
    """Run kicad-cli DRC and return parsed JSON.

    Fails CLOSED: a missing binary, a timeout, a missing report, or an
    unparseable report returns ``{'ok': False, 'error': ...}`` and removes
    any stale report first.  Callers must refuse to produce claims from a
    non-ok result; a missing report is never "zero violations".
    """
    cli = os.environ.get('KICAD_CLI', 'kicad-cli')
    try:
        if os.path.exists(out_json):
            os.remove(out_json)
    except OSError:
        pass
    try:
        p = subprocess.run(
            [cli, 'pcb', 'drc', '--format', 'json', '--output', out_json,
             '--severity-all', board_path],
            capture_output=True, text=True, timeout=300)
    except Exception as e:
        return {'ok': False, 'error': f'kicad-cli failed to run: {e}',
                'violations': [], 'unconnected_items': []}
    if not os.path.exists(out_json):
        return {'ok': False,
                'error': f'no DRC report (exit {p.returncode}): '
                         f'{(p.stderr or "")[:200]}',
                'violations': [], 'unconnected_items': []}
    try:
        with open(out_json, encoding='utf-8') as f:
            d = json.load(f)
    except Exception as e:
        return {'ok': False, 'error': f'unparseable DRC report: {e}',
                'violations': [], 'unconnected_items': []}
    if not isinstance(d, dict) or 'violations' not in d:
        return {'ok': False, 'error': 'DRC report missing violations key',
                'violations': [], 'unconnected_items': []}
    d['ok'] = True
    d['cli'] = cli
    return d


def save_preserving_project(board, board_path: str):
    """Save a board without letting pcbnew rewrite its paired project.

    pcbnew writes the project's design settings from the in-memory board,
    which after a bare ``LoadBoard`` are KiCad defaults.  The saved
    ``.kicad_pro`` then no longer describes the board's rules, and every
    later DRC -- run in a separate process -- judges against the wrong
    table.  Snapshot and restore the bytes around the save.
    """
    base = os.path.splitext(board_path)[0]
    sidecars = {}
    for path in (base + '.kicad_pro', base + '.kicad_dru'):
        if os.path.exists(path):
            with open(path, 'rb') as f:
                sidecars[path] = f.read()
    board.Save(board_path)
    for path, blob in sidecars.items():
        with open(path, 'wb') as f:
            f.write(blob)


def refill_zones(board_path: str):
    """Refill zones (SKILL.md: 'any autorouter output must be zone-refilled')."""
    import pcbnew
    b = pcbnew.LoadBoard(board_path)
    filler = pcbnew.ZONE_FILLER(b)
    filler.Fill(b.Zones())
    save_preserving_project(b, board_path)


def violating_nets(drc_json: str, types=('track_width', 'clearance')) -> set:
    """Net names implicated by DRC violations of the given types."""
    import re
    with open(drc_json, encoding='utf-8') as f:
        d = json.load(f)
    nets = set()
    for v in d.get('violations') or []:
        if v.get('severity') != 'error' or v.get('excluded'):
            continue
        if v.get('type') not in types:
            continue
        for it in v.get('items') or []:
            m = re.search(r'\[([^\]]+)\]', it.get('description') or '')
            if m:
                nets.add(m.group(1))
    return nets


def reroute_nets(board_path: str, nets, timeout_s: int = 240,
                 commit: bool = False) -> dict:
    """Rip up ``nets`` and let Freerouting re-route them.

    Everything else stays as protected copper in the DSN, so the router sees
    the real obstacle field.  The caller must treat the result as a
    transaction: evaluate and roll back if the hard-claim vector worsens.
    """
    import pcbnew
    import shutil
    from fluxpcb.router.freerouting import FreeroutingBackend

    nets = set(nets)
    if not nets:
        return {'ok': False, 'reason': 'no nets'}
    base = os.path.splitext(board_path)[0]
    pro = base + '.kicad_pro'
    b = pcbnew.LoadBoard(board_path)
    if b is None:
        return {'ok': False, 'reason': 'load failed'}
    removed = 0
    for t in list(b.GetTracks()):
        try:
            if t.GetNetname() in nets:
                b.Remove(t)
                removed += 1
        except Exception:
            continue
    if removed == 0:
        return {'ok': False, 'reason': 'no tracks removed'}
    work = base + '.reroute.kicad_pcb'
    save_preserving_project(b, work)
    if os.path.exists(pro):
        shutil.copy(pro, os.path.splitext(work)[0] + '.kicad_pro')
    fr = FreeroutingBackend().route(work, work + '.out.kicad_pcb',
                                    timeout_s=timeout_s, use_board_rules=True)
    if not fr.routed:
        return {'ok': False, 'reason': fr.error, 'removed': removed}
    # EXPERIMENTAL: the routed result is returned, not applied, unless the
    # caller opts in.  A caller that commits must wrap this in
    # transactional_repair and evaluate the post-state.
    if commit:
        shutil.copy(work + '.out.kicad_pcb', board_path)
        if os.path.exists(pro) and os.path.abspath(pro) != os.path.abspath(base + '.kicad_pro'):
            shutil.copy(pro, base + '.kicad_pro')
    return {'ok': True, 'nets': sorted(nets), 'removed': removed,
            'seconds': fr.seconds, 'congruent': fr.rule_congruence,
            'output': work + '.out.kicad_pcb', 'committed': commit}


def parse_violations(drc: dict) -> list:
    """DRC JSON -> Violation objects, with explicit measurement presence.

    Structured fields win; Chinese and English descriptions are fallbacks.
    A violation without a measured actual/required keeps has_measurement
    False so operators that mint floors skip it instead of using 0.0.
    """
    import re

    from fluxpcb.verifiers.operators import Violation

    raw_violations = drc.get('violations', [])
    violations = []
    for v in raw_violations:
        vtype = v.get('type', '')
        items = v.get('items', [])
        desc = v.get('description', '')

        required = actual = 0.0
        req_ok = act_ok = False
        for key in ('required_mm', 'required'):
            if v.get(key) is not None:
                try:
                    required, req_ok = float(v[key]), True
                    break
                except (TypeError, ValueError):
                    pass
        for key in ('actual_mm', 'actual'):
            if v.get(key) is not None:
                try:
                    actual, act_ok = float(v[key]), True
                    break
                except (TypeError, ValueError):
                    pass
        if not req_ok:
            m = re.search(r'(?:clearance|间距)\s*([0-9.]+)\s*mm', desc, re.I)
            if m:
                required, req_ok = float(m.group(1)), True
        if not act_ok:
            m = re.search(r'(?:actual|实际)\s*([0-9.]+)\s*mm', desc, re.I)
            if m:
                actual, act_ok = float(m.group(1)), True

        net = ''
        net2 = ''
        uuid = ''
        pos = (0.0, 0.0)
        items_out = []
        for item in items:
            idesc = item.get('description', '')
            m = re.search(r'\[([^\]]+)\]', idesc)
            if m:
                if not net:
                    net = m.group(1)
                elif not net2:
                    net2 = m.group(1)
            items_out.append({'description': idesc,
                              'pos': item.get('pos'),
                              'uuid': item.get('uuid', '')})
            if vtype == 'track_dangling' and not uuid:
                uuid = item.get('uuid', '')
                p = item.get('pos', {})
                pos = (p.get('x', 0.0), p.get('y', 0.0))
            # A clearance/width pair names its copper; keep a track uuid so
            # a geometric operator can move the segment, not just the net.
            low = idesc.lower()
            if (vtype in ('clearance', 'track_width', 'shorting_items')
                    and not uuid and item.get('uuid')
                    and ('track' in low or '走线' in low or '线段' in low)):
                uuid = item.get('uuid', '')

        violations.append(Violation(
            type=vtype, net=net, net2=net2,
            actual_mm=actual, required_mm=required,
            location=pos, track_uuid=uuid, items=items_out,
            has_measurement=bool(req_ok and act_ok),
        ))

    # Opens are first-class violations: the repair loop cannot close what it
    # never sees.  Keep the item list so a connectivity operator can use the
    # two anchors directly.
    for u in drc.get('unconnected_items') or []:
        if u.get('excluded'):
            continue
        desc = u.get('description') or ''
        m = re.search(r'\[([^\]]+)\]', desc)
        net = m.group(1) if m else ''
        if not net:
            for it in (u.get('items') or []):
                m2 = re.search(r'\[([^\]]+)\]', it.get('description') or '')
                if m2:
                    net = m2.group(1)
                    break
        pos = u.get('pos') or {}
        violations.append(Violation(
            type='unconnected', net=net,
            location=(pos.get('x', 0.0), pos.get('y', 0.0)),
            items=u.get('items') or [u],
            has_measurement=False,
        ))
    return violations


def reroute_residues(board_path: str, drc_json: str,
                     timeout_s: int = 240) -> dict:
    """Rip up the nets implicated by width/clearance violations and
    re-route them.  The caller must run this inside a transaction: the
    output is copied over the board, then evaluated and rolled back if the
    hard-claim vector does not improve."""
    import hashlib
    nets = violating_nets(drc_json, types=('track_width', 'clearance'))
    if not nets:
        return {'operators_run': [], 'reason': 'no width/clearance nets'}
    info = reroute_nets(board_path, nets, timeout_s=timeout_s, commit=False)
    if not info.get('ok'):
        return {'operators_run': [], 'error': info.get('reason'),
                'nets': sorted(nets)}
    shutil.copy(info['output'], board_path)
    h = hashlib.sha256(open(board_path, 'rb').read()).hexdigest()
    return {'operators_run': ['reroute_violating_nets'],
            'nets': sorted(nets), 'removed': info.get('removed'),
            'seconds': info.get('seconds'),
            'congruent': info.get('congruent'),
            'board_sha': h[:16]}


def reroute_residues_isolated(board_path: str, drc_json: str,
                              timeout_s: int = 240) -> dict:
    """Run reroute_residues in a fresh interpreter.

    pcbnew's SWIG wrappers become invalid after a net-level rip-up in the
    same process; isolating the reroute keeps the caller's board state
    usable for the post-action evaluation.
    """
    cmd = [sys.executable, os.path.abspath(__file__), '--reroute',
           board_path, drc_json, str(timeout_s)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    for line in (r.stdout or '').splitlines():
        if line.startswith('__REROUTE__'):
            return json.loads(line[len('__REROUTE__'):])
    return {'operators_run': [], 'error': (r.stderr or '')[-200:]}


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

    with open(drc_json, encoding='utf-8') as f:
        drc = json.load(f)

    if drc.get('ok') is False:
        # A failed measurement must not be repaired as if it were clean.
        return {'error': drc.get('error', 'DRC report not ok'),
                'operators_run': [], 'repaired': 0, 'failed': 0, 'ledger': []}

    violations = parse_violations(drc)

    ops = RepairOperators()
    import pcbnew
    board = pcbnew.LoadBoard(board_path)

    results = ops.dispatch_repair(board, violations)

    # Save repaired board (project bytes preserved: DRC must keep judging
    # against the board's own rules)
    save_preserving_project(board, board_path)

    return {
        'operators_run': [r.operator for r in results],
        'repaired': sum(r.repaired_count for r in results),
        'failed': sum(r.failed_count for r in results),
        'ledger': ops.ledger,
    }


def main():
    """CLI: refill -> transactional repair -> report.

    Uses the same transaction gate as the benchmark; a repair batch that
    does not strictly improve the hard-claim vector is rolled back.
    """
    if len(sys.argv) < 2:
        print('Usage: python repair_drc.py <board.kicad_pcb> [--output <fixed>]')
        sys.exit(1)

    board_path = sys.argv[1]
    output_path = board_path
    if '--output' in sys.argv:
        output_path = sys.argv[sys.argv.index('--output') + 1]

    from fluxpcb.verifiers.txn import claims_from_drc, transactional_repair

    print('=== DRC Repair Pipeline (transactional) ===')
    print(f'Board: {board_path}')
    print()

    def evaluate(path):
        refill_zones(path)
        dj = path + '.drc_live.json'
        d = run_drc(path, dj)
        if d.get('ok') is False:
            raise RuntimeError(d.get('error', 'DRC failed'))
        holder['drc'] = dj
        return claims_from_drc(d, True)

    holder = {'drc': None}

    def action(path):
        if not holder['drc']:
            return {}
        return repair_with_operators(path, holder['drc'])

    before = evaluate(board_path)
    print(f"Baseline: {before.n_errors} violations, "
          f"{before.n_unconnected} unconnected")
    rep = transactional_repair(board_path, action, evaluate,
                               max_rounds=3, initial=before)
    after = evaluate(board_path)
    print(f'Transactions: {rep.committed} committed, '
          f'{rep.rolled_back} rolled back')
    print(f"Final: {after.n_errors} violations, {after.n_unconnected} unconnected")

    if output_path != board_path:
        import shutil
        shutil.copy2(board_path, output_path)


if __name__ == '__main__':
    if len(sys.argv) >= 4 and sys.argv[1] == '--reroute':
        _b, _d, _t = sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else 240
        print('__REROUTE__' + json.dumps(
            reroute_residues(_b, _d, int(_t))), flush=True)
    else:
        main()
