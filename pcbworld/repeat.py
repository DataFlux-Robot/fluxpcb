#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Repeatability: run one (board, arm) cell k more times and count flips.

A single run cannot tell an effect from run-to-run variation.  The A/A
duplicate in gen0 compares ONE pair of runs, which sets a floor of "at
least this noisy" and nothing more.  This runs the arms a decision hinged
on k times each on the same board and reports how often the outcome
changes — the noise floor that was actually measured, per board and arm.

    repeat.py --run <run> --board <dir> --arms "gate+ripup,gate+ripup~x=1" --k 4
"""
import argparse
import dataclasses
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, '..', 'flux-neurotransmitter'))
sys.path.insert(0, HERE)

import arms_run  # noqa: E402
from fluxpcb.meta import distinguish as D  # noqa: E402
from fluxpcb.meta.arm import Arm  # noqa: E402


def arm_of(run, board, name):
    for c in D.load_cells(run):
        if c['board'] == board and c['arm'] == name:
            cert = json.load(open(os.path.join(run, c['cert']), encoding='utf-8'))
            return Arm.from_dict(cert['arm'])
    raise SystemExit(f'no cell for {board} / {name}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--run', required=True)
    ap.add_argument('--board', required=True)
    ap.add_argument('--arms', required=True, help='comma-separated arm names')
    ap.add_argument('--k', type=int, default=4)
    ap.add_argument('--workers', type=int, default=6)
    ap.add_argument('--budget', type=int, default=300)
    ap.add_argument('--timeout', type=int, default=240)
    args = ap.parse_args()
    run = args.run
    all_boards = json.load(open(os.path.join(HERE, 'boards_full.json'),
                                encoding='utf-8'))
    entry = [b for b in all_boards if b['dir'] == args.board][0]
    reps = []
    origin = {}
    for name in args.arms.split(','):
        arm = arm_of(run, args.board, name)
        for i in range(1, args.k + 1):
            copy = dataclasses.replace(arm, name=f'{name}~rep{i}')
            origin[copy.name] = name
            reps.append(copy)
    arms_run.evaluate(run, [entry], reps, args.budget, args.timeout,
                      args.workers, f'repeat-{args.board[:4]}')
    cells = {(c['board'], c['arm']): c for c in D.load_cells(run)}
    print(f'\n{args.board}: outcome over {args.k} repeat runs (+ the original cell)')
    flips_total = runs_total = 0
    for name in args.arms.split(','):
        outs = []
        orig = cells.get((args.board, name))
        if orig:
            outs.append(orig['outcome'])
        for i in range(1, args.k + 1):
            c = cells.get((args.board, f'{name}~rep{i}'))
            outs.append(c['outcome'] if c else 'missing')
        clean = sum(1 for o in outs if o == 'clean')
        flips = sum(1 for o in outs if o != outs[0])
        flips_total += flips
        runs_total += len(outs)
        print(f'  {name[:60]:60s} clean {clean}/{len(outs)}  {outs}')
    rate = round(flips_total / max(1, runs_total), 3)
    print(f'  disagreement with each series\' first run: {flips_total}/{runs_total} = {rate}')
    arms_run.atomic_json(os.path.join(run, f'repeat_{args.board[:4]}.json'), {
        'board': args.board, 'k': args.k, 'arms': args.arms.split(','),
        'outcomes': {n: [cells.get((args.board, n), {}).get('outcome')] + [
            cells.get((args.board, f'{n}~rep{i}'), {}).get('outcome')
            for i in range(1, args.k + 1)] for n in args.arms.split(',')},
        'disagreement_rate': rate})


if __name__ == '__main__':
    main()
