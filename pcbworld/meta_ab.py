#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Meta/harness A/B: is the harness load-bearing?

Runs the same boards twice:
  off: no harness (current fixed pipeline)
  on:  a strict harness that allows only widen_narrow_tracks and a single
       action, and denies anything else.

Load-bearing is not "the score improved".  It is:
  * the action/plan distribution changed (ops differ, or actions denied),
  * and/or the outcome/cost changed,
while the scope did not narrow (SII).
"""
import argparse
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, '..', 'flux-neurotransmitter'))

from fluxpcb.harness import HarnessHook, HarnessPolicy  # noqa: E402
from run_bench import route_and_repair  # noqa: E402

SAMPLE = [
    '0023_hardware-designs_si7021-board',
    '0029_espeverywhere__autosave-espeverywhere_breakout',
    '0030_kitspace_sop8breakout',
    '0037_hardware-designs_spsgrf-board',
    '0054_induction-hob_temperature-sensor',
    '0057_tepmachcha_tepmachcha',
]


def _run(d, hook, timeout):
    src = os.path.join(HERE, 'boards', d)
    tmp = tempfile.mkdtemp(prefix='ab_')
    paired = os.path.join(tmp, 'board.kicad_pcb')
    out = os.path.join(tmp, 'out.kicad_pcb')
    shutil.copy(os.path.join(src, 'circuit.reference.kicad_pcb'), paired)
    shutil.copy(os.path.join(src, 'circuit.kicad_pro'),
                os.path.join(tmp, 'board.kicad_pro'))
    info = route_and_repair(paired, out, os.path.join(src, 'circuit.kicad_pro'),
                            timeout, ledger_dir=None, board_family='d3a',
                            hook=hook)
    cert = info.get('cert') or {}
    ops = []
    for entry in (cert.get('txn') or {}).get('log') or []:
        act = entry.get('action') or {}
        ops.extend(act.get('operators_run') or [])
    after = cert.get('claims_after') or {}
    passed = (cert.get('board_worst') == 'pass'
              or (after.get('total') == 0 and after.get('rule_congruent')))
    return {
        'routed': bool(info.get('routed')),
        'pass': bool(passed),
        'readiness': cert.get('readiness'),
        'seconds': info.get('seconds'),
        'commits': (cert.get('txn') or {}).get('committed'),
        'rolled_back': (cert.get('txn') or {}).get('rolled_back'),
        'denied': (cert.get('txn') or {}).get('denied'),
        'ops': ops,
        'declared_rule_ids': sorted((cert.get('scope') or {}).get(
            'declared_rule_ids') or []),
        'harness': cert.get('harness'),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--timeout', type=int, default=240)
    ap.add_argument('--out', default=os.path.join(HERE, 'meta_ab.json'))
    args = ap.parse_args()
    policy_path = os.path.join(
        ROOT, '..', 'flux-neurotransmitter', 'fluxpcb', 'harness', 'policies',
        'strict_widen_only.json')
    strict = HarnessPolicy.load(policy_path)

    os.environ.pop('FLUXPCB_HARNESS', None)
    rows = []
    for d in SAMPLE:
        off = _run(d, None, args.timeout)
        on_hook = HarnessHook(strict)
        on = _run(d, on_hook, args.timeout)
        divergence = (sorted(off['ops']) != sorted(on['ops'])
                      or bool(on['denied']))
        narrowed = not (set(off['declared_rule_ids']) <=
                        set(on['declared_rule_ids']))
        rows.append({'dir': d, 'off': off, 'on': on,
                     'plan_divergence': divergence, 'scope_narrowed': narrowed})
        print(f"{d[:34]:34s} off={off['pass']}/{off['seconds']}s "
              f"on={on['pass']}/{on['seconds']}s div={divergence} "
              f"denied={on['denied']}", flush=True)

    off_pass = sum(1 for r in rows if r['off']['pass'])
    on_pass = sum(1 for r in rows if r['on']['pass'])
    div = sum(1 for r in rows if r['plan_divergence'])
    summary = {
        'boards': len(rows),
        'off_pass': off_pass, 'on_pass': on_pass,
        'mean_seconds_off': round(sum(r['off']['seconds'] or 0 for r in rows)
                                  / len(rows), 1),
        'mean_seconds_on': round(sum(r['on']['seconds'] or 0 for r in rows)
                                 / len(rows), 1),
        'commits_off': sum(r['off']['commits'] or 0 for r in rows),
        'commits_on': sum(r['on']['commits'] or 0 for r in rows),
        'denied_on': sum(r['on']['denied'] or 0 for r in rows),
        'plan_divergence_boards': div,
        'scope_narrowed_boards': sum(1 for r in rows if r['scope_narrowed']),
        'policy': strict.as_dict(),
        # A harness that changes actions is load-bearing; whether it is
        # beneficial is a separate outcome question.
        'verdict': ('load_bearing' if div > 0 else 'decorative'),
        'beneficial': on_pass >= off_pass,
    }
    json.dump({'summary': summary, 'rows': rows}, open(args.out, 'w'), indent=1)
    print(json.dumps(summary, indent=1))


if __name__ == '__main__':
    main()
