#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""One round of the full-118 policy ladder.

materialize policy (given, or proposed from the previous round's certs)
  -> full_round.py (one board per subprocess)
  -> score_runs.py over the round's runs directory
  -> round.json (policy digest, delta, score summary, gate vs previous).

The code is identical across rounds; only the PolicyArtifact changes.
"""
import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, '..', 'flux-neurotransmitter'))

from fluxpcb.meta.feedback import FailurePacket  # noqa: E402
from fluxpcb.meta.gate import validate_candidate  # noqa: E402
from fluxpcb.meta.policy import PolicyArtifact, propose_deltas  # noqa: E402

LEVERS = {
    'maze': {'kind': 'enable_op', 'value': 'maze_escape',
             'why': 'fallback lever: sealed anchors need grid escape'},
    'ripup': {'kind': 'enable_op', 'value': 'short_rip_reroute',
              'why': 'fallback lever: hard shorts need rip-up'},
    'budget': {'kind': 'budget', 'value': {'maze_seconds': 60},
               'why': 'fallback lever: widen maze budget'},
    'no-planning': {'kind': 'planning', 'value': False,
                    'why': 'fallback lever: planning off ablation'},
}


def load_certs(runs_dir):
    out = []
    for d in sorted(os.listdir(runs_dir)):
        p = os.path.join(runs_dir, d, 'cert.json')
        if os.path.exists(p):
            try:
                out.append((d, json.load(open(p, encoding='utf-8'))))
            except ValueError:
                continue
    return out


def aggregate(summary, split):
    s = (summary or {}).get(split) or {}
    return {'boards': s.get('n', 0),
            'routing_clean': s.get('routing_clean',
                                   s.get('clean', 0)),
            'total_errors': 0,
            'seconds': 0.0}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--round-dir', required=True)
    ap.add_argument('--policy-json', default='')
    ap.add_argument('--base-policy', default='')
    ap.add_argument('--propose-from', default='',
                    help='previous round dir: build packets from its certs')
    ap.add_argument('--fallback', default='',
                    choices=['', 'maze', 'ripup', 'budget', 'no-planning'])
    ap.add_argument('--manifest', default=os.path.join(HERE, 'boards_full.json'))
    ap.add_argument('--splits', default='d3a,d3b,d3c')
    ap.add_argument('--timeout', type=int, default=240)
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--skip-run', action='store_true')
    ap.add_argument('--skip-score', action='store_true')
    args = ap.parse_args()

    os.makedirs(args.round_dir, exist_ok=True)
    policy_path = os.path.join(args.round_dir, 'policy.json')
    delta = None
    if args.policy_json:
        policy = PolicyArtifact.from_json(args.policy_json)
    else:
        base = PolicyArtifact.from_json(args.base_policy) if args.base_policy \
            else PolicyArtifact()
        packets = []
        if args.propose_from:
            for d, cert in load_certs(os.path.join(args.propose_from, 'runs')):
                packets.append(FailurePacket.from_cert(d, cert))
        deltas = propose_deltas(packets, base, max_deltas=3)
        if deltas:
            delta = deltas[0]
        elif args.fallback:
            delta = LEVERS[args.fallback]
        if delta is None:
            delta = {'kind': 'text', 'value': 'no new evidence',
                     'why': 'round holds the incumbent policy'}
        policy = base.with_delta(delta, rationale=delta.get('why', ''))
    policy.to_json(policy_path)
    digest = policy.digest()

    if not args.skip_run:
        cmd = [sys.executable, os.path.join(HERE, 'full_round.py'),
               '--round-dir', args.round_dir, '--policy-json', policy_path,
               '--manifest', args.manifest, '--splits', args.splits,
               '--timeout', str(args.timeout)]
        if args.limit:
            cmd += ['--limit', str(args.limit)]
        subprocess.run(cmd, check=False)

    summary = {}
    if not args.skip_score:
        score_out = os.path.join(args.round_dir, 'score.json')
        cmd = [sys.executable, os.path.join(HERE, 'score_runs.py'),
               '--manifest', args.manifest, '--splits', args.splits,
               '--runs', os.path.join(args.round_dir, 'runs'),
               '--out', score_out, '--resume']
        subprocess.run(cmd, check=False)
        sum_path = score_out.replace('.json', '_summary.json')
        if os.path.exists(sum_path):
            summary = json.load(open(sum_path, encoding='utf-8'))

    gate = None
    if args.propose_from:
        prev = os.path.join(args.propose_from, 'score_summary.json')
        if os.path.exists(prev):
            ps = json.load(open(prev, encoding='utf-8'))
            inc = aggregate(ps, 'd3b')
            inc['total_errors'] = ps.get('d3b', {}).get('n', 0) - \
                ps.get('d3b', {}).get('routing_clean', 0)
            cand = aggregate(summary, 'd3b')
            cand['total_errors'] = summary.get('d3b', {}).get('n', 0) - \
                summary.get('d3b', {}).get('routing_clean', 0)
            gate = validate_candidate(inc, cand, incumbent_heldout=inc,
                                      candidate_heldout=cand)
    report = {'round': os.path.basename(args.round_dir),
              'policy_digest': digest, 'delta': delta,
              'policy': policy.payload(), 'score_summary': summary,
              'gate': gate}
    json.dump(report, open(os.path.join(args.round_dir, 'round.json'), 'w'),
              indent=1, ensure_ascii=False)
    print(json.dumps({'round': report['round'], 'digest': digest[:12],
                      'delta': delta, 'gate': gate,
                      'summary': {k: summary.get(k) for k in
                                  ('d3a', 'd3b', 'd3c', 'all')}},
                     indent=1))


if __name__ == '__main__':
    main()
