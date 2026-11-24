"""Error-model induction over failure certificates."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from fluxpcb.meta.error_model import (conditions_of, counterfactual_of,  # noqa: E402
                                      from_memory_entries, gradient_check,
                                      induce, shortfall_band, to_memory_entries)
from fluxpcb.verifiers.certs import cert_entry  # noqa: E402


def _cert(outcome, short=None, margin=None, sealed=None, w_max=None,
          op='connect_unconnected', cls='unconnected', board='B1',
          budget=None, layers=2, density=0.3, via_sites=None):
    why = {}
    if short is not None:
        why['short_by_mm'] = short
        why['blocker'] = {'kind': 'track'}
    if margin is not None:
        why['margin_mm'] = margin
        why['blocker'] = why.get('blocker') or {'kind': 'pad'}
    if sealed is not None:
        why['sealed'] = sealed
    if w_max is not None:
        why['w_max_mm'] = w_max
    if budget is not None:
        why['budget'] = {'exhausted': budget}
    if via_sites is not None:
        why['via_sites'] = via_sites
    return cert_entry(op, {'net': 'N1'},
                      outcome,
                      why_not=why,
                      attempt={'class': cls, 'width_mm': 0.2},
                      requirement={'clearance_mm': 0.2},
                      board=board)


def test_bins_are_numerical_and_stable():
    c1 = _cert('verify_rejected', short=0.017)
    c2 = _cert('verify_rejected', short=0.017)
    assert conditions_of(c1) == conditions_of(c2)
    assert shortfall_band(0.017) == 'lt20um'
    assert shortfall_band(0.3) == 'gt200um'
    assert conditions_of(c1)[2] == 'lt20um'


def test_induce_clusters_and_accumulates_capability():
    day1 = [_cert('verify_rejected', short=0.017, board='B1'),
            _cert('verify_rejected', short=0.017, board='B2'),
            _cert('no_path', board='B3')]
    models = induce(day1, at='2026-01-01')
    shorted = [m for m in models if m.conditions[2] == 'lt20um'][0]
    assert shorted.support == 2
    assert shorted.counterfactual.startswith('corridor short by lt20um')
    # same conditions on another day keep the history and add a win — a
    # success carries the model it addressed
    win = _cert('ok', board='B4', op='maze_escape')
    win['attempt']['model_id'] = shorted.model_id()
    day2 = [win, _cert('verify_rejected', short=0.017, board='B5')]
    models2 = induce(day2, existing={m.model_id(): m for m in models},
                     at='2026-01-02')
    again = [m for m in models2 if m.conditions[2] == 'lt20um'][0]
    assert again.support == 3
    assert again.capability['maze_escape'] == {'trials': 1, 'wins': 1}
    assert not again.needs_new_capability      # one confirmed win
    # a model with two failed trials and no wins asks for a new capability
    lone = [m for m in models2 if m.conditions[0] == 'no_path'][0]
    assert lone.capability['connect_unconnected']['trials'] == 1
    day3 = [_cert('no_path', board='B6')]
    models3 = induce(day3, existing={m.model_id(): m for m in models2},
                     at='2026-01-03')
    lone3 = [m for m in models3 if m.conditions[0] == 'no_path'][0]
    assert lone3.zero_reach(2) and lone3.needs_new_capability


def test_memory_round_trip_keeps_history():
    models = induce([_cert('verify_rejected', short=0.04)], at='t')
    entries = to_memory_entries(models, 't')
    back = from_memory_entries(entries)
    m = back[[k for k in back][0]]
    assert m.support == 1 and m.capability


def test_counterfactual_for_a_sealed_anchor_is_quantitative():
    via = {'layers': [{'nearest_mm': None}, {'nearest_mm': 1.7}]}
    c = _cert('sealed', sealed=True, via_sites=via)
    cond = conditions_of(c)
    cf = counterfactual_of(cond, c['why_not'])
    assert 'sealed' in cf and '1.7 mm away' in cf


def test_gradient_check_retires_wrong_directions():
    recs = [{'model_id': 'm1', 'field': 'maze_max_expand',
             'prediction': 'up', 'before': 0.0, 'after': -0.3},
            {'model_id': 'm1', 'field': 'maze_max_expand',
             'prediction': 'up', 'before': 0.0, 'after': -0.2},
            {'model_id': 'm1', 'field': 'maze_max_expand',
             'prediction': 'up', 'before': 0.0, 'after': -0.1},
            {'model_id': 'm1', 'field': 'maze_max_expand',
             'prediction': 'up', 'before': 0.0, 'after': -0.4},
            {'model_id': 'm2', 'field': 'blind_widen',
             'prediction': 'down', 'before': 1.0, 'after': 0.0}]
    res = gradient_check(recs)
    assert res['m1:maze_max_expand']['rate'] == 0.0
    assert res['m1:maze_max_expand']['retire'] is True
    assert res['m2:blind_widen']['rate'] == 1.0
    assert res['m2:blind_widen']['retire'] is False
