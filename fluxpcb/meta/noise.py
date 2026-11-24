"""Measurement — deciding, with few expensive runs, whether a change is real.

The unit is the BOARD.  A board has its own pass probability p under a
given configuration; repeats only estimate that p, they never add boards.
Everything here follows three rules the literature backs (see
research/LITREVIEW_2026-11-25/C_measurement.md):

  1. no A/B before a noise model: "0 flips in 23" bounds the per-board
     flip probability at 12%, it does not show 0;
  2. the board is the unit: per-board clean rates over R repeats, an exact
     sign-flip permutation test over boards, and a margin;
  3. a three-tier held-out fixed from base-only data BEFORE any candidate
     is seen: 0<p<1 boards (two-sided), always-clean boards (one-sided loss
     guard), and fresh boards scored once for the final report.
"""
from __future__ import annotations

import itertools
import math
import random


# ---------------------------------------------------------------- board level
def clean_rate(outcomes: list) -> float:
    """Share of runs that cleaned the board (missing runs do not count)."""
    runs = [o for o in outcomes if o is not None]
    return sum(1 for o in runs if o == 'clean') / len(runs) if runs else 0.0


def is_stochastic(outcomes: list, graded: list = None) -> bool:
    """A board is stochastic if its binary outcome OR its graded error
    count varies across runs."""
    runs = [o for o in outcomes if o is not None]
    if len(set(map(str, runs))) > 1:
        return True
    g = [str(x) for x in (graded or []) if x is not None]
    return len(set(g)) > 1


def noise_model(per_board: dict, graded: dict = None) -> dict:
    """per_board: {board: [outcome per run]}; graded: {board: [errors per run]}.

    Returns the per-board pass rates, which boards are stochastic, how
    many runs each has, and the estimated flip exposure of a single A/A
    pair (the probability that one duplicate pair shows a flip).
    """
    graded = graded or {}
    rates, stoch, runs = {}, {}, {}
    for b, outs in per_board.items():
        valid = [o for o in outs if o is not None]
        runs[b] = len(valid)
        rates[b] = round(clean_rate(outs), 3)
        stoch[b] = is_stochastic(outs, graded.get(b))
    flip = sum(2 * rates[b] * (1 - rates[b]) for b in rates
               if 0 < rates[b] < 1) / max(1, len(rates))
    return {'boards': len(rates),
            'rates': rates,
            'stochastic': sorted(b for b in rates if stoch[b]),
            'runs': runs,
            'single_pair_flip_exposure': round(flip, 4),
            # a p=0.2 board escapes a single duplicate pair 68% of the time
            'detection_at_R': {R: round(1 - sum(
                rates[b] ** (2 * R) + (1 - rates[b]) ** (2 * R)
                for b in rates) / len(rates), 3) for R in (1, 3, 5)}}


def strata(rates: dict, lo: float = 0.0, hi: float = 1.0) -> dict:
    """Three tiers fixed from base-only data.

    D: boards with 0<p<1 — the only ones that can show a gain.
    G: boards the base always cleans — a loss guard only.
    X: boards the base never cleans — gains show here as p rising above 0.
    """
    D = sorted(b for b, p in rates.items() if lo < p < hi)
    G = sorted(b for b, p in rates.items() if p >= hi)
    X = sorted(b for b, p in rates.items() if p <= lo)
    return {'D': D, 'G': G, 'X': X, 'n': {'D': len(D), 'G': len(G), 'X': len(X)}}


# ---------------------------------------------------------------- tests
def paired_delta(base: dict, cand: dict) -> dict:
    """Per-board clean-rate difference and the mean over shared boards."""
    shared = sorted(set(base) & set(cand))
    d = {b: cand[b] - base[b] for b in shared}
    mean = sum(d.values()) / len(d) if d else 0.0
    return {'boards': shared, 'delta': {b: round(v, 3) for b, v in d.items()},
            'mean': round(mean, 4)}


def sign_flip_pvalue(base: dict, cand: dict, max_exact: int = 20,
                     samples: int = 20000, seed: int = 7) -> dict:
    """Exact (or Monte-Carlo) two-sided sign-flip permutation test.

    The null: swapping base and cand within a board is as likely as not.
    The statistic is the mean per-board difference.  Returns the p-value
    and the forward/backward flip counts.
    """
    shared = sorted(set(base) & set(cand))
    d = [cand[b] - base[b] for b in shared if cand[b] != base[b]]
    if not d:
        return {'p': 1.0, 'forward': 0, 'backward': 0, 'exact': True}
    observed = abs(sum(d))
    fwd = sum(1 for x in d if x > 0)
    bwd = sum(1 for x in d if x < 0)
    if len(d) <= max_exact:
        count = total = 0
        for signs in itertools.product((1, -1), repeat=len(d)):
            total += 1
            if abs(sum(s * x for s, x in zip(signs, d))) >= observed - 1e-12:
                count += 1
        return {'p': count / total, 'forward': fwd, 'backward': bwd,
                'exact': True}
    rng = random.Random(seed)
    count = 0
    for _ in range(samples):
        if abs(sum(x if rng.random() < 0.5 else -x for x in d)) >= observed - 1e-12:
            count += 1
    return {'p': (count + 1) / (samples + 1), 'forward': fwd,
            'backward': bwd, 'exact': False}


def mde(n_boards: int, between_sd: float, within_sd: float = None,
        rates: dict = None, repeats: int = 1, alpha: float = 0.05,
        power: float = 0.8) -> float:
    """Smallest mean clean-rate difference the design can detect.

    Either pass per-board rates (preferred: the within-board variance is
    then p(1-p)/R summed over boards) or a single within_sd.  Normal
    approximation; optimistic at small n.
    """
    z = _z(alpha / 2) + _z(1 - power)
    if rates:
        within = sum(p * (1 - p) for p in rates.values()) / len(rates) / repeats
        between = (sum((p - sum(rates.values()) / len(rates)) ** 2
                       for p in rates.values()) / max(1, len(rates) - 1))
        return round(z * math.sqrt((between + within) / n_boards), 3)
    return round(z * math.sqrt((between_sd ** 2 + 2 * (within_sd or 0) ** 2
                                / repeats) / n_boards), 3)


def _z(prob: float) -> float:
    """Upper-tail z for a two-sided level / power target.

    A small table with linear interpolation: exact enough for a
    sample-size rule and impossible to get wrong in the far tail.
    """
    table = ((0.5, 0.0), (0.3, 0.524), (0.2, 0.842), (0.1, 1.282),
             (0.05, 1.645), (0.025, 1.960), (0.01, 2.326), (0.005, 2.576),
             (0.001, 3.090))
    if prob >= table[0][0]:
        return 0.0
    if prob <= table[-1][0]:
        return table[-1][1]
    for (p0, z0), (p1, z1) in zip(table, table[1:]):
        if p1 <= prob <= p0:
            return z0 + (z1 - z0) * (p0 - prob) / (p0 - p1)
    return table[-1][1]


def zero_flip_bound(n_boards: int, conf: float = 0.95) -> float:
    """Upper bound on the per-board flip probability given 0 flips."""
    return round(1 - (1 - conf) ** (1.0 / max(1, n_boards)), 4)


# ---------------------------------------------------------------- the gate
def decide(base: dict, cand: dict, tiers: dict, alpha: float = 0.05,
           margin: float = 0.0, guard_boards: int = 0) -> dict:
    """Is the candidate an improvement on the boards used to choose?

    base/cand: {board: clean_rate}.  tiers from `strata()` on the BASE.
      * tier D (0<p<1): two-sided sign-flip test must reach alpha and the
        mean delta must exceed `margin`;
      * tier G (always clean): no board may drop below 1.0;
      * tier X (never clean): any board rising above 0 counts as forward.
    Anything less is 'undecided', never a pass.
    """
    reasons, checks = [], {}
    D = [b for b in tiers['D'] if b in base and b in cand]
    G = [b for b in tiers['G'] if b in base and b in cand]
    X = [b for b in tiers['X'] if b in base and b in cand]
    dD = {b: cand[b] - base[b] for b in D}
    dX = {b: cand[b] - base[b] for b in X}
    test = sign_flip_pvalue({b: base[b] for b in D + X},
                            {b: cand[b] for b in D + X})
    mean = (sum(dD.values()) + sum(dX.values())) / max(1, len(D) + len(X))
    lost = [b for b in G if cand[b] < 1.0]
    checks['gain'] = {'p': round(test['p'], 4),
                      'forward': test['forward'], 'backward': test['backward'],
                      'mean_delta': round(mean, 4),
                      'tier_D': len(D), 'tier_X': len(X)}
    if lost:
        checks['guard'] = {'pass': False, 'lost': lost}
        reasons.append(f'{len(lost)} always-clean board(s) regressed: {lost[:4]}')
    else:
        checks['guard'] = {'pass': True, 'lost': []}
    if test['p'] >= alpha:
        reasons.append(f"gain not significant (p={test['p']:.3f} >= {alpha})")
    if mean <= margin:
        reasons.append(f'mean delta {mean:+.3f} does not exceed margin {margin}')
    if test['backward'] > 0 and mean <= 0:
        reasons.append('net non-positive')
    return {'ok': not reasons, 'reasons': reasons, 'checks': checks}


def audit(selection_verdict: dict, fresh: dict) -> dict:
    """The fresh-tier check that decides what may be REPORTED.

    selection_verdict: output of `decide()`; fresh: {'base': {b: rate},
    'cand': {b: rate}} on boards never used to choose.  A gain that does
    not survive here is reported as 'not confirmed', however it looked on
    the selection set.
    """
    base, cand = fresh.get('base', {}), fresh.get('cand', {})
    shared = sorted(set(base) & set(cand))
    delta = {b: round(cand[b] - base[b], 3) for b in shared}
    mean = round(sum(delta.values()) / len(delta), 4) if delta else 0.0
    lost = [b for b in shared if cand[b] < base[b]]
    gained = [b for b in shared if cand[b] > base[b]]
    sel = (selection_verdict.get('checks') or {}).get('gain', {}).get('mean_delta')
    return {'boards': len(shared), 'fresh_delta': mean,
            'gained': gained, 'lost': lost,
            'selection_delta': sel,
            'inflation': (round(sel - mean, 4) if sel is not None else None),
            'confirmed': bool(selection_verdict.get('ok')) and mean > 0
            and not lost}
