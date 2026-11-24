#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build an evidence ledger over every routed board's certificate.

Each board contributes three claims:

    rule_congruence  the router's rule table equalled the referee's (L1 proof)
    drc_clean        KiCad found no error-severity violation
    unconnected_zero KiCad found no missing connection

Readiness is worst-first: a board with 0 DRC errors but one open is not
"99% done", it is failing.  Gates are qualified once at startup; an
unqualified gate cannot yield a pass.
"""
import collections
import glob
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, '..', 'flux-neurotransmitter'))

from fluxpcb.verifiers.ledger import (Claim, Ledger, Outcome,  # noqa: E402
                                      default_registry, file_sha256, worst)

LEDGER = os.path.join(HERE, 'ledger', 'ledger.jsonl')
os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
if os.path.exists(LEDGER):
    os.remove(LEDGER)
ledger = Ledger(LEDGER)
reg = default_registry()
quals = {n: q.ok for n, q in reg.quals.items()}
if not all(quals.values()):
    print('UNQUALIFIED GATES:', [n for n, ok in quals.items() if not ok])
    sys.exit(1)

rows = []
for cert_path in sorted(glob.glob(os.path.join(HERE, 'runs', '*', 'cert.json'))):
    d = os.path.basename(os.path.dirname(cert_path))
    c = json.load(open(cert_path, encoding='utf-8'))
    board = os.path.join(os.path.dirname(cert_path), f'{d}.kicad_pcb')
    board_hash = file_sha256(board) if os.path.exists(board) else ''
    live = c.get('ledger') or {}
    if live:
        rows.append({'dir': d, 'board_hash': board_hash[:16],
                     'rule_ok': (live.get('by_claim') or {}).get('rule_congruence') == 'pass',
                     'n_errors': (c.get('claims_after') or {}).get('n_errors'),
                     'n_unconnected': (c.get('claims_after') or {}).get('n_unconnected'),
                     'worst': live.get('worst'),
                     'board_worst': live.get('board_worst'),
                     'scope_worst': live.get('scope_worst'),
                     'residue': (c.get('residue') or {}).get('by_class'),
                     'txn': (c.get('txn') or {}).get('committed', 0)})
        continue
    patch = c.get('rule_patch') or {}
    pre = c.get('rule_audit_pre') or {}
    post_ok = bool(patch.get('verified')) or bool(pre.get('ok'))
    # L1 claim: the read-back proof decides; pre-audit divergence is expected
    v_rule = reg.evaluate('rule_congruence', {'verified': post_ok})
    ledger.record(Claim(f'{d}/rule_congruence',
                        'router rule table equals referee',
                        mechanism='established', owner='T1-A',
                        gate='rule_congruence'), v_rule)

    after = c.get('claims_after') or {}
    n_err = after.get('n_errors')
    n_un = after.get('n_unconnected')
    v_drc = reg.evaluate('drc_clean', {'n_error_violations': n_err})
    ledger.record(Claim(f'{d}/drc_clean', 'zero error-severity DRC violations',
                        mechanism='established', owner='T1-A', gate='drc_clean'),
                  v_drc)
    v_un = reg.evaluate('unconnected_zero', {'n_unconnected': n_un})
    ledger.record(Claim(f'{d}/unconnected_zero', 'KiCad connectivity complete',
                        mechanism='established', owner='T1-B',
                        gate='unconnected_zero'), v_un)

    board_worst = worst([v_rule.outcome, v_drc.outcome, v_un.outcome])
    rows.append({'dir': d, 'board_hash': board_hash[:16],
                 'rule_ok': post_ok, 'n_errors': n_err,
                 'n_unconnected': n_un, 'worst': board_worst.value,
                 'residue': (c.get('residue') or {}).get('by_class'),
                 'txn': (c.get('txn') or {}).get('committed', 0)})

# per-board status must be computed from the whole run, not from the running
# worst (the ledger is append-only and claims repeat across boards)
status = collections.Counter(r['worst'] for r in rows)
by_rule = collections.Counter(r['rule_ok'] for r in rows)
residue = collections.Counter()
for r in rows:
    for k, v in (r['residue'] or {}).items():
        residue[k] += v

summary = {
    'boards': len(rows),
    'gates_qualified': quals,
    'rule_congruent_boards': sum(1 for r in rows if r['rule_ok']),
    'worst_first': dict(status),
    'board_worst': dict(collections.Counter(r.get('board_worst') for r in rows)),
    'scope_worst': dict(collections.Counter(r.get('scope_worst') for r in rows)),
    'residue_by_class': dict(sorted(residue.items(), key=lambda kv: -kv[1])),
    'txn_commits': sum(r['txn'] or 0 for r in rows),
    'board_hashes': {r['dir']: r['board_hash'] for r in rows},
}
json.dump(summary, open(os.path.join(HERE, 'ledger', 'summary.json'), 'w'),
          indent=1)
ok, why = ledger.verify_chain()
print(json.dumps(summary, indent=1))
print('chain:', ok, why)
