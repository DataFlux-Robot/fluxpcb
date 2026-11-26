"""Domain derivations: the physics a domain kernel can certify from declared inputs.

Each function returns ``{'status': 'derived'|'unknown', 'measured': ..., 'units': ...,
'evidence': {...}}``.  A derivation is never `proven`: it is a checked
calculation over a declared model and declared inputs, with the model id and
an inputs hash recorded.  Missing inputs are `unknown` and name what is
missing — the same discipline as the Lean side.
"""
from __future__ import annotations

import hashlib
import json
import math

SIGMA_CU = 5.8e7


def _hash(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str)
                          .encode()).hexdigest()[:16]


def power_drop(current_a, length_mm, width_mm, t_mm=0.035, v_rail=None,
               copper=True):
    """Analytic rail drop: rho*L/(w*t) on a declared length and width."""
    missing = [k for k, v in (('current_a', current_a), ('length_mm', length_mm),
                              ('width_mm', width_mm)) if v in (None, 0)]
    if missing:
        return {'status': 'unknown', 'needed_information': missing}
    if copper:
        r = 1.0 / SIGMA_CU * (length_mm * 1e-3) / (width_mm * 1e-3 * t_mm * 1e-3)
    else:
        return {'status': 'unknown', 'needed_information': ['conductor']}
    drop = current_a * r
    return {'status': 'derived', 'measured': round(drop * 1e3, 3), 'units': 'mV',
            'drop_pct': round(100 * drop / v_rail, 3) if v_rail else None,
            'evidence': {'model': 'R = rho*L/(w*t)', 'model_version': '1',
                         'inputs': {'I': current_a, 'L_mm': length_mm,
                                    'w_mm': width_mm, 't_mm': t_mm},
                         'inputs_hash': _hash([current_a, length_mm, width_mm,
                                               t_mm]),
                         'result': round(drop, 6)}}


def thermal_tj(power_w, theta_ja=None, theta_jc=None, theta_cs=None,
               theta_sa=None, t_amb=25.0, sink=False):
    """Lumped junction temperature: theta_ja, or the theta_jc+cs+sa chain."""
    if power_w is None:
        return {'status': 'unknown', 'needed_information': ['power_w']}
    if sink:
        th = [theta_jc, theta_cs, theta_sa]
        if any(v is None for v in th):
            return {'status': 'unknown',
                    'needed_information': ['theta_jc/theta_cs/theta_sa']}
        total = sum(th)
    else:
        if theta_ja is None:
            return {'status': 'unknown', 'needed_information': ['theta_ja']}
        total = theta_ja
    tj = t_amb + power_w * total
    return {'status': 'derived', 'measured': round(tj, 1), 'units': 'degC',
            'evidence': {'model': 'Tj = Ta + P*theta', 'model_version': '1',
                         'inputs': {'P': power_w, 'theta': total, 'Ta': t_amb},
                         'inputs_hash': _hash([power_w, total, t_amb]),
                         'result': round(tj, 3)}}


def z0_microstrip(width_mm, h_mm, er, t_mm=0.035):
    """IPC-2141 microstrip impedance (the same formula as tools/ipc2141)."""
    if not width_mm or not h_mm or not er:
        return {'status': 'unknown',
                'needed_information': ['width_mm', 'h_mm', 'er']}
    w, h = width_mm, h_mm
    z0 = 87.0 / math.sqrt(er + 1.41) * math.log(5.98 * h / (0.8 * w + t_mm))
    return {'status': 'derived', 'measured': round(z0, 1), 'units': 'ohm',
            'evidence': {'model': 'IPC-2141 microstrip', 'model_version': '1',
                         'inputs': {'w': w, 'h': h, 'er': er, 't': t_mm},
                         'inputs_hash': _hash([w, h, er, t_mm]),
                         'result': round(z0, 3)}}
