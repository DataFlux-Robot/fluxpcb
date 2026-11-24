#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""C1/C2 iteration campaign.

Round: cached per-board evaluations -> FailurePackets -> typed policy
deltas -> evaluate at most N candidates on a training coreset -> pick the
Pareto winner -> C2 gate on a held-out family -> activate or stop.

Attempts are budgeted: candidates per round, boards per split, and a
content-addressed cache so a (board, policy) pair is never evaluated
twice.  --replay rebuilds packets from existing runs/*/cert.json without
routing, for a cheap logic check.
"""
import argparse
import glob
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, '..', 'flux-neurotransmitter'))

from fluxpcb.meta.feedback import FailurePacket, axis_of  # noqa: E402
from fluxpcb.meta.gate import validate_candidate  # noqa: E402
from fluxpcb.meta.policy import PolicyArtifact, propose_deltas  # noqa: E402

MANIFEST = os.path.join(HERE, 'boards_full.json')


def load_boards(manifest):
    return json.load(open(manifest, encoding='utf-8'))


def pick(boards, split, n):
    sel = [b for b in boards if b['set'] == split]
    sel.sort(key=lambda b: b['dir'])
    return sel[:n] if n else sel


def summarize(board, cert, seconds, error=None, routed=True):
    claims = cert.get('claims_after') or cert.get('claims_before') or {}
    counts = claims.get('type_counts') or {}
    opens = claims.get('n_unconnected', 0)
    routing = opens + sum(n for t, n in counts.items()
                          if axis_of(t) == 'routing' and t != 'unconnected')
    return {'dir': board['dir'], 'set': board['set'],
            'routing_errors': routing,
            'total_errors': (claims.get('total') or 0) + opens,
            'unconnected': opens, 'seconds': seconds or 0.0,
            'routed': bool(routed), 'error': error,
            'capacity': (cert.get('capacity') or {}).get('verdict'),
            'plan_digest': cert.get('plan_digest'),
            'cert': cert}


def eval_board(board, policy, timeout, cache_dir):
    key = board['dir'] + '__' + policy.digest()[:16]
    os.makedirs(cache_dir, exist_ok=True)
    cpath = os.path.join(cache_dir, key + '.json')
    if os.path.exists(cpath):
        try:
            row = json.load(open(cpath, encoding='utf-8'))
            row['cache'] = True
            return row
        except ValueError:
            pass
    src = os.path.join(HERE, 'boards', board['dir'])
    tmp = tempfile.mkdtemp(prefix='it_')
    paired = os.path.join(tmp, 'board.kicad_pcb')
    shutil.copy(os.path.join(src, 'circuit.reference.kicad_pcb'), paired)
    shutil.copy(os.path.join(src, 'circuit.kicad_pro'),
                os.path.join(tmp, 'board.kicad_pro'))
    from run_bench import route_and_repair
    res = route_and_repair(paired, os.path.join(tmp, 'out.kicad_pcb'),
                           os.path.join(src, 'circuit.kicad_pro'),
                           timeout_s=timeout, enable_planning=True,
                           policy=policy)
    row = summarize(board, res.get('cert') or {}, res.get('seconds'),
                    res.get('error'), res.get('routed'))
    row['cache'] = False
    with open(cpath, 'w', encoding='utf-8') as fh:
        json.dump(row, fh)
    return row


def eval_set(boards, policy, timeout, cache_dir):
    return [eval_board(b, policy, timeout, cache_dir) for b in boards]


def aggregate(rows):
    n = max(1, len(rows))
    return {'boards': len(rows),
            'routing_clean': sum(1 for r in rows
                                 if r['routed'] and r['routing_errors'] == 0),
            'total_errors': sum(r['total_errors'] for r in rows),
            'seconds': round(sum(r['seconds'] for r in rows), 1),
            'n': n}


def better(cand, inc):
    """Pareto-companion ordering: clean count, then fewer errors, then time."""
    return ((cand['routing_clean'], -cand['total_errors'], -cand['seconds'])
            > (inc['routing_clean'], -inc['total_errors'], -inc['seconds']))


def replay_set(boards, runs):
    rows = []
    for b in boards:
        cpath = os.path.join(runs, b['dir'], 'cert.json')
        if not os.path.exists(cpath):
            continue
        cert = json.load(open(cpath, encoding='utf-8'))
        rows.append(summarize(b, cert, (cert.get('route') or {}).get(
            'seconds', 0.0), (cert.get('route') or {}).get('error'),
            (cert.get('route') or {}).get('routed', True)))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--manifest', default=MANIFEST)
    ap.add_argument('--runs', default=os.path.join(HERE, 'runs'))
    ap.add_argument('--out', default=os.path.join(HERE, 'iterate'))
    ap.add_argument('--rounds', type=int, default=5)
    ap.add_argument('--train', type=int, default=16)
    ap.add_argument('--heldout', type=int, default=10)
    ap.add_argument('--audit', type=int, default=9)
    ap.add_argument('--train-split', default='d3a')
    ap.add_argument('--heldout-split', default='d3b')
    ap.add_argument('--audit-split', default='d3c')
    ap.add_argument('--timeout', type=int, default=240)
    ap.add_argument('--max-candidates', type=int, default=3)
    ap.add_argument('--replay', action='store_true')
    ap.add_argument('--policy', default='')
    ap.add_argument('--train-manifest', default='')
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    cache_dir = os.path.join(args.out, 'cache')
    boards = load_boards(args.manifest)
    if args.train_manifest:
        # Failure-stratified coreset: sorted-by-name selection can land on
        # clean boards and stop at no_proposal (the smoke-test trap).
        train = json.load(open(args.train_manifest, encoding='utf-8'))
    else:
        train = pick(boards, args.train_split, args.train)
    held = pick(boards, args.heldout_split, args.heldout)
    audit = pick(boards, args.audit_split, args.audit)
    incumbent = (PolicyArtifact.from_json(args.policy) if args.policy
                 else PolicyArtifact())
    report = {'train': len(train), 'heldout': len(held),
              'audit': len(audit), 'replay': args.replay,
              'rounds': [], 'active': None,
              'start_policy': incumbent.digest()}

    def evaluate(group, policy):
        if args.replay:
            return replay_set(group, args.runs)
        return eval_set(group, policy, args.timeout, cache_dir)

    train_inc = aggregate(evaluate(train, incumbent))
    held_inc = aggregate(evaluate(held, incumbent))
    audit_inc = aggregate(evaluate(audit, incumbent))
    report['incumbent'] = {'train': train_inc, 'heldout': held_inc,
                           'audit': audit_inc}

    for rnd in range(1, args.rounds + 1):
        inc_rows = evaluate(train, incumbent)
        packets = [FailurePacket.from_cert(r['dir'], r['cert'], r['set'])
                   for r in inc_rows if r['routing_errors'] > 0]
        deltas = propose_deltas(packets, incumbent,
                                max_deltas=args.max_candidates)
        entry = {'round': rnd, 'packets': len(packets),
                 'deltas': deltas, 'decision': 'no_proposal'}
        if not deltas:
            report['rounds'].append(entry)
            break

        candidates = []
        for d in deltas[:args.max_candidates]:
            try:
                cand = incumbent.with_delta(d, rationale=d.get('why', ''))
            except (ValueError, KeyError):
                continue
            rows = evaluate(train, cand)
            a = aggregate(rows)
            candidates.append({'policy': cand, 'agg': a, 'delta': d,
                               'rows': rows})
        if not candidates:
            entry['decision'] = 'no_compilable_delta'
            report['rounds'].append(entry)
            break

        best = max(candidates, key=lambda c: (
            c['agg']['routing_clean'], -c['agg']['total_errors'],
            -c['agg']['seconds']))
        entry['best'] = {'delta': best['delta'], 'agg': best['agg'],
                         'policy': best['policy'].digest()}
        if not better(best['agg'], train_inc):
            entry['decision'] = 'no_improvement'
            report['rounds'].append(entry)
            break

        held_cand = aggregate(evaluate(held, best['policy']))
        gate = validate_candidate(
            train_inc, best['agg'],
            incumbent_heldout=held_inc, candidate_heldout=held_cand,
            scope_narrowed=not set(incumbent.operator_portfolio) <=
            set(best['policy'].operator_portfolio),
            regressed=[r['dir'] for r in best['rows']
                       if r['routing_errors'] > 0
                       and r['dir'] in {x['dir'] for x in inc_rows
                                        if x['routing_errors'] == 0}])
        entry['gate'] = gate
        entry['heldout'] = held_cand
        if gate['ok']:
            incumbent = best['policy']
            train_inc, held_inc = best['agg'], held_cand
            audit_inc = aggregate(evaluate(audit, incumbent))
            entry['decision'] = 'activated'
            incumbent.to_json(os.path.join(args.out, 'active_policy.json'))
            report['active'] = incumbent.digest()
        else:
            entry['decision'] = 'rejected'
        report['rounds'].append(entry)
        if entry['decision'] != 'activated':
            break

    report['final'] = {'train': train_inc, 'heldout': held_inc,
                       'audit': audit_inc,
                       'active_policy': (incumbent.digest()
                                         if report['active'] else None)}
    json.dump(report, open(os.path.join(args.out, 'campaign.json'), 'w'),
              indent=1, ensure_ascii=False)
    print(json.dumps({k: report[k] for k in ('train', 'heldout', 'audit',
                                             'final')}, indent=1))
    print(json.dumps([{'round': r['round'], 'decision': r['decision'],
                       'gate': r.get('gate')} for r in report['rounds']],
                     indent=1))


if __name__ == '__main__':
    main()
