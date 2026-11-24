#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Triage every board into PASS / FAIL / GAP / ASSUMED / INFEASIBLE.

The point is not to change the score.  It is to stop reporting a scalar
"pass" when the pass is conditional on a policy assumption or when the
claim set does not cover the rule that matters.

Sources: runs/<dir>/cert.json (claims_after, rule_patch, rule_audit_pre),
plus a global rule register (KiCad constraint types vs what the DSN model
represents).
"""
import collections
import glob
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
BOARDS = os.path.join(HERE, 'boards_full.json')
RUNS = os.path.join(HERE, 'runs')

# KiCad 10's DRC constraint types (drc_constraint_types.h) and the subset the
# DSN model can carry: width, clearance, default rule, via diameter/drill.
DECLARED_RULES = 40
REPRESENTED_RULES = 5


def main():
    boards = json.load(open(BOARDS))
    rows = []
    for b in boards:
        d = b['dir']
        cert = os.path.join(RUNS, d, 'cert.json')
        if not os.path.exists(cert):
            rows.append({'dir': d, 'set': b['set'], 'state': 'GAP',
                         'reason': 'no cert'})
            continue
        c = json.load(open(cert, encoding='utf-8'))
        live = c.get('ledger') or {}
        if live.get('board_worst'):
            state = ('PASS' if live['board_worst'] == 'pass'
                     and not c.get('assumptions') else
                     'ASSUMED' if live['board_worst'] == 'pass' else 'FAIL')
            if state == 'FAIL' and (c.get('claims_after') or {}).get('n_errors'):
                types = (c.get('claims_after') or {}).get('type_counts') or {}
                if set(types) == {'track_width'} and (c.get('txn') or {}).get('rolled_back'):
                    state = 'INFEASIBLE'
            rows.append({'dir': d, 'set': b['set'], 'state': state,
                         'assumed': c.get('assumptions') or [],
                         'gaps': ([] if (live.get('by_claim') or {}).get('rule_scope') == 'pass'
                                  else ['rule_scope']),
                         'n_errors': (c.get('claims_after') or {}).get('n_errors'),
                         'n_unconnected': (c.get('claims_after') or {}).get('n_unconnected'),
                         'types': (c.get('claims_after') or {}).get('type_counts') or {},
                         'readiness': live.get('worst'),
                         'txn': {'c': (c.get('txn') or {}).get('committed', 0),
                                 'r': (c.get('txn') or {}).get('rolled_back', 0)}})
            continue
        after = c.get('claims_after') or {}
        patch = c.get('rule_patch') or {}
        pre = c.get('rule_audit_pre') or {}
        counts = dict(after.get('type_counts') or {})
        n_err = after.get('n_errors') or 0
        n_un = after.get('n_unconnected') or 0

        # assumptions: the pcb-level default rule was injected by us, not
        # exported by KiCad; every pass is conditional on that policy.
        assumed = []
        if patch.get('default_rule_injected'):
            assumed.append('default_rule_policy')
        for note in patch.get('notes') or []:
            if 'unclaimed' in note:
                assumed.append('netclass_unclaimed_nets')

        # gaps: rules outside the model.  Pre-audit out_of_model is empty by
        # construction; the global register is the honest denominator.
        gaps = []
        if pre.get('out_of_model'):
            gaps.extend(pre['out_of_model'])

        if n_err == 0 and n_un == 0:
            state = 'ASSUMED' if assumed else 'PASS'
        elif counts.get('unconnected') or (n_un and not n_err):
            state = 'FAIL'
        elif set(counts) == {'track_width'} and c.get('txn', {}).get('rolled_back'):
            # widening was tried and rejected: the hard-claim vector could
            # not improve under the board's own rules.
            state = 'INFEASIBLE'
        else:
            state = 'FAIL'
        rows.append({'dir': d, 'set': b['set'], 'state': state,
                     'assumed': assumed, 'gaps': gaps,
                     'n_errors': n_err, 'n_unconnected': n_un,
                     'types': counts,
                     'txn': {'c': (c.get('txn') or {}).get('committed', 0),
                             'r': (c.get('txn') or {}).get('rolled_back', 0)}})

    by_state = collections.Counter(r['state'] for r in rows)
    by_set = collections.defaultdict(collections.Counter)
    for r in rows:
        by_set[r['set']][r['state']] += 1
    regs = []
    for b in boards:
        cp = os.path.join(RUNS, b['dir'], 'cert.json')
        if os.path.exists(cp):
            rr = (json.load(open(cp, encoding='utf-8')).get('rule_register') or {})
            if rr:
                regs.append(rr)
    reg = regs[-1] if regs else {'declared': DECLARED_RULES,
                                 'represented': REPRESENTED_RULES,
                                 'R_om': round(1 - REPRESENTED_RULES / DECLARED_RULES, 3)}
    fails = [r for r in rows if r['state'] in ('FAIL', 'INFEASIBLE')]
    assumptions = collections.Counter(a for r in rows for a in r['assumed'])

    out = {
        'boards': len(rows),
        'by_state': dict(by_state),
        'by_set': {k: dict(v) for k, v in by_set.items()},
        'assumptions': dict(assumptions),
        'rule_register': reg,
        'failures': [{k: r[k] for k in ('dir', 'set', 'state', 'n_errors',
                                        'n_unconnected', 'types', 'txn')}
                     for r in fails],
    }
    with open(os.path.join(HERE, 'triage.json'), 'w', encoding='utf-8') as f:
        json.dump(out, f, indent=1)
    print(json.dumps({k: v for k, v in out.items() if k != 'failures'},
                     indent=1))
    print('\nfailures:')
    for r in fails:
        print(f"  {r['dir'][:44]:44s} {r['state']:10s} {r['types']} "
              f"txn={r['txn']}")


if __name__ == '__main__':
    main()
