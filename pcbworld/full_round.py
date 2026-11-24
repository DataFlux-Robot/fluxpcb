#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Full-118 round runner: one board per subprocess, resumable.

Why: a single process that calls pcbnew.LoadBoard + DRC for 118 boards is
routinely terminated natively (0x40010004) and takes the whole batch down.
The parent here never imports pcbnew; each board is a child process, and
completion is judged by a FRESH cert (matching policy digest and newer
than the routed board), not by a rows file.
"""
import argparse
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
RUN_BENCH = os.path.join(HERE, 'run_bench.py')


def policy_digest(path):
    body = json.load(open(path, encoding='utf-8'))
    if body.get('policy_digest'):
        return body['policy_digest']
    import hashlib
    body.pop('policy_digest', None)
    return hashlib.sha256(json.dumps(body, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def fresh(runs, d, digest):
    cert_path = os.path.join(runs, d, 'cert.json')
    board = os.path.join(runs, d, f'{d}.kicad_pcb')
    if not (os.path.exists(cert_path) and os.path.exists(board)):
        return False
    try:
        cert = json.load(open(cert_path, encoding='utf-8'))
    except ValueError:
        return False
    if cert.get('policy_digest') != digest:
        return False
    return os.path.getmtime(cert_path) >= os.path.getmtime(board) - 1e-6


def run_one(board, args, runs, digest):
    d = board['dir']
    mf = os.path.join(args.round_dir, 'one', d + '.json')
    os.makedirs(os.path.dirname(mf), exist_ok=True)
    json.dump([board], open(mf, 'w'), indent=1)
    rows = os.path.join(args.round_dir, 'rows', d + '.json')
    os.makedirs(os.path.dirname(rows), exist_ok=True)
    cmd = [sys.executable, RUN_BENCH, '--manifest', mf,
           '--splits', board['set'], '--runs-dir', runs, '--rows', rows,
           '--policy', args.policy_json,
           '--timeout', str(args.timeout), '--max-new', '1']
    t0 = time.time()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           encoding='utf-8', errors='replace',
                           timeout=args.subprocess_timeout)
        rc, err = p.returncode, (p.stderr or '')[-300:]
    except subprocess.TimeoutExpired:
        rc, err = -9, f'subprocess timeout {args.subprocess_timeout}s'
    ok = fresh(runs, d, digest)
    return {'dir': d, 'set': board['set'], 'ok': ok, 'rc': rc,
            'seconds': round(time.time() - t0, 1), 'error': None if ok else err}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--round-dir', required=True)
    ap.add_argument('--policy-json', required=True)
    ap.add_argument('--manifest', default=os.path.join(HERE, 'boards_full.json'))
    ap.add_argument('--splits', default='d3a,d3b,d3c')
    ap.add_argument('--timeout', type=int, default=240,
                    help='router timeout passed to run_bench')
    ap.add_argument('--subprocess-timeout', type=int, default=1800)
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--retry', type=int, default=1)
    args = ap.parse_args()

    os.makedirs(args.round_dir, exist_ok=True)
    runs = os.path.join(args.round_dir, 'runs')
    os.makedirs(runs, exist_ok=True)
    digest = policy_digest(args.policy_json)
    want = set(args.splits.split(','))
    boards = [b for b in json.load(open(args.manifest, encoding='utf-8'))
              if b['set'] in want]
    if args.limit:
        boards = boards[:args.limit]
    status_path = os.path.join(args.round_dir, 'status.jsonl')
    done = set()
    if os.path.exists(status_path):
        for line in open(status_path, encoding='utf-8'):
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get('ok'):
                done.add(row['dir'])
    todo = [b for b in boards if b['dir'] not in done
            and not fresh(runs, b['dir'], digest)]
    print(f'round {os.path.basename(args.round_dir)}: '
          f'{len(boards)} boards, {len(done)} done, {len(todo)} to run, '
          f'policy {digest[:12]}', flush=True)
    with open(status_path, 'a', encoding='utf-8') as st:
        for i, b in enumerate(todo, 1):
            row = run_one(b, args, runs, digest)
            for attempt in range(args.retry):
                if row['ok']:
                    break
                row = run_one(b, args, runs, digest)
            st.write(json.dumps(row, ensure_ascii=False) + '\n')
            st.flush()
            print(f"[{i}/{len(todo)}] {b['dir'][:40]}: "
                  f"{'ok' if row['ok'] else 'FAIL'} {row['seconds']}s "
                  f"{'' if row['ok'] else (row['error'] or '')[-80:]}",
                  flush=True)
    ok = sum(1 for b in boards if fresh(runs, b['dir'], digest))
    print(f'round complete: {ok}/{len(boards)} fresh certs', flush=True)


if __name__ == '__main__':
    main()
