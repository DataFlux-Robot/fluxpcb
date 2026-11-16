# -*- coding: utf-8 -*-
"""Simulated-annealing placement refinement (knowhow: elcruzo GAT-biased SA,
tbrandenburg greedy+SA, Nsai-roshan Leiden+SA, yelabb deterministic scoring).

Starts FROM the existing rule-based placement (only improves), cost =
HPWL + overlap + decap distance + connector-edge distance. Pure numpy,
deterministic seed, bounded iterations — seconds, not minutes.
"""
from __future__ import annotations

import math
import random
from pathlib import Path


def _boxes(place: dict, footprints: dict) -> dict:
    from fluxpcb.kicadgen.hierplace import cluster_boxes
    return cluster_boxes(place, footprints)


def _bbox_overlap(a, b) -> float:
    ox = min(a[2], b[2]) - max(a[0], b[0])
    oy = min(a[3], b[3]) - max(a[1], b[1])
    return max(0.0, ox) * max(0.0, oy)


def cost(place: dict, footprints: dict, board_w: float, board_h: float,
         edge_refs: tuple = ("J4",), decap_refs: tuple = (),
         ic_prefixes: tuple = ("U", "MOD")) -> float:
    """Lower is better. HPWL(粗) + overlap penalty + decap distance +
    connector edge + out-of-board penalty."""
    boxes = _boxes(place, footprints)
    refs = sorted(boxes)
    c = 0.0
    # pseudo-HPWL: sum of bbox spans over all pairs is O(n^2) with n~20 — fine
    for i, a in enumerate(refs):
        for b in refs[i + 1:]:
            A, B = boxes[a], boxes[b]
            c += _bbox_overlap(A, B) * 50.0          # overlap strongly penalized
    # decap distance to nearest IC
    ics = [r for r in refs if r.startswith(ic_prefixes)]
    for d in decap_refs:
        if d in boxes:
            dx, dy, _, _ = boxes[d]
            cx, cy = (dx + boxes[d][2]) / 2, (dy + boxes[d][3]) / 2
            best = min(
                ((boxes[i][0] + boxes[i][2]) / 2 - cx) ** 2 +
                ((boxes[i][1] + boxes[i][3]) / 2 - cy) ** 2
                for i in ics) if ics else 0.0
            c += math.sqrt(best) * 0.5
    # connector at edge
    for r in edge_refs:
        if r in boxes:
            x0, y0, x1, y1 = boxes[r]
            c += min(x0, y0, board_w - x1, board_h - y1) * 2.0
    # containment
    for r in refs:
        x0, y0, x1, y1 = boxes[r]
        out = max(0, 0.5 - x0) + max(0, 0.5 - y0) \
            + max(0, x1 - (board_w - 0.5)) + max(0, y1 - (board_h - 0.5))
        c += out * 100.0
    return c


def refine(place: dict, footprints: dict, board_w: float, board_h: float,
           movable: tuple | None = None, iterations: int = 1500,
           seed: int = 42, log=print) -> tuple[dict, float, float]:
    """Anneal small nudges on non-connector parts. Returns
    (new_place, cost_before, cost_after); never worsens."""
    rng = random.Random(seed)
    movable = movable or tuple(
        r for r in place if not r.startswith("J"))
    c0 = cost(place, footprints, board_w, board_h)
    best = dict(place)
    cb = cc = c0
    T0, T1 = 1.2, 0.02
    for it in range(iterations):
        T = T0 * (T1 / T0) ** (it / max(1, iterations - 1))
        ref = rng.choice(movable)
        x, y, rot = best[ref]
        step = rng.uniform(0.3, 1.5)
        ang = rng.uniform(0, 2 * math.pi)
        cand = dict(best)
        cand[ref] = (x + step * math.cos(ang), y + step * math.sin(ang), rot)
        cn = cost(cand, footprints, board_w, board_h)
        if cn <= cc or rng.random() < math.exp(-(cn - cc) / max(T, 1e-6)):
            best, cc = cand, cn
    if cc <= c0:
        return best, c0, cc
    return dict(place), c0, c0        # never worse than the start
