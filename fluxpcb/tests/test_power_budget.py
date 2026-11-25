"""The power budget and its gate: arithmetic against hand calculation.

The reference skill's formulas are simple enough that a wrong constant is
visible: a pure-sleep deck has a closed-form life, a single pulse moves the
average by I*t/T, and the droop is a product.  The gate must block a release
only when a budget was declared and missed, and must never invent a battery.
"""
from __future__ import annotations

import textwrap

from fluxpcb.gates import run_gate
from fluxpcb.tools import power_budget as pb


def _spec(tmp_path, budget):
    import yaml
    body = {'project': {'name': 't'}, 'requirements': [],
            'power': {'input': 'USB-C 5V', 'total_w': 1.0, 'rails': ['3V3']},
            'interfaces': [], 'constraints': {'family': 'esp32s31-devboard'}}
    if budget is not None:
        body['power']['budget'] = budget
    (tmp_path / 'spec.yaml').write_text(yaml.safe_dump(body, allow_unicode=True),
                                       encoding='utf-8')
    return tmp_path


def test_pure_sleep_life_is_closed_form():
    r = pb.run({'battery': {'capacity_mah': 1000, 'usable_factor': 0.8,
                            'eol_voltage_v': 3.0},
                'sleep_ma': 2.0, 'v_min_op': 2.7})
    assert r['status'] == 'ok', r
    assert abs(r['i_avg_ma'] - 2.0) < 1e-9
    assert abs(r['life_days'] - 1000 * 0.8 / 2.0 / 24.0) < 0.05, r['life_days']


def test_one_pulse_moves_the_average_by_i_t_over_T():
    r = pb.run({'battery': {'capacity_mah': 1200, 'usable_factor': 0.8,
                            'eol_voltage_v': 2.4, 'r_battery_ohm': 0.3},
                'path_r_ohm': 0.15, 'sleep_ma': 2.0, 'period_s': 60,
                'duty': [{'name': 'tx', 'i_ma': 25, 't_s': 0.005, 'n': 1}],
                'v_min_op': 2.0})
    assert r['status'] == 'ok', r
    want = 2.0 + 25 * 0.005 / 60
    assert abs(r['i_avg_ma'] - want) < 1e-6
    assert abs(r['v_min_v'] - (2.4 - 0.025 * 0.45)) < 1e-4, r['v_min_v']


def test_bulk_cap_from_a_declared_load_step():
    r = pb.run({'battery': {'capacity_mah': 100, 'eol_voltage_v': 3.0},
                'sleep_ma': 1.0, 'v_min_op': 2.7,
                'load_step': {'i_a': 0.05, 'dt_s': 0.001, 'dv_v': 0.05}})
    assert r['status'] == 'ok', r
    assert abs(r['cap_uf'] - 1000.0) < 1.0        # 0.05*0.001/0.05 = 1 mF


def test_missing_inputs_are_named_not_defaulted():
    r = pb.run({'sleep_ma': 1.0, 'v_min_op': 2.7})
    assert r['status'] == 'incomplete'
    assert 'capacity_mah' in r['missing'] and 'eol_voltage_v' in r['missing']


def test_no_budget_is_blocked_with_the_missing_block(tmp_path):
    d = _spec(tmp_path, None)
    g = run_gate('power_budget', d, [])
    assert g.status.value == 'blocked', g.to_dict()
    assert any('power.budget' in s for s in g.needed_information)


def test_a_declared_budget_that_misses_its_target_fails(tmp_path):
    d = _spec(tmp_path, {
        'battery': {'capacity_mah': 1000, 'usable_factor': 0.8,
                    'eol_voltage_v': 3.0},
        'sleep_ma': 2.0, 'v_min_op': 2.7, 'life_target_days': 30})
    g = run_gate('power_budget', d, [])
    assert g.status.value == 'fail', g.to_dict()
    life = [f for f in g.findings if f.get('rule_id') == 'POWER.life']
    assert life and life[0]['severity'] == 'P1' and life[0]['status'] == 'estimate'
    assert life[0]['measured'] < life[0]['limit']


def test_a_met_budget_passes(tmp_path):
    d = _spec(tmp_path, {
        'battery': {'capacity_mah': 1000, 'usable_factor': 0.8,
                    'eol_voltage_v': 3.0},
        'sleep_ma': 2.0, 'v_min_op': 2.7, 'life_target_days': 10})
    g = run_gate('power_budget', d, [])
    assert g.status.value == 'pass', g.to_dict()
    assert g.details['life_days'] > 10
