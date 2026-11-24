"""Distinguishability analysis: what did running several arms buy?

Interventional distinguishability, read two ways from one factorial run:

* per board -- how many distinct outcomes the arms produced.  A board with
  one outcome says nothing about any axis; a board with two or more tells
  the selector which arm to pick; a board no arm cleans needs a new
  capability rather than a choice.
* per axis -- flipping one axis while holding the other fixed.  Wins and
  losses are counted board by board, and only read as an effect when the
  losses stay within what the A/A duplicate flips on its own.

Pure arithmetic over cells; nothing is re-derived.  The cells come from
work/pcbworld/arms_run.py.
"""
from __future__ import annotations

import itertools
import json
import os
from collections import defaultdict

AXES = ('plan', 'loop')


def load_cells(run):
    """Every cell JSON under <run>/cells."""
    cells = []
    d = os.path.join(run, 'cells')
    if not os.path.isdir(d):
        return cells
    for fn in sorted(os.listdir(d)):
        if not fn.endswith('.json'):
            continue
        try:
            cells.append(json.load(open(os.path.join(d, fn), encoding='utf-8')))
        except ValueError:
            continue
    return cells


def success(c):
    return bool(c) and c.get('outcome') == 'clean'


def comparable(c):
    return bool(c) and c.get('outcome') in ('clean', 'fail', 'budget')


def is_factorial(c):
    return '~' not in (c.get('arm') or '')


def by_board(cells):
    out = defaultdict(dict)
    for c in cells:
        out[c['board']][c['arm']] = c
    return out


def arm_axes(cells):
    out = {}
    for c in cells:
        out[c['arm']] = c.get('arm_axes') or {}
    return out


def noise(cells, bb, base='gate+batch', dup='gate+batch~aa'):
    """A/A: the same arm twice.  Its flip rate is the floor an effect must clear."""
    a = [c for c in cells if c.get('arm') == base]
    b = [c for c in cells if c.get('arm') == dup]
    if not a or not b:
        return {'available': False}
    flips, n = [], 0
    for board, per in bb.items():
        c1, c2 = per.get(base), per.get(dup)
        if comparable(c1) and comparable(c2):
            n += 1
            if success(c1) != success(c2):
                flips.append(board)
    return {'available': True, 'boards': n, 'flips': flips,
            'flip_rate': round(len(flips) / n, 3) if n else None}


def main_effects(cells, bb, axes):
    out = {}
    for axis in AXES:
        other = [a for a in AXES if a != axis][0]
        vals = sorted({v[axis] for v in axes.values() if is_factorial_name(v)})
        pairs = {}
        for v1, v2 in itertools.combinations(vals, 2):
            wins, losses, touched = [], [], 0
            for board, per in bb.items():
                for ov in sorted({v[other] for v in axes.values()
                                  if is_factorial_name(v)}):
                    n1 = [n for n, v in axes.items()
                          if is_factorial_name(n) and v[axis] == v1 and v[other] == ov]
                    n2 = [n for n, v in axes.items()
                          if is_factorial_name(n) and v[axis] == v2 and v[other] == ov]
                    if not n1 or not n2:
                        continue
                    c1, c2 = per.get(n1[0]), per.get(n2[0])
                    if not (comparable(c1) and comparable(c2)):
                        continue
                    s1, s2 = success(c1), success(c2)
                    if s1 != s2:
                        touched += 1
                        (wins if s2 else losses).append(f'{board} [{ov}]')
            pairs[f'{v1} -> {v2}'] = {'wins': len(wins), 'losses': len(losses),
                                      'touched': touched,
                                      'win_boards': wins[:8],
                                      'loss_boards': losses[:8]}
        out[axis] = pairs
    return out


def is_factorial_name(name):
    return '~' not in name


def ablation(cells, bb, base='gate+batch', variant='gate+batch~lenientpad'):
    wins, losses, n = [], [], 0
    for board, per in bb.items():
        c1, c2 = per.get(base), per.get(variant)
        if comparable(c1) and comparable(c2):
            n += 1
            if success(c2) and not success(c1):
                wins.append(board)
            elif success(c1) and not success(c2):
                losses.append(board)
    return {'boards': n, 'variant_wins': wins, 'variant_losses': losses}


def per_board(cells, bb, axes, order_clean):
    rows = []
    for board, per in bb.items():
        mine = {n: c for n, c in per.items() if is_factorial_name(n)}
        outs = {c.get('outcome') for c in mine.values()}
        clean_arms = sorted(n for n, c in mine.items() if success(c))
        if not mine:
            continue
        if clean_arms and len(clean_arms) == len(mine):
            channel = 'no_information'        # every arm passes
        elif clean_arms:
            channel = 'selectable'            # the choice matters here
        elif outs <= {'budget'}:
            channel = 'budget_blind'          # no arm finished in time
        else:
            channel = 'needs_new_capability'  # no arm passes at this budget
        errs = {c.get('arm'): ((c.get('score') or {}).get('errors'),
                               (c.get('score') or {}).get('unconnected'))
                for c in mine.values() if c.get('score')}
        rows.append({'board': board, 'set': next(iter(mine.values()))['set'],
                     'distinct_outcomes': len(outs), 'outcomes': sorted(outs),
                     'clean_arms': clean_arms, 'channel': channel,
                     'errors_per_arm': errs,
                     'best_error': min((e[0] or 0) + (e[1] or 0)
                                       for e in errs.values()) if errs else None})
    rows.sort(key=lambda r: (order_clean.get(r['channel'], 9), r['board']))
    return rows


def analyse(run):
    cells = load_cells(run)
    bb = by_board(cells)
    axes = arm_axes(cells)
    fact = [c for c in cells if is_factorial(c)]
    arms = sorted({c['arm'] for c in fact})
    boards = sorted(bb)

    per_arm = {}
    for a in arms:
        mine = [c for c in fact if c['arm'] == a]
        per_arm[a] = {
            'clean': sum(1 for c in mine if success(c)),
            'boards': len(mine),
            'mean_wall_s': round(sum(c.get('wall_seconds') or 0 for c in mine)
                                 / max(1, len(mine)), 1),
            'instrument_bad': sum(1 for c in mine if not c.get('instrument_ok')),
        }
    # Pareto over (clean up, wall down)
    front = []
    for a, s in per_arm.items():
        dominated = any(
            o['clean'] >= s['clean'] and o['mean_wall_s'] <= s['mean_wall_s']
            and (o['clean'] > s['clean'] or o['mean_wall_s'] < s['mean_wall_s'])
            for n, o in per_arm.items() if n != a)
        if not dominated:
            front.append(a)

    order = {'needs_new_capability': 0, 'selectable': 1, 'budget_blind': 2,
             'no_information': 3}
    rows = per_board(fact, bb, axes, order)
    channels = defaultdict(list)
    for r in rows:
        channels[r['channel']].append(r['board'])

    oracle = sum(1 for r in rows if r['clean_arms'])
    best = max((s['clean'] for s in per_arm.values()), default=0)
    aa = noise(cells, bb)
    floor = len(aa.get('flips', [])) if aa.get('available') else None
    effects = main_effects(fact, bb, {a: axes[a] for a in arms})
    # an effect is only read where the losses stay within the A/A noise
    for axis, pairs in effects.items():
        for p in pairs.values():
            p['distinguishable'] = (floor is not None
                                    and p['wins'] > floor and p['losses'] <= floor)
    ab = ablation(cells, bb)

    return {
        'run': run, 'cells': len(cells), 'factorial_cells': len(fact),
        'boards': len(boards), 'arms': arms,
        'instrument_bad_cells': sum(1 for c in cells
                                    if not c.get('instrument_ok')),
        'per_arm': per_arm, 'pareto_front': sorted(front),
        'best_single_arm': best, 'oracle_any_arm': oracle,
        'selector_headroom': oracle - best,
        'noise_aa': aa,
        'main_effects': effects,
        'ablation_lenient_pad': ab,
        'channels': dict(channels),
        'per_board': rows,
    }


def render(res):
    print(f"cells {res['cells']} (factorial {res['factorial_cells']}), "
          f"boards {res['boards']}, instrument-bad {res['instrument_bad_cells']}")
    aa = res['noise_aa']
    if aa.get('available'):
        print(f"A/A noise: {len(aa['flips'])}/{aa['boards']} boards flip "
              f"({aa['flip_rate']}) {aa['flips']}")
    else:
        print('A/A noise: NOT MEASURED')
    print(f"best single arm {res['best_single_arm']} | oracle {res['oracle_any_arm']} "
          f"| selector headroom {res['selector_headroom']}")
    print('arm                     clean  mean_wall  pareto')
    for a, s in sorted(res['per_arm'].items(), key=lambda kv: -kv[1]['clean']):
        print(f"{a:22s}  {s['clean']:>3}/{s['boards']:<3}  {s['mean_wall_s']:>8}s  "
              f"{'yes' if a in res['pareto_front'] else ''}")
    for axis, pairs in res['main_effects'].items():
        for p, v in pairs.items():
            print(f"axis {axis:5s} {p:22s} wins {v['wins']:>2} losses {v['losses']:>2} "
                  f"touched {v['touched']:>2}  distinguishable={v['distinguishable']}")
    ab = res['ablation_lenient_pad']
    print(f"ablation strict pad clearance vs lenient: variant(lenient) wins "
          f"{len(ab['variant_wins'])}, losses {len(ab['variant_losses'])} "
          f"on {ab['boards']} boards")
    for ch, bs in res['channels'].items():
        print(f"{ch:22s} {len(bs):>3}  {[b[:14] for b in bs[:8]]}")
    print('selectable boards (arm -> clean):')
    for r in res['per_board']:
        if r['channel'] == 'selectable':
            print(f"  {r['board'][:34]:34s} {r['clean_arms']}")
