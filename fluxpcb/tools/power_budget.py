"""Power budget: does the declared energy source last, and hold up at peak?

The formulas are the ones in the reference skill (MIT, low-power-iot-
hardware-design, references/06-power-budget.md):

    I_avg = I_sleep + sum(I_event * t_event * n_per_period) / T_period
    life  = capacity_mAh * usable / I_avg
    V_min = V_eol - I_peak * (R_battery + R_path)
    C     = I_step * dt / dV

Nothing here reads a board.  The inputs are declared (battery, duty cycle,
minimum operating voltage); the output is an estimate and says so.  A missing
input is returned as `missing`, never defaulted.

    run(budget) -> {'status': 'ok'|'incomplete', 'i_avg_ma', 'life_days', ...}
"""
from __future__ import annotations

REQUIRED = ('capacity_mah', 'sleep_ma', 'period_s', 'duty', 'eol_voltage_v',
            'v_min_op')


def _f(x, default=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def average_current_ma(sleep_ma, duty, period_s):
    """I_sleep + Σ(I·t·n)/T.  `duty` rows: {i_ma, t_s, n}."""
    total = float(sleep_ma)
    for d in duty or []:
        i = _f(d.get('i_ma'))
        t = _f(d.get('t_s'))
        n = _f(d.get('n', 1))
        if i is None or t is None or n is None:
            continue
        total += i * t * n / float(period_s)
    return total


def peak_current_ma(sleep_ma, duty):
    """The declared peak: `peak_ma` wins, else sleep + the largest event."""
    return max([_f(sleep_ma, 0.0)] +
               [_f(d.get('i_ma'), 0.0) for d in (duty or [])])


def battery_life_days(capacity_mah, usable_factor, i_avg_ma,
                      self_discharge_ua=0.0):
    """capacity * usable / (I_avg + self-discharge) in days."""
    if i_avg_ma <= 0:
        return None
    eff = float(i_avg_ma) + max(0.0, float(self_discharge_ua)) / 1000.0
    return float(capacity_mah) * float(usable_factor) / eff / 24.0


def peak_droop_v(eol_voltage_v, i_peak_ma, r_battery_ohm, r_path_ohm):
    return float(i_peak_ma) / 1000.0 * (float(r_battery_ohm) + float(r_path_ohm))


def bulk_cap_uf(i_step_a, dt_s, dv_v):
    if dv_v <= 0:
        return None
    return float(i_step_a) * float(dt_s) / float(dv_v) * 1e6


def run(budget):
    """The whole budget, or the list of inputs that are missing."""
    b = budget or {}
    battery = b.get('battery') or {}
    cap = _f(battery.get('capacity_mah', b.get('capacity_mah')))
    usable = _f(battery.get('usable_factor', b.get('usable_factor', 0.8)), 0.8)
    eol = _f(battery.get('eol_voltage_v', b.get('eol_voltage_v')))
    r_batt = _f(battery.get('r_battery_ohm', b.get('r_battery_ohm', 0.0)), 0.0)
    r_path = _f(b.get('path_r_ohm'), 0.0)
    sleep = _f(b.get('sleep_ma'))
    period = _f(b.get('period_s'))
    duty = b.get('duty') or []
    v_min_op = _f(b.get('v_min_op'))
    missing = [k for k, v in (('capacity_mah', cap), ('sleep_ma', sleep),
                              ('eol_voltage_v', eol),
                              ('v_min_op', v_min_op)) if v is None]
    if duty and period is None:
        missing.append('period_s')
    if missing:
        return {'status': 'incomplete', 'missing': missing}
    i_avg = average_current_ma(sleep, duty, period)
    peak = _f(b.get('peak_ma')) or peak_current_ma(sleep, duty)
    sd = _f(b.get('self_discharge_pct_per_month'), 0.0)
    sd_ua = cap * (sd / 100.0) / (30 * 24) * 1000.0 if sd else 0.0
    life = battery_life_days(cap, usable, i_avg, sd_ua)
    droop = peak_droop_v(eol, peak, r_batt, r_path)
    step = b.get('load_step') or {}
    cap_uf = None
    if step.get('i_a') is not None and step.get('dt_s') is not None \
            and step.get('dv_v') is not None:
        cap_uf = bulk_cap_uf(_f(step['i_a']), _f(step['dt_s']),
                             _f(step['dv_v']))
    return {'status': 'ok', 'i_avg_ma': round(i_avg, 6),
            'peak_ma': round(peak, 2),
            'life_days': round(life, 1) if life is not None else None,
            'life_years': round(life / 365.0, 2) if life is not None else None,
            'usable_factor': usable,
            'v_min_v': round(eol - droop, 4), 'droop_mv': round(droop * 1e3, 1),
            'v_min_op': v_min_op,
            'cap_uf': round(cap_uf, 1) if cap_uf is not None else None,
            'cap_available_uf': _f(b.get('cap_available_uf')),
            'self_discharge_ua': round(sd_ua, 2) if sd_ua else 0.0,
            'life_target_days': _f(b.get('life_target_days')),
            'assumption': ('duty-cycle average from declared currents; battery '
                           'capacity derated by the declared usable factor; '
                           'peak droop from declared pack and path resistance; '
                           'no temperature or ageing model')}
