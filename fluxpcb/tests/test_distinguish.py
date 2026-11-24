"""Distinguishability arithmetic on synthetic cells."""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from fluxpcb.meta.distinguish import (analyse, by_board, main_effects,  # noqa: E402
                                      noise, per_board)

AX = [('direct', 'batch'), ('gate', 'batch'), ('preroute', 'batch'),
      ('direct', 'morph'), ('gate', 'morph'), ('preroute', 'morph')]


def _cell(board, plan, loop, outcome, wall=10.0, arm=None):
    return {'board': board, 'set': 'd3a', 'arm': arm or f'{plan}+{loop}',
            'arm_axes': {'plan': plan, 'loop': loop}, 'outcome': outcome,
            'status': 'ok' if outcome in ('clean', 'fail') else outcome,
            'wall_seconds': wall, 'instrument_ok': True,
            'score': {'errors': 0 if outcome == 'clean' else 2,
                      'unconnected': 0}}


def _write(run, cells):
    d = os.path.join(run, 'cells')
    os.makedirs(d, exist_ok=True)
    for i, c in enumerate(cells):
        with open(os.path.join(d, f'{i:03d}.json'), 'w', encoding='utf-8') as f:
            json.dump(c, f)


def test_noise_is_the_flip_rate_of_the_same_arm_twice(tmp_path):
    cells = []
    for b, (x, y) in {'B1': ('clean', 'clean'), 'B2': ('fail', 'fail'),
                      'B3': ('clean', 'fail')}.items():
        cells.append(_cell(b, 'gate', 'batch', x, arm='gate+batch'))
        cells.append(_cell(b, 'gate', 'batch', y, arm='gate+batch~aa'))
    _write(str(tmp_path), cells)
    aa = noise(cells, by_board(cells))
    assert aa['available'] and aa['boards'] == 3
    assert aa['flips'] == ['B3'] and aa['flip_rate'] == round(1 / 3, 3)


def test_main_effect_counts_wins_and_losses_board_by_board(tmp_path):
    cells = []
    # on B1 the plan axis flips the outcome under every loop value
    for loop in ('batch', 'morph'):
        cells += [_cell('B1', 'direct', loop, 'fail'),
                  _cell('B1', 'gate', loop, 'clean'),
                  _cell('B1', 'preroute', loop, 'clean')]
    # B2: only under 'morph'
    cells += [_cell('B2', 'direct', 'batch', 'fail'),
              _cell('B2', 'gate', 'batch', 'fail'),
              _cell('B2', 'preroute', 'batch', 'fail'),
              _cell('B2', 'direct', 'morph', 'fail'),
              _cell('B2', 'gate', 'morph', 'clean'),
              _cell('B2', 'preroute', 'morph', 'clean')]
    _write(str(tmp_path), cells)
    bb = by_board(cells)
    axes = {c['arm']: c['arm_axes'] for c in cells}
    eff = main_effects(cells, bb, axes)
    # gate beats direct: twice on B1, once on B2, never the reverse
    assert eff['plan']['direct -> gate']['wins'] == 3
    assert eff['plan']['direct -> gate']['losses'] == 0
    assert eff['plan']['gate -> preroute'] == {
        'wins': 0, 'losses': 0, 'touched': 0, 'win_boards': [],
        'loss_boards': []}
    # loop: morph beats batch on B2 under 'gate' and 'preroute' only
    assert eff['loop']['batch -> morph']['wins'] == 2
    assert eff['loop']['batch -> morph']['losses'] == 0


def test_channels_split_boards_by_what_they_can_teach(tmp_path):
    cells = []
    for plan, loop in AX:
        cells.append(_cell('ALLCLEAN', plan, loop, 'clean'))
        cells.append(_cell('ALLFAIL', plan, loop, 'fail'))
        cells.append(_cell('SOME', plan, loop,
                           'clean' if plan == 'preroute' else 'fail'))
        cells.append(_cell('SLOW', plan, loop, 'budget'))
    _write(str(tmp_path), cells)
    rows = {r['board']: r for r in per_board(cells, by_board(cells), {}, {})}
    assert rows['ALLCLEAN']['channel'] == 'no_information'
    assert rows['ALLFAIL']['channel'] == 'needs_new_capability'
    assert rows['SOME']['channel'] == 'selectable'
    assert sorted(rows['SOME']['clean_arms']) == ['preroute+batch',
                                                   'preroute+morph']
    assert rows['SLOW']['channel'] == 'budget_blind'
    assert rows['SOME']['distinct_outcomes'] == 2


def test_analyse_reports_headroom_and_reads_effects_against_noise(tmp_path):
    cells = []
    # A/A duplicate never flips: noise floor 0
    for plan, loop in AX:
        cells.append(_cell('A', plan, loop, 'clean'))
        cells.append(_cell('B', plan, loop,
                           'clean' if plan == 'preroute' else 'fail'))
    cells += [_cell('A', 'gate', 'batch', 'clean', arm='gate+batch~aa'),
              _cell('B', 'gate', 'batch', 'fail', arm='gate+batch~aa')]
    _write(str(tmp_path), cells)
    res = analyse(str(tmp_path))
    assert res['noise_aa']['flips'] == []
    # preroute cleans both boards; every other arm one
    assert res['best_single_arm'] == 2 and res['oracle_any_arm'] == 2
    assert res['selector_headroom'] == 0
    assert res['pareto_front'] == ['preroute+batch', 'preroute+morph']
    gate = res['main_effects']['plan']['direct -> preroute']
    # direct -> preroute flips board B under both loop values; A is clean either way
    assert gate['wins'] == 2 and gate['losses'] == 0
    assert gate['distinguishable'] is True
    # reverse direction: the losses stay within the noise floor (0)
    assert res['channels']['no_information'] == ['A']
    assert res['channels']['selectable'] == ['B']


def test_an_effect_inside_the_noise_floor_is_not_called_an_effect(tmp_path):
    cells = []
    for plan, loop in AX:
        cells.append(_cell('B', plan, loop,
                           'clean' if plan == 'preroute' else 'fail'))
    # the duplicate flips once on its own
    cells.append(_cell('B', 'gate', 'batch', 'clean', arm='gate+batch~aa'))
    _write(str(tmp_path), cells)
    res = analyse(str(tmp_path))
    assert res['noise_aa']['flips'] == ['B']
    d = res['main_effects']['plan']['direct -> preroute']
    assert d['wins'] == 2 and d['distinguishable'] is True   # 2 wins > floor 1
    g = res['main_effects']['plan']['direct -> gate']
    assert g['wins'] == 0 and g['distinguishable'] is False


def test_instrument_bad_cells_are_counted_not_hidden(tmp_path):
    cells = [_cell('B', 'gate', 'batch', 'clean')]
    cells[0]['instrument_ok'] = False
    _write(str(tmp_path), cells)
    assert analyse(str(tmp_path))['instrument_bad_cells'] == 1
