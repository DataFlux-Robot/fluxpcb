"""Measurement layer: the board is the unit, noise is modelled first."""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from fluxpcb.meta.noise import (audit, clean_rate, decide,  # noqa: E402
                                is_stochastic, mde, noise_model,
                                paired_delta, sign_flip_pvalue,
                                strata, zero_flip_bound)


def test_zero_flips_bound_is_not_zero():
    # 23 boards, 0 flips: the per-board flip probability is bounded at
    # 12.2%, not shown to be 0
    assert zero_flip_bound(23) == 0.1221
    assert zero_flip_bound(1000) <= 0.003


def test_clean_rate_ignores_missing_runs():
    assert clean_rate(['clean', 'fail', 'clean', None]) == 2 / 3
    assert clean_rate([None, None]) == 0.0


def test_stochastic_on_binary_or_graded_outcome():
    assert is_stochastic(['clean', 'fail'])
    assert not is_stochastic(['clean', 'clean'])
    # same binary outcome but the error count moves: still stochastic
    assert is_stochastic(['fail', 'fail'], graded=[[9], [12]])
    assert not is_stochastic(['fail', 'fail'], graded=[[9], [9]])


def test_noise_model_detects_a_flaky_board_and_its_exposure():
    per = {f'B{i}': ['clean'] * 5 for i in range(22)}
    per['flaky'] = ['clean', 'fail', 'fail', 'fail', 'fail']      # p = 0.2
    m = noise_model(per)
    assert m['stochastic'] == ['flaky'] and m['rates']['flaky'] == 0.2
    # a single duplicate pair exposes a p=0.2 board with prob 0.32;
    # averaged over 23 boards -> 0.32/23
    assert m['single_pair_flip_exposure'] == round(0.32 / 23, 4)
    assert m['detection_at_R'][5] > m['detection_at_R'][1]


def test_strata_from_base_only():
    s = strata({'a': 0.0, 'b': 0.4, 'c': 1.0, 'd': 1.0})
    assert s['D'] == ['b'] and s['G'] == ['c', 'd'] and s['X'] == ['a']


def test_paired_delta_is_per_board():
    d = paired_delta({'a': 0.2, 'b': 1.0}, {'a': 0.6, 'b': 1.0, 'z': 0.0})
    assert d['delta'] == {'a': 0.4, 'b': 0.0} and d['mean'] == 0.2
    assert 'z' not in d['delta']          # not shared -> not compared


def test_sign_flip_exact_matches_hand_count():
    base = {'a': 0.0, 'b': 0.0, 'c': 0.0}
    cand = {'a': 1.0, 'b': 1.0, 'c': 0.0}
    r = sign_flip_pvalue(base, cand)
    # 2 forward of 2 discordant: two-sided exact p = 2/4 = 0.5
    assert r['exact'] and r['p'] == 0.5
    assert r['forward'] == 2 and r['backward'] == 0
    # 6 forward, 0 backward: p = 2/64 = 0.03125 -> significant
    base6 = {f'b{i}': 0.0 for i in range(6)}
    cand6 = {f'b{i}': 1.0 for i in range(6)}
    assert sign_flip_pvalue(base6, cand6)['p'] == 2 / 64
    assert sign_flip_pvalue(base6, base6)['p'] == 1.0


def test_sign_flip_monte_carlo_for_many_boards():
    base = {f'b{i}': 0.0 for i in range(40)}
    cand = {f'b{i}': (1.0 if i < 30 else 0.0) for i in range(40)}
    r = sign_flip_pvalue(base, cand)
    assert not r['exact'] and r['p'] < 0.001 and r['forward'] == 30


def test_mde_shrinks_with_repeats_but_has_a_floor():
    rates = {'a': 0.2, 'b': 0.5, 'c': 1.0, 'd': 0.0}
    r1 = mde(4, 0, rates=rates, repeats=1)
    r5 = mde(4, 0, rates=rates, repeats=5)
    r20 = mde(4, 0, rates=rates, repeats=20)
    assert r1 > r5 > r20
    # between-board spread is never removed by repeats
    assert r20 > 0
    assert mde(100, 0.3, within_sd=0.2, repeats=5) < mde(25, 0.3,
                                                        within_sd=0.2, repeats=5)


def _rates(d):
    return {b: float(v) for b, v in d.items()}


def test_decide_requires_significance_margin_and_guard():
    tiers = strata(_rates({**{f'D{i}': 0.2 for i in range(8)},
                           'G0': 1.0, 'X0': 0.0}))
    base = _rates({**{f'D{i}': 0.2 for i in range(8)}, 'G0': 1.0, 'X0': 0.0})
    # a real gain on the stochastic boards, guard intact
    good = _rates({**{f'D{i}': 1.0 for i in range(8)}, 'G0': 1.0, 'X0': 0.0})
    v = decide(base, good, tiers)
    assert v['ok'] and v['checks']['guard']['pass']
    assert v['checks']['gain']['forward'] == 8 and v['checks']['gain']['backward'] == 0
    # a gain on one board only is not significant with 8 discordant boards
    weak = _rates({**{f'D{i}': 0.2 for i in range(8)}, 'D0': 1.0,
                   'G0': 1.0, 'X0': 0.0})
    v2 = decide(base, weak, tiers)
    assert not v2['ok'] and any('not significant' in r for r in v2['reasons'])
    # the always-clean board regressed: refused regardless of the gain
    regressed = _rates({**{f'D{i}': 1.0 for i in range(8)}, 'G0': 0.6, 'X0': 0.0})
    v3 = decide(base, regressed, tiers)
    assert not v3['ok'] and 'always-clean' in ' '.join(v3['reasons'])
    # a gain that appears only on the never-clean tier still counts
    xgain = _rates({**{f'D{i}': 0.2 for i in range(8)}, 'G0': 1.0, 'X0': 1.0})
    v4 = decide(base, xgain, tiers)
    assert not v4['ok']          # 1 of 9 discordant: not significant


def test_audit_separates_selection_from_report():
    sel = {'ok': True, 'checks': {'gain': {'mean_delta': 0.10}}}
    conf = audit(sel, {'base': {'f1': 0.2, 'f2': 1.0},
                      'cand': {'f1': 0.6, 'f2': 1.0}})
    assert conf['confirmed'] and conf['fresh_delta'] == 0.2
    assert conf['inflation'] == -0.1
    lost = audit(sel, {'base': {'f1': 0.2, 'f2': 1.0},
                      'cand': {'f1': 0.6, 'f2': 0.4}})
    assert not lost['confirmed'] and lost['lost'] == ['f2']
    flat = audit(sel, {'base': {'f1': 0.5}, 'cand': {'f1': 0.5}})
    assert not flat['confirmed'] and flat['inflation'] == 0.10
