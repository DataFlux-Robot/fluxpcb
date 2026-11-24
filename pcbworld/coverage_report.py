#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P2 coverage report: mutation adequacy + pair validation + AHR.

Inputs (all produced by the P2 runners):
  mutation_results.jsonl
  redteam_summary.json
  runs/*/cert.json (rule_register)
Output: coverage.json + a printed table.
"""
import collections
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, '..', 'flux-neurotransmitter'))

from fluxpcb.contract.pairs import RulePairRegistry  # noqa: E402


def main():
    # ---- mutation summary --------------------------------------------
    mut = collections.defaultdict(lambda: {'generated': 0, 'crossed': 0,
                                           'killed': 0, 'killed_crossed': 0})
    mp = os.path.join(HERE, 'mutation_results.jsonl')
    if os.path.exists(mp):
        for line in open(mp, encoding='utf-8'):
            r = json.loads(line)
            if r.get('status') != 'ok':
                continue
            m = mut[r['op']]
            m['generated'] += 1
            if r.get('crossed'):
                m['crossed'] += 1
            if r.get('killed'):
                m['killed'] += 1
                if r.get('crossed'):
                    m['killed_crossed'] += 1
    for op, m in mut.items():
        m['MS'] = (round(m['killed_crossed'] / m['crossed'], 3)
                   if m['crossed'] else None)
        m['blind_spots'] = max(0, m['crossed'] - m['killed_crossed'])
        m['equivalents'] = m['generated'] - m['crossed']
    # shift_pad now carries a hole-to-hole crossing metric, so survivors
    # are classified as equivalents (not crossed) rather than unknown.
    unclassified = []

    # ---- second oracle + targeted probes ------------------------------
    redteam = {}
    rp = os.path.join(HERE, 'redteam_summary.json')
    if os.path.exists(rp):
        redteam = json.load(open(rp, encoding='utf-8'))
    pp = os.path.join(HERE, 'pair_probe_summary.json')
    if os.path.exists(pp):
        redteam['pair_probe'] = json.load(open(pp, encoding='utf-8'))

    # ---- pairs --------------------------------------------------------
    pairs = RulePairRegistry.default().report(mut, redteam)

    # ---- register / R_om ---------------------------------------------
    reg = {}
    for cp in glob.glob(os.path.join(HERE, 'runs', '*', 'cert.json')):
        r = json.load(open(cp, encoding='utf-8')).get('rule_register') or {}
        if r:
            reg = r
            break
    declared = reg.get('declared', 0)
    R_om_dsn = reg.get('R_om')
    R_om_check = (round(1 - pairs['validated'] / declared, 3)
                  if declared else None)

    out = {
        'mutation': dict(mut),
        'unclassified': unclassified,
        'pairs': pairs,
        'redteam': redteam,
        'register': {'declared': declared, 'R_om_dsn': R_om_dsn,
                     'R_om_check': R_om_check,
                     'validated_pairs': pairs['validated']},
        'metrics': {
            'MS_by_class': {k: v['MS'] for k, v in mut.items()},
            'blind_spots': {k: v['blind_spots'] for k, v in mut.items()},
            'AHR': redteam.get('AHR'),
            'wrong_pro_detected': redteam.get('wrong_pro_detected'),
            'stale_report_detected': redteam.get('stale_report_detected'),
        },
    }
    json.dump(out, open(os.path.join(HERE, 'coverage.json'), 'w'), indent=1)

    print(f"{'op':12s} {'gen':>4s} {'crossed':>7s} {'killed':>6s} "
          f"{'MS':>5s} {'blind':>5s} {'equiv':>5s}")
    for op, m in sorted(mut.items()):
        ms = '-' if m['MS'] is None else f"{m['MS']:.2f}"
        print(f"{op:12s} {m['generated']:4d} {m['crossed']:7d} "
              f"{m['killed']:6d} {ms:>5s} {m['blind_spots']:5d} "
              f"{m['equivalents']:5d}")
    print()
    print(f"pairs validated {pairs['validated']}/10  mapped {pairs['mapped']}  "
          f"blind {pairs['blind_spot']}")
    print(f"R_om_dsn {R_om_dsn}   R_om_check {R_om_check} "
          f"(validated {pairs['validated']}/{declared})")
    if redteam:
        print(f"AHR {redteam.get('AHR')}  detected {redteam.get('detected_types')}")
        print(f"wrong-pro detected {redteam.get('wrong_pro_detected')}/"
              f"{redteam.get('boards')}  stale-report "
              f"{redteam.get('stale_report_detected')}/{redteam.get('boards')}")
    if unclassified:
        print('unclassified survivors:', unclassified)


if __name__ == '__main__':
    main()
